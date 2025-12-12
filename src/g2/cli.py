from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import click
import psycopg
from psycopg import sql
import typer
from typer.core import TyperGroup
from requests import exceptions as req_exc
from rich.console import Console
from rich.table import Table

from g2.alphavantage.catalog import parse_daily_adjusted
from g2.alphavantage.client import AlphaVantageClient
from g2.ingest.indicators import ingest_indicators_for_symbols, INDICATOR_FUNCTIONS
from g2.config import load_settings
from g2.db import schema
from psycopg.types.json import Json
from g2.db import migrate
from g2.db.ingest import (
    insert_stock_ohlcv,
    upsert_stock,
    ensure_all_indicator_feature_definitions,
    ensure_feature_definitions,
    delete_feature_data_only,
    trim_feature_data,
    trim_stock_ohlcv,
    ensure_store_targets,
    drop_features,
    feature_ids_for_names,
)
from g2.ingest.universe import (
    fetch_listings,
    filter_listings,
    ingest_prices_for_symbols,
    load_listings_from_file,
)
from g2.utils.progress import ProgressReporter
from rich.live import Live
from g2.utils.db_load import get_available_connections, plan_workers
from g2.utils.adaptive import AdaptiveLimiter, ResourceAwareAdaptiveLimiter, chunked
from typing import Dict, Any
from g2.db import pool as db_pool


class SortedGroup(TyperGroup):
    def list_commands(self, ctx):  # pragma: no cover - cosmetic
        return sorted(super().list_commands(ctx))


app = typer.Typer(
    help="g2 CLI",
    no_args_is_help=False,
    add_completion=True,
    invoke_without_command=True,
    cls=SortedGroup,
)
SETTINGS = load_settings()


def emit(
    message: str,
    data: Optional[dict] = None,
    json_output: Optional[bool] = None,
    error: bool = False,
) -> None:
    """Emit either plain text or JSON."""
    if json_output is None:
        try:
            ctx = click.get_current_context(silent=True)
        except Exception:
            ctx = None
        if ctx and getattr(ctx, "obj", None) is not None:
            json_output = ctx.obj.get("json_output")
    json_output = bool(json_output)
    if json_output:
        payload = {"status": "error" if error else "ok", "message": message}
        if data:
            payload.update(data)
        typer.echo(json.dumps(payload))
    else:
        console = Console()
        style = "bold red" if error else "bold green"
        console.print(message, style=style)
        if data:
            for k, v in data.items():
                console.print(f"{k}: {v}", style="dim")


def emit_error(message: str, json_output: Optional[bool] = None, data: Optional[dict] = None) -> None:
    emit(message, data=data, json_output=json_output, error=True)
    raise typer.Exit(code=1)


def emit_json(payload: dict) -> None:
    """Lightweight JSON emitter to mirror emit() when only a payload is needed."""
    typer.echo(json.dumps(payload))


def _export_feature_functions(conn, names: Optional[List[str]] = None) -> list[dict]:
    where = ""
    params: List[str] = []
    if names:
        placeholders = ",".join(["%s"] * len(names))
        where = f"WHERE name IN ({placeholders})"
        params = list(names)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, version, status, description, language, function_body, inputs,
                   output_name, output_type, param_schema, defaults, dependencies,
                   checksum, tags, min_app_version, enabled, created_by
            FROM feature_functions
            {where}
            ORDER BY name, version;
            """.format(where=where),
            params or None,
        )
        rows = cur.fetchall()
    data = []
    for r in rows:
        if names and r[0] not in names:
            continue
        data.append(
            {
                "name": r[0],
                "version": r[1],
                "status": r[2],
                "description": r[3],
                "language": r[4],
                "function_body": r[5],
                "inputs": r[6],
                "output_name": r[7],
                "output_type": r[8],
                "param_schema": r[9],
                "defaults": r[10],
                "dependencies": r[11],
                "checksum": r[12],
                "tags": list(r[13]) if r[13] is not None else None,
                "min_app_version": r[14],
                "enabled": r[15],
                "created_by": r[16],
            }
        )
    return data


def _export_feature_definitions(conn, names: Optional[List[str]] = None) -> list[dict]:
    where = ""
    params: List[str] = []
    if names:
        placeholders = ",".join(["%s"] * len(names))
        where = f"WHERE name IN ({placeholders})"
        params = list(names)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, function_name, params, source_table, source_column,
                   store_table, store_column, store_type, active, version
            FROM feature_definitions
            {where}
            ORDER BY name;
            """.format(where=where),
            params or None,
        )
        rows = cur.fetchall()
    data = []
    for r in rows:
        if names and r[0] not in names:
            continue
        data.append(
            {
                "name": r[0],
                "function_name": r[1],
                "params": r[2],
                "source_table": r[3],
                "source_column": r[4],
                "store_table": r[5],
                "store_column": r[6],
                "store_type": r[7],
                "active": r[8],
                "version": r[9],
            }
        )
    return data


def export_functions_to_directory(
    conn,
    directory: Path,
    function_names: Optional[List[str]] = None
) -> int:
    """
    Export feature functions to individual JSON files in a directory.

    Args:
        conn: Database connection
        directory: Target directory for exports (created if doesn't exist)
        function_names: Optional list of specific function names to export

    Returns:
        Number of functions exported
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # Get functions from database
    functions = _export_feature_functions(conn, function_names)

    # Write each function to its own file
    for func in functions:
        # Filename format: functionname_vX.Y.json
        name = func["name"]
        version = func["version"]
        filename = f"{name}_v{version}.json"
        filepath = directory / filename

        filepath.write_text(json.dumps(func, indent=2))

    return len(functions)


def import_functions_from_directory(
    conn,
    directory: Path,
    function_names: Optional[List[str]] = None
) -> int:
    """
    Import feature functions from JSON files in a directory.

    Args:
        conn: Database connection
        directory: Source directory containing JSON files
        function_names: Optional list of specific function names to import

    Returns:
        Number of functions imported
    """
    directory = Path(directory)
    if not directory.exists():
        return 0

    # Find all JSON files in directory
    json_files = list(directory.glob("*.json"))

    imported_count = 0
    for json_file in json_files:
        try:
            payload = json.loads(json_file.read_text())

            # Skip if filtering by names and this isn't in the list
            if function_names and payload.get("name") not in function_names:
                continue

            # Upsert the function (idempotent)
            _upsert_feature_function(conn, payload)
            imported_count += 1

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # Skip invalid JSON files or files missing required fields
            continue

    return imported_count


def _parse_date_or_error(val: Optional[str], json_output: Optional[bool]):
    if val is None:
        return None
    try:
        return date.fromisoformat(val)
    except Exception:
        if json_output:
            emit_error("Invalid date format; use YYYY-MM-DD", json_output=True)
        else:
            raise typer.BadParameter("Invalid date format; use YYYY-MM-DD", param_hint="'--before' / '--after'")


def _auto_indicator_workers(compute_locally: bool, calls_per_minute: int) -> int:
    """
    Calculate optimal worker count based on computation mode and rate limits.

    For local computation: uses CPU count (capped at 8)
    For API mode: respects rate limits to avoid throttling

    Args:
        compute_locally: True if computing indicators locally, False if using API
        calls_per_minute: API rate limit in calls per minute

    Returns:
        Number of workers to use (minimum 2, maximum 10)
    """
    if compute_locally:
        # CPU-bound local computation - use available CPU cores
        cpu_count = os.cpu_count() or 2
        # Cap at 8 workers to avoid overwhelming the database
        return max(2, min(8, cpu_count))
    else:
        # API rate-limited - calculate based on calls per minute
        # Assume each worker makes ~15 calls/minute on average
        # Be conservative to avoid hitting rate limits
        api_workers = max(2, min(10, calls_per_minute // 30))
        return api_workers


def _db_available_connections(url: str) -> Optional[int]:
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SHOW max_connections;")
                max_conn = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM pg_stat_activity;")
                used = int(cur.fetchone()[0])
                return max_conn - used
    except Exception:
        return None


def _available_connections(url: str) -> Optional[int]:
    avail = get_available_connections(url)
    if isinstance(avail, tuple):
        return avail[0]
    return avail


def _plan_workers_for_stage(
    available: Optional[int],
    compute_locally: bool,
    calls_per_minute: int,
    requested_fetch: Optional[int],
    requested_writer: Optional[int],
    default_writer: int = 1,
    reserve: int = 2,
) -> tuple[int, int]:
    auto_fetch = _auto_indicator_workers(compute_locally, calls_per_minute)
    return plan_workers(
        available,
        requested_fetch,
        requested_writer,
        auto_fetch,
        requested_writer or default_writer,
        reserve=reserve,
    )


@app.callback()
def main(
    ctx: typer.Context,
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output in JSON (applies to subcommands unless overridden)"),
) -> None:
    """CLI entrypoint."""
    ctx.obj = ctx.obj or {}
    ctx.obj["json_output"] = json_output
    if ctx.invoked_subcommand is None:
        if json_output:
            commands = sorted(ctx.command.commands.keys())
            emit("No command specified", data={"commands": commands}, json_output=True, error=True)
            raise typer.Exit(code=1)
        else:
            typer.echo(ctx.get_help())
            raise typer.Exit(code=1)


def _db_url(override: Optional[str]) -> str:
    return override or SETTINGS.database_url or os.getenv("DATABASE_URL") or schema.test_db_url()


@app.command("prices-ingest")
def ingest_prices(
    symbol: str = typer.Option(..., help="Ticker symbol to ingest"),
    input: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Path to AlphaVantage JSON payload (optional)"),
    timeframe: str = typer.Option("auto", help="compact, full, or auto (API fetch)"),
    refresh_existing: bool = typer.Option(
        False,
        "--refresh-existing/--no-refresh-existing",
        "--update-existing/--no-update-existing",
        help="Refresh existing rows on conflict (upsert) when fetching from API",
    ),
    db_url: Optional[str] = typer.Option(None, help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Ingest daily adjusted prices from AlphaVantage. If --input is provided, load from file; otherwise fetch via API."""
    url = _db_url(db_url)
    if input:
        payload = json.loads(input.read_text())
        rows = parse_daily_adjusted(symbol=symbol, payload=payload)
        if not rows:
            emit("No rows parsed; nothing to ingest.", json_output=json_output, error=True)
            raise typer.Exit(code=1)
        try:
            with psycopg.connect(url) as conn:
                schema.create_stocks_table(conn)
                schema.create_stock_ohlcv_table(conn)
                stock_id = upsert_stock(conn, symbol)
                inserted = insert_stock_ohlcv(conn, stock_id, rows)
                emit(
                    f"Inserted {inserted} price rows for {symbol}",
                    data={"symbol": symbol, "inserted": inserted},
                    json_output=json_output,
                )
        except psycopg.OperationalError as exc:  # pragma: no cover - infra guard
            emit(f"Database connection failed: {exc}", json_output=json_output, error=True)
            raise typer.Exit(code=2)
    else:
        try:
            client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key)
        except ValueError as exc:
            emit(str(exc), json_output=json_output, error=True)
            raise typer.Exit(code=2)
        reporter = ProgressReporter(total=1, json_output=json_output, enabled=not json_output)
        reporter.mode = "api"
        live: Optional[Live] = None
        if not json_output:
            live = reporter.start_live()
            if live:
                live.__enter__()
        try:
            inserted = ingest_prices_for_symbols(
                db_url=url,
                client=client,
                symbols=[symbol],
                max_workers=1,
                writer_workers=1,
                timeframe=timeframe,
                update_existing=refresh_existing,
                progress=reporter,
            )
            if live:
                live.update(reporter._build_table())
            reporter.complete(live=live)
        except Exception as exc:
            if live:
                live.__exit__(type(exc), exc, exc.__traceback__)
            emit_error(f"Ingest failed: {exc}", json_output=json_output)
        finally:
            if live:
                live.__exit__(None, None, None)
        emit(
            f"Inserted {inserted} price rows for {symbol}",
            data={"symbol": symbol, "inserted": inserted},
            json_output=json_output,
        )


@app.command("universe-ingest")
def ingest_universe(
    exchange: str = typer.Option(..., help="Exchange filter (e.g., NASDAQ, NYSE)"),
    status: str = typer.Option("Active", help="Listing status filter"),
    limit: Optional[int] = typer.Option(None, help="Optional limit for symbols to ingest"),
    max_workers: Optional[int] = typer.Option(
        None, help="Parallel workers for price fetch/ingest (auto if not set)"
    ),
    writer_workers: Optional[int] = typer.Option(
        None, help="Parallel writers to DB (default 1 to reduce lock contention)"
    ),
    calls_per_minute: int = typer.Option(75, help="AlphaVantage rate limit (premium default)"),
    timeframe: str = typer.Option("auto", help="compact, full, or auto"),
    update_existing: bool = typer.Option(
        False,
        "--refresh-existing/--no-refresh-existing",
        "--update-existing/--no-update-existing",
        help="Refresh existing rows on conflict (upsert)",
    ),
    refresh: bool = typer.Option(False, help="Shortcut for full timeframe + refresh existing rows"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    listings_file: Optional[Path] = typer.Option(
        None, help="Optional path to listings CSV/JSON (bypass network fetch)"
    ),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show progress updates"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Fetch listing status and ingest prices for the filtered universe."""
    if refresh:
        timeframe = "full"
        update_existing = True

    try:
        client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key, calls_per_minute=calls_per_minute)
    except ValueError as exc:
        emit(str(exc), json_output=json_output, error=True)
        raise typer.Exit(code=2)

    try:
        if listings_file:
            listings = load_listings_from_file(listings_file)
        else:
            listings = fetch_listings(client)
    except req_exc.RequestException as exc:
        emit(f"Failed to fetch listings: {exc}", json_output=json_output, error=True)
        raise typer.Exit(code=2)
    filtered = filter_listings(listings, exchange=exchange, status=status)
    symbols = [row["symbol"] for row in filtered]
    if limit:
        symbols = symbols[:limit]
    if not symbols:
        emit("No symbols matched filters; nothing to ingest.", json_output=json_output, error=True)
        raise typer.Exit(code=1)

    url = _db_url(db_url)
    available = _available_connections(url)
    worker_count, writer_count = _plan_workers_for_stage(
        available,
        compute_locally=False,
        calls_per_minute=calls_per_minute,
        requested_fetch=max_workers,
        requested_writer=writer_workers,
        default_writer=writer_workers or 1,
    )

    # Do bulk filtering ONCE for all symbols before chunking
    # This is much faster than filtering each chunk separately
    symbols_before = len(symbols)
    skipped = 0
    if not update_existing:
        from g2.ingest.universe import _expected_market_date, filter_symbols_needing_update
        import psycopg
        with psycopg.connect(url) as conn:
            from g2.db import schema
            schema.create_stocks_table(conn)
            schema.create_stock_ohlcv_table(conn)
            target_date = _expected_market_date()
            symbols = filter_symbols_needing_update(conn, symbols, target_date)
            skipped = symbols_before - len(symbols)
            if skipped > 0 and not json_output:
                emit(f"Skipped {skipped} up-to-date symbols, processing {len(symbols)} symbols", json_output=False)

    # Create reporter with initial skipped count already set
    # Reset start time to exclude bulk filtering duration from rate calculation
    reporter = ProgressReporter(total=symbols_before, json_output=json_output, enabled=progress)
    if skipped > 0:
        reporter.done = skipped
        reporter.successes = skipped
        # Reset timer so bulk filtering time doesn't skew the rate
        reporter._start = time.monotonic()
    reporter.workers = worker_count
    reporter.mode = "api"

    live: Optional[Live] = None
    if progress and not json_output:
        live = reporter.start_live()
        if live:
            live.__enter__()
    try:
        inserted = 0
        for sym_chunk in chunked(symbols, 50):
            inserted += ingest_prices_for_symbols(
                db_url=url,
                client=client,
                symbols=sym_chunk,
                max_workers=worker_count,
                writer_workers=writer_count,
                timeframe=timeframe,
                update_existing=update_existing,
                progress=reporter,
            )
        if live:
            live.update(reporter._build_table())
        reporter.complete(live=live)
    except Exception as exc:
        if live:
            live.__exit__(type(exc), exc, exc.__traceback__)
        emit_error(f"Ingest failed: {exc}", json_output=json_output)
    finally:
        if live:
            live.__exit__(None, None, None)
    emit(
        f"Ingested price rows: {inserted} across {len(symbols)} symbols",
        data={
            "symbols": symbols,
            "inserted": inserted,
            "fetch_workers": worker_count,
            "writer_workers": writer_count,
        },
        json_output=json_output,
    )


@app.command("db-health")
def db_health(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Report basic DB health: connections, chunk intervals, BRIN indexes, table presence."""
    url = _db_url(db_url)
    health: Dict[str, Any] = {}
    avail = get_available_connections(url)
    if isinstance(avail, tuple):
        health["available_connections"], health["max_connections"], health["used_connections"] = avail
    else:
        health["available_connections"] = avail
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                tables = ["stock_ohlcv", "computed_features"]
                table_status = {}
                for t in tables:
                    cur.execute("SELECT to_regclass(%s);", (f"public.{t}",))
                    table_status[t] = cur.fetchone()[0] is not None
                health["tables"] = table_status

                chunk_map = {}
                try:
                    cur.execute(
                        """
                        SELECT h.hypertable_name, d.interval_length
                        FROM timescaledb_information.hypertables h
                        LEFT JOIN timescaledb_information.dimensions d
                          ON h.hypertable_name = d.hypertable_name
                        WHERE h.hypertable_name = ANY(%s) AND (d.interval_length IS NOT NULL OR d.column_name = 'date');
                        """,
                        (tables,),
                    )
                    chunk_map = {name: interval for name, interval in cur.fetchall()}
                except Exception:
                    chunk_map = {}
                health["chunk_intervals"] = chunk_map

                cur.execute(
                    """
                    SELECT tablename, indexdef
                    FROM pg_indexes
                    WHERE tablename = ANY(%s);
                    """,
                    (tables,),
                )
                brin = {}
                for table, idxdef in cur.fetchall():
                    brin.setdefault(table, False)
                    if "BRIN" in idxdef.upper():
                        brin[table] = True
                health["brin_indexes"] = brin
    except Exception as exc:
        emit_error(f"DB health failed: {exc}", json_output=json_output)
        return

    emit("DB health", data=health, json_output=json_output)


@app.command("db-init")
def db_init(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Initialize database schema from sql/schema.sql.
    Creates all tables, hypertables, and indexes. Safe to run multiple times (idempotent).
    """
    import subprocess
    import sys

    url = _db_url(db_url)

    # Find the schema.sql file relative to the package
    try:
        import g2
        package_dir = Path(g2.__file__).parent.parent.parent
        schema_path = package_dir / "sql" / "schema.sql"

        if not schema_path.exists():
            emit_error(f"Schema file not found at {schema_path}", json_output=json_output)
            return
    except Exception as exc:
        emit_error(f"Failed to locate schema file: {exc}", json_output=json_output)
        return

    try:
        # Read the schema file content
        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        # Parse the database URL to get connection parameters
        from urllib.parse import urlparse
        parsed = urlparse(url)

        # Build psql command (use stdin instead of -f to avoid snap confinement issues)
        env = os.environ.copy()
        if parsed.password:
            env['PGPASSWORD'] = parsed.password

        cmd = [
            'psql',
            '-h', parsed.hostname or 'localhost',
            '-p', str(parsed.port or 5432),
            '-U', parsed.username or 'postgres',
            '-d', parsed.path.lstrip('/') if parsed.path else 'postgres'
        ]

        if not json_output:
            emit("Initializing database schema...")

        # Pipe schema SQL via stdin (works with snap-confined psql)
        result = subprocess.run(
            cmd,
            input=schema_sql,
            env=env,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            emit_error(
                f"Database initialization failed: {result.stderr}",
                json_output=json_output,
                data={"stderr": result.stderr, "stdout": result.stdout}
            )
            return

        emit(
            "Database initialized successfully",
            data={"schema_file": str(schema_path)},
            json_output=json_output
        )

    except Exception as exc:
        emit_error(f"Initialization failed: {exc}", json_output=json_output)


@app.command("db-tune")
def db_tune(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    chunk_days: int = typer.Option(30, help="Chunk interval in days to set for time-series tables"),
    compress_after_days: int = typer.Option(
        60, help="Add compression policy for chunks older than this many days (set to 0 to skip)"
    ),
    show_chunk_ranges: bool = typer.Option(False, "--show-chunk-ranges", help="Display current chunk date ranges"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Apply Timescale tuning: set chunk intervals and optional compression policies.
    Safe to re-run; ignores missing tables.
    """
    from g2.utils.timescale import get_chunk_date_range

    url = _db_url(db_url)
    tables = ["stock_ohlcv", "computed_features"]
    applied = {"chunk_interval": [], "compression": []}
    status = {}
    chunk_ranges = {}

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                for t in tables:
                    cur.execute("SELECT to_regclass(%s);", (f"public.{t}",))
                    exists = cur.fetchone()[0]
                    table_result = {"chunk": "skipped", "compression": "skipped"}
                    if not exists:
                        status[t] = table_result
                        continue

                    # Get chunk date range for this table
                    if show_chunk_ranges or True:  # Always collect for reporting
                        min_date, max_date = get_chunk_date_range(conn, t)
                        if min_date and max_date:
                            chunk_ranges[t] = {
                                "min_date": min_date.isoformat(),
                                "max_date": max_date.isoformat()
                            }
                        else:
                            chunk_ranges[t] = {"min_date": None, "max_date": None}

                    try:
                        cur.execute(
                            "SELECT set_chunk_time_interval(%s, %s::interval);",
                            (t, f"{chunk_days} days"),
                        )
                        applied["chunk_interval"].append(t)
                        table_result["chunk"] = "ok"
                    except Exception as exc:
                        table_result["chunk"] = f"error: {exc}"
                    if compress_after_days and compress_after_days > 0:
                        try:
                            cur.execute(
                                """
                                SELECT 1
                                FROM timescaledb_information.compression_settings
                                WHERE hypertable_name = %s;
                                """,
                                (t,),
                            )
                            compression_exists = cur.fetchone() is not None
                            if not compression_exists:
                                if t == "stock_ohlcv":
                                    cur.execute(
                                        sql.SQL(
                                            "ALTER TABLE {} SET (timescaledb.compress, timescaledb.compress_segmentby = 'data_id', timescaledb.compress_orderby = 'date');"
                                        ).format(sql.Identifier(t))
                                    )
                                elif t == "computed_features":
                                    cur.execute(
                                        sql.SQL(
                                            "ALTER TABLE {} SET (timescaledb.compress, timescaledb.compress_segmentby = 'data_id,feature_id', timescaledb.compress_orderby = 'date');"
                                        ).format(sql.Identifier(t))
                                    )
                                else:
                                    cur.execute(
                                        sql.SQL(
                                            "ALTER TABLE {} SET (timescaledb.compress, timescaledb.compress_segmentby = 'data_id', timescaledb.compress_orderby = 'date');"
                                        ).format(sql.Identifier(t))
                                    )
                            cur.execute(
                                "SELECT add_compression_policy(%s, %s::interval, if_not_exists => true);",
                                (t, f"{compress_after_days} days"),
                            )
                            applied["compression"].append(t)
                            table_result["compression"] = "ok"
                        except Exception as exc:
                            table_result["compression"] = f"error: {exc}"
                    status[t] = table_result
    except Exception as exc:
        emit_error(f"DB tune failed: {exc}", json_output=json_output)
        return

    result_data = {
        "chunk_interval": applied["chunk_interval"],
        "compression": applied["compression"],
        "table_status": status,
        "chunk_ranges": chunk_ranges
    }

    emit(
        "DB tuning applied",
        data=result_data,
        json_output=json_output,
    )


@app.command("features-seed")
def seed_features(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Create metadata tables and seed feature_definitions for all known indicators.
    """
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_stocks_table(conn)
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)
            feature_map = ensure_all_indicator_feature_definitions(conn)
        emit(
            "Seeded indicator feature definitions",
            data={"features": feature_map},
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"Seeding failed: {exc}", json_output=json_output)


def _normalize_feature_definition(payload: dict) -> dict:
    """
    Ensure feature definition has defaults and rejects legacy source table.
    """
    d = dict(payload)
    if d.get("source_table") is None:
        d["source_table"] = "stock_ohlcv"
    if d.get("source_table") == "stock_prices":
        raise ValueError("source_table 'stock_prices' is deprecated; use 'stock_ohlcv'")
    d.setdefault("source_column", "close")
    d.setdefault("store_type", "double precision")
    d.setdefault("active", True)
    return d


@app.command("features-migrate-source")
def migrate_feature_definitions_source(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Update feature_definitions.source_table from legacy 'stock_prices' to 'stock_ohlcv'.
    Safe to run multiple times; no effect when already migrated.
    """
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            updated = migrate.migrate_feature_definitions_source_table(conn)
        emit(
            "Migrated feature_definitions source_table",
            data={"updated_rows": updated},
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"Migration failed: {exc}", json_output=json_output)


@app.command("db-migrate-stock-prices")
def db_migrate_stock_prices(
    drop_old: bool = typer.Option(False, "--drop-old", help="Drop legacy stock_prices after copy"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Copy legacy stock_prices data into stock_ohlcv and optionally drop old table.
    Idempotent: skips if stock_prices is absent; re-runs won't duplicate rows.
    """
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            copied, dropped = migrate.migrate_stock_prices_to_ohlcv(conn, drop_old=drop_old)
        emit(
            "Migrated stock_prices to stock_ohlcv",
            data={"copied_rows": copied, "dropped": bool(dropped), "drop_old": drop_old},
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"DB migration failed: {exc}", json_output=json_output)


@app.command("features-fx-register")
def register_function(
    definition: str = typer.Option(..., "--definition", help="JSON string for a feature function definition"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Register a feature function from a JSON payload.

    Required keys: name, version, language, function_body.

    Example:
      --definition '{
        "name": "volume_zscore",
        "version": "1.0.0",
        "language": "python_expr",
        "function_body": "def compute(df, window=20):\\n  import pandas as pd\\n  return ((df['volume'] - df['volume'].rolling(window).mean()) / df['volume'].rolling(window).std()).fillna(0)",
        "description": "Volume z-score over rolling window",
        "tags": ["volume", "indicator"],
        "param_schema": {"type": "object", "properties": {"window": {"type": "integer", "minimum": 1}}},
        "defaults": {"window": 20}
      }'
    """
    try:
        payload = json.loads(definition)
        if not isinstance(payload, dict):
            raise ValueError("definition must be a JSON object")
    except Exception as exc:
        emit_error(f"Invalid JSON: {exc}", json_output=json_output)
        return

    required = ["name", "version", "language", "function_body"]
    missing = [k for k in required if k not in payload]
    if missing:
        emit_error(f"Missing required keys: {', '.join(missing)}", json_output=json_output)
        return

    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_functions_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feature_functions
                    (name, version, status, description, language, function_body, inputs, output_name, output_type,
                     param_schema, defaults, dependencies, checksum, tags, min_app_version, enabled, created_by)
                    VALUES (%(name)s, %(version)s, %(status)s, %(description)s, %(language)s, %(function_body)s,
                            %(inputs)s, %(output_name)s, %(output_type)s, %(param_schema)s, %(defaults)s,
                            %(dependencies)s, %(checksum)s, %(tags)s, %(min_app_version)s, %(enabled)s, %(created_by)s)
                    ON CONFLICT (name, version) DO UPDATE SET
                        status = EXCLUDED.status,
                        description = EXCLUDED.description,
                        language = EXCLUDED.language,
                        function_body = EXCLUDED.function_body,
                        inputs = EXCLUDED.inputs,
                        output_name = EXCLUDED.output_name,
                        output_type = EXCLUDED.output_type,
                        param_schema = EXCLUDED.param_schema,
                        defaults = EXCLUDED.defaults,
                        dependencies = EXCLUDED.dependencies,
                        checksum = EXCLUDED.checksum,
                        tags = EXCLUDED.tags,
                        min_app_version = EXCLUDED.min_app_version,
                        enabled = EXCLUDED.enabled,
                        created_by = EXCLUDED.created_by,
                        updated_at = NOW()
                    RETURNING id;
                    """,
                    {
                        "name": payload.get("name"),
                        "version": payload.get("version"),
                        "status": payload.get("status", "active"),
                        "description": payload.get("description"),
                        "language": payload.get("language"),
                        "function_body": payload.get("function_body"),
                        "inputs": Json(payload.get("inputs")) if payload.get("inputs") is not None else None,
                        "output_name": payload.get("output_name", "value"),
                        "output_type": payload.get("output_type", "double precision"),
                        "param_schema": Json(payload.get("param_schema")) if payload.get("param_schema") is not None else None,
                        "defaults": Json(payload.get("defaults")) if payload.get("defaults") is not None else None,
                        "dependencies": Json(payload.get("dependencies")) if payload.get("dependencies") is not None else None,
                        "checksum": payload.get("checksum"),
                        "tags": payload.get("tags"),
                        "min_app_version": payload.get("min_app_version"),
                        "enabled": payload.get("enabled", True),
                        "created_by": payload.get("created_by", "cli"),
                    },
                )
                func_id = cur.fetchone()[0]
        emit(
            f"Registered function '{payload['name']}' v{payload['version']}",
            data={"id": func_id},
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"Register function failed: {exc}", json_output=json_output)


@app.command("features-fx-list")
def list_functions(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
    feature: Optional[str] = typer.Option(None, "--feature", help="Optional function name to filter"),
    show_body: bool = typer.Option(False, "--show-body/--no-show-body", help="Include function_body in output"),
) -> None:
    """List registered feature functions."""
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_functions_table(conn)
            with conn.cursor() as cur:
                select_cols = "name, version, status, language, enabled, description, tags, updated_at"
                if show_body:
                    select_cols += ", function_body"
                params: Dict[str, object] = {}
                where_clause = ""
                if feature:
                    where_clause = "WHERE name = %(feature)s"
                    params["feature"] = feature
                cur.execute(
                    f"""
                    SELECT {select_cols}
                    FROM feature_functions
                    {where_clause}
                    ORDER BY name, version;
                    """,
                    params or None,
                )
                rows = cur.fetchall()

        data = []
        for r in rows:
            entry = {
                "name": r[0],
                "version": r[1],
                "status": r[2],
                "language": r[3],
                "enabled": r[4],
                "description": r[5],
                "tags": list(r[6]) if r[6] is not None else None,
                "updated_at": r[7].isoformat() if len(r) > 7 and r[7] else None,
            }
            if show_body and len(r) > 8:
                entry["function_body"] = r[8]
            data.append(entry)

        if json_output:
            emit("Feature functions", data={"functions": data}, json_output=True)
            return

        console = Console()
        if not data:
            console.print("[yellow]No feature functions found.[/yellow]")
            return

        if show_body:
            for d in data:
                header = f"[bold]{d['name']}[/bold] v{d['version']} ({d['status']})"
                header += f" [{'ENABLED' if d['enabled'] else 'DISABLED'}]"
                header += f" [{d['language']}]"
                console.print(header)
                if d.get("tags"):
                    console.print(f"tags: {', '.join(d['tags'])}", style="blue")
                if d.get("updated_at"):
                    console.print(f"updated: {d['updated_at']}", style="dim")
                if d.get("description"):
                    console.print(d["description"])
                body = d.get("function_body") or ""
                console.print(body, style="cyan")
                console.print()
        else:
            table = Table(title="Feature Functions", header_style="bold cyan")
            table.add_column("Name", style="white")
            table.add_column("Version", style="magenta")
            table.add_column("Status", style="green")
            table.add_column("Enabled", style="yellow")
            table.add_column("Language", style="cyan")
            table.add_column("Tags", style="blue")
            table.add_column("Updated", style="dim")
            for d in data:
                table.add_row(
                    d["name"] or "",
                    d["version"] or "",
                    d["status"] or "",
                    str(d["enabled"]),
                    d["language"] or "",
                    ",".join(d["tags"]) if d.get("tags") else "",
                    d["updated_at"] or "",
                )
            console.print(table)
    except Exception as exc:
        emit_error(f"List functions failed: {exc}", json_output=json_output)


@app.command("features-register")
def register_feature(
    definition: str = typer.Option(..., "--definition", help="JSON string for a single feature definition"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Register a single feature definition from a JSON payload.

    Example:
      --definition '{
        "name": "my_feature",
        "function_name": "my_fx",
        "params": {"window": 30},
        "source_table": "stock_ohlcv",
        "source_column": "close",
        "store_table": "computed_features",
        "store_column": "value",
        "store_type": "double precision",
        "active": true
      }'
    """
    try:
        payload = json.loads(definition)
        if not isinstance(payload, dict):
            raise ValueError("definition must be a JSON object")
    except Exception as exc:
        emit_error(f"Invalid JSON: {exc}", json_output=json_output)
        return

    required = ["name", "function_name", "store_table", "store_column"]
    missing = [k for k in required if k not in payload]
    if missing:
        emit_error(f"Missing required keys: {', '.join(missing)}", json_output=json_output)
        return

    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)
            normalized = _normalize_feature_definition(payload)
            ids = ensure_feature_definitions(conn, [normalized])
            ensure_store_targets(conn, [normalized])
        emit(
            f"Registered feature '{payload['name']}'",
            data={"ids": ids},
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"Register failed: {exc}", json_output=json_output)


@app.command("features-export")
def features_export(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory to write feature files (default: feature-functions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    functions: Optional[str] = typer.Option(None, "--functions", help="Comma-separated list of function names to export"),
) -> None:
    """
    Export feature_functions to individual JSON files (one per function).

    By default, exports all functions to the 'feature-functions/' directory.
    Each function is saved as <name>_v<version>.json.
    """
    target_dir = Path(dir) if dir else Path("feature-functions")
    fx_filter = [s.strip() for s in functions.split(",")] if functions else None
    url = _db_url(db_url)

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_functions_table(conn)
            exported_count = export_functions_to_directory(conn, target_dir, fx_filter)

        emit(f"Exported {exported_count} function(s) to {target_dir}")
    except Exception as exc:
        emit_error(f"Export failed: {exc}")


def _upsert_feature_function(conn: psycopg.Connection, payload: dict) -> None:
    required = ["name", "version", "language", "function_body"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"Missing required keys for feature_function: {', '.join(missing)}")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions
            (name, version, status, description, language, function_body, inputs, output_name, output_type,
             param_schema, defaults, dependencies, checksum, tags, min_app_version, enabled, created_by)
            VALUES (%(name)s, %(version)s, %(status)s, %(description)s, %(language)s, %(function_body)s,
                    %(inputs)s, %(output_name)s, %(output_type)s, %(param_schema)s, %(defaults)s,
                    %(dependencies)s, %(checksum)s, %(tags)s, %(min_app_version)s, %(enabled)s, %(created_by)s)
            ON CONFLICT (name, version) DO UPDATE SET
                status = EXCLUDED.status,
                description = EXCLUDED.description,
                language = EXCLUDED.language,
                function_body = EXCLUDED.function_body,
                inputs = EXCLUDED.inputs,
                output_name = EXCLUDED.output_name,
                output_type = EXCLUDED.output_type,
                param_schema = EXCLUDED.param_schema,
                defaults = EXCLUDED.defaults,
                dependencies = EXCLUDED.dependencies,
                checksum = EXCLUDED.checksum,
                tags = EXCLUDED.tags,
                min_app_version = EXCLUDED.min_app_version,
                enabled = EXCLUDED.enabled,
                created_by = EXCLUDED.created_by,
                updated_at = NOW();
            """,
            {
                "name": payload.get("name"),
                "version": payload.get("version"),
                "status": payload.get("status", "active"),
                "description": payload.get("description"),
                "language": payload.get("language"),
                "function_body": payload.get("function_body"),
                "inputs": Json(payload.get("inputs")) if payload.get("inputs") is not None else None,
                "output_name": payload.get("output_name", "value"),
                "output_type": payload.get("output_type", "double precision"),
                "param_schema": Json(payload.get("param_schema")) if payload.get("param_schema") is not None else None,
                "defaults": Json(payload.get("defaults")) if payload.get("defaults") is not None else None,
                "dependencies": Json(payload.get("dependencies")) if payload.get("dependencies") is not None else None,
                "checksum": payload.get("checksum"),
                "tags": payload.get("tags"),
                "min_app_version": payload.get("min_app_version"),
                "enabled": payload.get("enabled", True),
                "created_by": payload.get("created_by", "cli"),
            },
        )


@app.command("features-import")
def features_import(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory containing feature JSON files (default: feature-functions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    functions: Optional[str] = typer.Option(None, "--functions", help="Comma-separated list of function names to import"),
) -> None:
    """
    Import feature_functions from individual JSON files.

    By default, imports all JSON files from the 'feature-functions/' directory.
    Idempotent: re-running will upsert by (name, version).
    """
    src_dir = Path(dir) if dir else Path("feature-functions")
    fx_filter = [s.strip() for s in functions.split(",")] if functions else None
    url = _db_url(db_url)

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_functions_table(conn)
            imported_count = import_functions_from_directory(conn, src_dir, fx_filter)

        if imported_count == 0:
            emit(f"No functions found in {src_dir}")
        else:
            emit(f"Imported {imported_count} function(s) from {src_dir}")
    except Exception as exc:
        emit_error(f"Import failed: {exc}")


@app.command("features-trim")
def trim_features(
    feature: str = typer.Option(..., "--feature", help="Comma-separated feature names to trim"),
    before: Optional[str] = typer.Option(None, help="Drop rows before this date (YYYY-MM-DD)"),
    after: Optional[str] = typer.Option(None, help="Drop rows after this date (YYYY-MM-DD)"),
    trim_prices: bool = typer.Option(True, "--trim-prices/--no-trim-prices", help="Also trim stock_ohlcv for date window"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Trim computed_features for given feature names.
    Use --before for left-trim, --after for right-trim.
    """
    if not before and not after:
        if json_output:
            emit_error("Specify --before and/or --after", json_output=True)
        else:
            raise typer.BadParameter("Missing option '--before' or '--after'", param_hint="'--before' / '--after'")
    before_dt = _parse_date_or_error(before, json_output)
    after_dt = _parse_date_or_error(after, json_output)

    names = [n.strip() for n in feature.split(",") if n.strip()]
    if not names:
        emit_error("No feature names provided", json_output=json_output)
        return

    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)
            deleted = trim_feature_data(conn, names, before=before_dt, after=after_dt)
            prices_deleted = 0
            if trim_prices:
                schema.create_stock_ohlcv_table(conn)
                prices_deleted = trim_stock_ohlcv(conn, before=before_dt, after=after_dt)
        emit(
            f"Trimmed features {', '.join(names)}",
            data={
                "deleted_features": deleted,
                "deleted_prices": prices_deleted,
                "features": names,
                "before": before,
                "after": after,
            },
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"Trim failed: {exc}", json_output=json_output)


@app.command("prices-trim")
def trim_prices(
    before: Optional[str] = typer.Option(None, help="Drop price rows before this date (YYYY-MM-DD)"),
    after: Optional[str] = typer.Option(None, help="Drop price rows after this date (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols to trim (optional)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Trim stock_ohlcv by date (optionally limited to symbols)."""
    if not before and not after:
        if json_output:
            emit_error("Specify --before and/or --after", json_output=True)
        else:
            raise typer.BadParameter("Missing option '--before' or '--after'", param_hint="'--before' / '--after'")
    before_dt = _parse_date_or_error(before, json_output)
    after_dt = _parse_date_or_error(after, json_output)
    sym_list = None
    if symbols:
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not sym_list:
            sym_list = None
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_stocks_table(conn)
            schema.create_stock_ohlcv_table(conn)
            deleted = trim_stock_ohlcv(conn, before=before_dt, after=after_dt, symbols=sym_list)
        emit(
            "Trimmed stock_ohlcv",
            data={
                "deleted_prices": deleted,
                "before": before,
                "after": after,
                "symbols": sym_list if sym_list else "All",
            },
            json_output=json_output,
        )
    except Exception as exc:
        emit_error(f"Trim prices failed: {exc}", json_output=json_output)


@app.command("features-drop")
def drop_features_cmd(
    feature: Optional[str] = typer.Option(None, "--feature", help="Comma-separated feature names to drop"),
    all_features: bool = typer.Option(False, "--all", help="Drop all features (use with caution!)"),
    data_only: bool = typer.Option(False, "--data-only", help="Delete data rows only; keep feature definitions/schema"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Drop feature definitions and their data.

    WARNING: This deletes rows from computed_features and any custom store tables defined for the feature.
    Use --data-only to remove data rows without dropping definitions/schema.

    Examples:
        # Drop specific features
        g2 features-drop --feature indicator_rsi_14,indicator_macd

        # Drop all feature data but keep definitions
        g2 features-drop --all --data-only

        # Drop all features completely (DANGEROUS!)
        g2 features-drop --all
    """
    url = _db_url(db_url)

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)

            # Determine which features to drop
            if all_features:
                # Get all feature names from database
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM feature_definitions ORDER BY name")
                    names = [row[0] for row in cur.fetchall()]

                if not names:
                    emit("No features found to drop", json_output=json_output)
                    return

                # Confirm for safety
                if not json_output:
                    action = "delete data for" if data_only else "completely drop"
                    emit(f"WARNING: About to {action} ALL {len(names)} features!")
                    emit(f"Features: {', '.join(names[:5])}{' ...' if len(names) > 5 else ''}")

                    # Interactive confirmation
                    confirmation = input(f"\nType 'yes' to confirm: ").strip().lower()
                    if confirmation != 'yes':
                        emit("Operation cancelled.")
                        return

            elif feature:
                names = [n.strip() for n in feature.split(",") if n.strip()]
                if not names:
                    emit_error("No feature names provided", json_output=json_output)
                    return
            else:
                emit_error("Must specify either --feature or --all", json_output=json_output)
                return

            # Execute the drop with progress indicator
            if data_only:
                # Delete data only (fast batch operation)
                if not json_output and len(names) > 1:
                    emit(f"Deleting data for {len(names)} features...")
                deleted = delete_feature_data_only(conn, names)
                emit(
                    f"Deleted data for {len(names)} feature(s)",
                    data={
                        "deleted_rows": deleted,
                        "definitions_kept": True,
                        "features": names,
                        "count": len(names)
                    },
                    json_output=json_output,
                )
            else:
                # Drop features in batches with progress
                # Batch size balances performance vs progress visibility
                batch_size = 10
                total_deleted = 0

                if not json_output and len(names) > 1:
                    emit(f"Dropping {len(names)} features in batches of {batch_size}...")

                for i in range(0, len(names), batch_size):
                    batch = names[i:i + batch_size]
                    if not json_output and len(names) > batch_size:
                        emit(f"[{i+1}-{min(i+batch_size, len(names))}/{len(names)}] Dropping batch...")

                    deleted = drop_features(conn, batch)
                    total_deleted += deleted

                emit(
                    f"Dropped {len(names)} feature(s)",
                    data={
                        "deleted": total_deleted,
                        "features": names,
                        "count": len(names)
                    },
                    json_output=json_output,
                )
    except Exception as exc:
        emit_error(f"Drop features failed: {exc}", json_output=json_output)


@app.command("features-list")
def features_list(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """List feature definitions."""
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, function_name, source_table, source_column, store_table, store_column, active, created_at
                    FROM feature_definitions
                    ORDER BY name;
                    """
                )
                rows = cur.fetchall()

                data = []
                for fid, name, fn, source_table, source_column, store_table, store_column, active, created_at in rows:
                    data.append(
                        {
                            "name": name,
                            "function": fn,
                            "source_table": source_table,
                            "source_column": source_column,
                            "store_table": store_table,
                            "store_column": store_column,
                            "active": active,
                            "created_at": created_at.isoformat() if created_at else None,
                        }
                    )

        if json_output:
            emit("Features", data={"features": data}, json_output=True)
        else:
            console = Console()
            if not data:
                console.print("[yellow]No features found.[/yellow]")
                return
            table = Table(title="Features", header_style="bold cyan")
            table.add_column("Name", style="white")
            table.add_column("Function", style="magenta")
            table.add_column("Source", style="cyan")
            table.add_column("Source Col", style="cyan")
            table.add_column("Store", style="green")
            table.add_column("Column", style="blue")
            table.add_column("Active", style="yellow")
            table.add_column("Created", style="dim")
            for d in data:
                table.add_row(
                    d["name"] or "",
                    d["function"] or "",
                    d.get("source_table") or "",
                    d.get("source_column") or "",
                    d["store_table"] or "",
                    d["store_column"] or "",
                    str(d["active"]),
                    d["created_at"] or "",
                )
            console.print(table)
    except Exception as exc:
        emit_error(f"List failed: {exc}", json_output=json_output)


@app.command("features-show")
def features_show(
    feature: str = typer.Option(..., "--feature", help="Feature name"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Show a single feature definition."""
    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, function_name, params, source_table, source_column,
                           store_table, store_column, store_type, active, version, created_at
                    FROM feature_definitions WHERE name = %s;
                    """,
                    (feature,),
                )
                row = cur.fetchone()
        if not row:
            emit_error(f"Feature '{feature}' not found", json_output=json_output)
            return
        data = {
            "name": row[0],
            "function": row[1],
            "params": row[2],
            "source_table": row[3],
            "source_column": row[4],
            "store_table": row[5],
            "store_column": row[6],
            "store_type": row[7],
            "active": row[8],
            "version": row[9],
            "created_at": row[10].isoformat() if row[10] else None,
        }
        emit(f"Feature {feature}", data=data, json_output=json_output)
    except Exception as exc:
        emit_error(f"Show failed: {exc}", json_output=json_output)


@app.command("features-compute")
def features_compute(
    symbols: Optional[str] = typer.Option(None, help="Comma-separated list of stock symbols (e.g., AAPL,MSFT)"),
    features: Optional[str] = typer.Option(None, "--features", help="Comma list of feature names to compute"),
    all_features: bool = typer.Option(False, "--all-features", help="Compute all active features"),
    function_names: Optional[str] = typer.Option(None, "--function-names", help="Comma list of function types (indicator,derivative,etc)"),
    incremental: bool = typer.Option(True, "--incremental/--full", help="Incremental (only new dates) or full refresh"),
    update_existing: bool = typer.Option(False, "--update-existing", help="Update existing rows on conflict"),
    max_workers: Optional[int] = typer.Option(None, help="Max parallel workers (auto if not set)"),
    feature_batch_size: int = typer.Option(2000, "--batch-size", help="DB insert batch size for computed_features"),
    profile: bool = typer.Option(False, "--profile/--no-profile", help="Include per-symbol timing in output"),
    sync_commit: bool = typer.Option(False, "--sync-commit/--no-sync-commit", help="Use synchronous_commit for inserts (default off for speed)"),
    writer_workers: int = typer.Option(2, "--writer-workers", help="Number of writer threads for pipelined inserts"),
    parallel_functions: bool = typer.Option(False, "--parallel-functions/--no-parallel-functions", help="Process function groups in parallel (experimental)"),
    max_parallel_functions: Optional[int] = typer.Option(None, "--max-parallel-functions", help="Max parallel function groups (defaults to cpu_count - 2)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show progress updates"),
) -> None:
    """
    Compute features using the generic dispatcher.

    This command uses the new dispatcher pattern and supports ALL feature types
    (indicators, derivatives, fundamentals, etc.), not just indicators.

    Features must already be defined in feature_definitions table.

    Examples:
        # Compute all indicators for AAPL
        g2 features-compute --symbols AAPL --function-names indicator

        # Compute specific features for multiple stocks
        g2 features-compute --symbols AAPL,MSFT --features indicator_rsi_14,derivative_rsi_14_slope_5

        # Full refresh of all features for all stocks
        g2 features-compute --all-features --full
    """
    from g2.features.dispatcher import compute_features

    url = _db_url(db_url)

    # Parse feature names
    feature_name_list = None
    if features:
        feature_name_list = [s.strip() for s in features.split(",") if s.strip()]

    # Parse function names
    function_name_list = None
    if function_names:
        function_name_list = [s.strip() for s in function_names.split(",") if s.strip()]

    # Parse symbols
    symbol_list = None
    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)

            # If all_features, get all active feature names
            if all_features and not feature_name_list:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM feature_definitions WHERE active = TRUE ORDER BY name;"
                    )
                    feature_name_list = [r[0] for r in cur.fetchall()]

            # If no symbols specified, get all stocks
            if not symbol_list:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol FROM stocks ORDER BY symbol;")
                    symbol_list = [r[0] for r in cur.fetchall()]

            if not symbol_list:
                emit_error("No stocks found in database", json_output=json_output)
                return

            # Calculate worker budget based on available connections
            # Be conservative: reserve connections for main operations and other processes
            avail_tuple = get_available_connections(url)
            available = avail_tuple[0] if isinstance(avail_tuple, tuple) else None

            # If user specified max_workers, respect it as absolute limit
            # Otherwise, let ResourceAwareAdaptiveLimiter calculate optimal based on resources
            if max_workers is not None:
                # User explicitly specified max_workers
                max_w = max(1, max_workers)
                user_specified_limit = True
            else:
                # No user specification - use a high default to let resource limiter decide
                # Calculate reasonable upper bound based on DB connections
                budget = max(1, (available or 100) - 5) if available else 50
                max_w = min(budget, 50)  # Cap at 50 as reasonable upper bound
                user_specified_limit = False

            if not json_output and progress:
                limit_type = "user-specified" if user_specified_limit else "auto (resource-based)"
                emit(f"Available connections: {available or 'unknown'}, Max workers: {max_w} ({limit_type})")

            # Initialize connection pool to reuse prepared statements across symbols
            # Pool sizing: Each worker needs 1 main connection + writer_workers writer threads
            # With dynamic scaling, writer_workers can increase, so size pool generously
            # Use max possible writer_workers (8) for pool sizing calculation
            pool_needed = db_pool.get_pool() is None
            if pool_needed:
                # Conservative pool size that allows for dynamic writer_workers scaling
                max_possible_writers = 8  # Reasonable upper bound
                min_pool = max(2, writer_workers)
                buffer = 5  # Increased buffer for safety
                # Calculate pool size assuming max_workers and max_possible_writers
                max_pool = max(
                    max_w * (1 + max_possible_writers) + buffer,
                    min_pool + buffer
                )
                db_pool.init_pool(url, min_size=min_pool, max_size=max_pool, prepare_statements=True)

            # Adaptive worker scaling with resource awareness
            # Start conservatively and let the limiter scale up based on actual resource availability
            # ResourceAwareAdaptiveLimiter will:
            # - Calculate optimal workers based on CPU, memory, and DB connections
            # - Scale up when resources are available
            # - Scale down when resources are constrained
            # - Respect user's max_workers as absolute limit (if specified)
            start_workers = 2  # Always start conservatively

            # Use resource-aware limiter for dynamic scaling based on system resources
            # This will periodically check CPU, memory, and DB connections
            # and adjust max_workers, writer_workers, and batch_size accordingly

            # Calculate hard limit on total threads to prevent system hang
            # Allow up to 2x CPU count in total threads (workers + writer threads)
            # This matches the previous behavior but now with emergency brake protection
            import multiprocessing
            cpu_count = multiprocessing.cpu_count()
            max_total_threads = max(4, int(cpu_count * 2))  # At least 4, but scale with CPUs

            limiter = ResourceAwareAdaptiveLimiter(
                start_workers=start_workers,
                max_workers=max_w,  # Either user-specified limit or calculated upper bound
                available_db_connections=available,
                writer_workers=writer_workers,
                user_max_writer_workers=None,  # Allow auto-scaling of writer workers
                batch_size=feature_batch_size,
                user_max_batch_size=None,  # Allow auto-scaling of batch_size
                check_interval_seconds=30.0,   # Check resources every 30 seconds
                emit_func=emit if progress and not json_output else None,
                max_total_threads=max_total_threads,  # Hard limit to prevent thread explosion
                min_memory_threshold_gb=2.0,  # Emergency brake if memory drops below 2GB
                enable_emergency_brake=True,  # Enable automatic emergency scaling down
            )

            total_inserted = 0
            errors = []
            profiles: List[Dict[str, Any]] = []
            timings_totals: Dict[str, float] = {}

            # Set up progress reporting
            reporter = ProgressReporter(total=len(symbol_list), json_output=json_output, enabled=progress)
            reporter.mode = "dispatcher"
            reporter.workers = start_workers
            reporter.max_workers = max_w
            reporter.writer_workers = writer_workers
            reporter.batch_size = feature_batch_size
            live: Optional[Live] = None
            if progress and not json_output:
                live = reporter.start_live()
                if live:
                    live.__enter__()

            def process_stock(symbol: str) -> Dict[str, Any]:
                """Process a single stock in a worker thread with retry logic."""
                import time

                max_retries = 3
                base_delay = 0.5  # 500ms

                for attempt in range(max_retries):
                    try:
                        start_time = time.monotonic()
                        with db_pool.get_connection() as worker_conn:
                            worker_conn.autocommit = True
                            # Note: synchronous_commit is handled by compute_features() -> insert_computed_features()
                            # No need to set it here

                            # Get data_id
                            with worker_conn.cursor() as cur:
                                cur.execute("SELECT id FROM stocks WHERE symbol = %s", (symbol,))
                                row = cur.fetchone()

                            if not row:
                                return {
                                    "symbol": symbol,
                                    "error": True,
                                    "reason": "Stock not found",
                                    "inserted": 0,
                                }

                            data_id = row[0]

                            # Get current dynamically-scaled values from limiter
                            current_writer_workers = limiter.get_writer_workers()
                            current_batch_size = limiter.get_batch_size()

                            # Compute features via dispatcher
                            result = compute_features(
                                worker_conn,
                                data_id=data_id,
                                function_names=function_name_list,
                                feature_names=feature_name_list,
                                incremental=incremental,
                                full_refresh=not incremental,
                                update_existing=update_existing,
                                feature_batch_size=current_batch_size,
                                writer_workers=current_writer_workers,
                                profile=profile,
                                sync_commit=sync_commit,
                                parallel_functions=parallel_functions,
                                max_parallel_functions=max_parallel_functions,
                            )

                            inserted = result.get('summary', {}).get('total_inserted', 0)
                            has_errors = result.get('summary', {}).get('total_errors', 0) > 0
                            timing = result.get('summary', {}).get('timing') if profile else None
                            duration = time.monotonic() - start_time

                            if profile:
                                if timing:
                                    for k, v in timing.items():
                                        timings_totals[k] = timings_totals.get(k, 0.0) + float(v)
                                profiles.append(
                                    {
                                        "symbol": symbol,
                                        "inserted": inserted,
                                        "duration_sec": round(duration, 3),
                                        "timing": timing,
                                    }
                                )

                            return {
                                "symbol": symbol,
                                "error": False,
                                "inserted": inserted,
                                "has_feature_errors": has_errors,
                                "feature_error_count": result.get('summary', {}).get('total_errors', 0),
                                "duration_sec": duration,
                                "timing": timing,
                            }

                    except psycopg.OperationalError as exc:
                        # Connection/database errors - retry with exponential backoff
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)  # Exponential backoff
                            time.sleep(delay)
                            continue
                        else:
                            # Final attempt failed
                            return {
                                "symbol": symbol,
                                "error": True,
                                "reason": f"Connection failed after {max_retries} attempts: {exc}",
                                "inserted": 0,
                            }

                    except Exception as exc:
                        # Other errors - don't retry
                        return {
                            "symbol": symbol,
                            "error": True,
                            "reason": str(exc),
                            "inserted": 0,
                        }

                # Shouldn't reach here, but just in case
                return {
                    "symbol": symbol,
                    "error": True,
                    "reason": "Unknown error",
                    "inserted": 0,
                }

            try:
                # Process stocks in batches with adaptive worker scaling
                for batch_symbols in chunked(symbol_list, 50):
                    current_workers = limiter.value()
                    current_max_workers = limiter.max_workers
                    current_writer_workers = limiter.get_writer_workers()
                    current_batch_size = limiter.get_batch_size()
                    reporter.workers = current_workers
                    reporter.max_workers = current_max_workers
                    reporter.writer_workers = current_writer_workers
                    reporter.batch_size = current_batch_size

                    prev_resource_errors = reporter.resource_errors

                    with ThreadPoolExecutor(max_workers=current_workers) as executor:
                        futures = {executor.submit(process_stock, sym): sym for sym in batch_symbols}

                        for future in as_completed(futures):
                            result = future.result()
                            symbol = result["symbol"]

                            if result["error"]:
                                errors.append({"symbol": symbol, "error": result.get("reason", "Unknown error")})
                                reporter.step_done(
                                    label=symbol,
                                    error=True,
                                    meta={"reason": result.get("reason", "Unknown error")}
                                )
                            else:
                                total_inserted += result["inserted"]
                                if result.get("has_feature_errors"):
                                    errors.append({
                                        "symbol": symbol,
                                        "errors": result.get("feature_error_count", 0)
                                    })

                                reporter.step_done(
                                    label=symbol,
                                    error=False,
                                    meta={"inserted": result["inserted"], "duration_sec": result.get("duration_sec"), "timing": result.get("timing")}
                                )

                    # Adjust workers based on RESOURCE errors only
                    resource_err_delta = reporter.resource_errors - prev_resource_errors
                    limiter.record_batch(errors=resource_err_delta)

                reporter.complete(live=live)

            finally:
                if live:
                    live.__exit__(None, None, None)

            # Output summary
            if json_output:
                output = {
                    "success": True,
                    "total_inserted": total_inserted,
                    "stocks_processed": len(symbol_list),
                    "errors": errors,
                    "batch_size": feature_batch_size,
                }
                if profile:
                    output["profiles"] = profiles
                    if timings_totals:
                        output["timing"] = {k: round(v, 6) for k, v in timings_totals.items()}
                emit_json(output)
            else:
                # Summary with colors
                console = Console()
                console.print()
                console.print(f"[bold green]✓[/bold green] Total: [cyan]{total_inserted:,}[/cyan] rows inserted across [cyan]{len(symbol_list)}[/cyan] stocks (batch_size={feature_batch_size})")

                if profile and profiles:
                    slowest = sorted(profiles, key=lambda p: p.get("duration_sec", 0), reverse=True)[:5]
                    console.print("\n[bold]Profile (top 5 by duration):[/bold]")
                    for p in slowest:
                        console.print(f"  [yellow]{p['symbol']}[/yellow]: {p.get('duration_sec', 0):.3f}s, inserted={p.get('inserted', 0):,}")

                if errors:
                    # Count complete failures vs partial failures
                    complete_failures = sum(1 for e in errors if "error" in e and e["error"])
                    partial_failures = len(errors) - complete_failures

                    if complete_failures > 0:
                        console.print(f"\n[bold red]✗[/bold red] Complete failures: [red]{complete_failures}[/red] stocks failed to process")

                    if partial_failures > 0:
                        console.print(f"[bold yellow]⚠[/bold yellow]  Feature errors: [yellow]{partial_failures}[/yellow] stocks had some features fail (but data was inserted)")

                    if partial_failures > 0 and complete_failures == 0:
                        console.print(f"[dim]   (These stocks processed successfully but some individual features had errors)[/dim]")
                else:
                    console.print(f"[bold green]✓[/bold green] No errors!")

    except Exception as exc:
        emit_error(f"Computation failed: {exc}", json_output=json_output)
    finally:
        # Only close pool if we initialized it (don't close pools managed by caller)
        if pool_needed:
            db_pool.close_pool()


@app.command("data-update")
def update_all(
    exchange: Optional[str] = typer.Option(None, help="Exchange filter (e.g., NASDAQ, NYSE). If omitted, infer from stocks table."),
    status: str = typer.Option("Active", help="Listing status filter"),
    indicators: str = typer.Option("rsi,macd,bbands,adx,stoch,sma20,sma50,sma200,ema12,ema26,psar", help="Comma list of indicators"),
    timeframe: str = typer.Option("auto", help="compact, full, or auto"),
    feature_batch_size: int = typer.Option(200, help="DB insert batch size for computed_features"),
    refresh_existing: bool = typer.Option(
        False,
        "--refresh-existing/--no-refresh-existing",
        "--update-existing/--no-update-existing",
        help="Refresh existing rows on conflict (upsert)",
    ),
    refresh: bool = typer.Option(False, help="Shortcut for full timeframe + refresh existing rows"),
    compute_locally: bool = typer.Option(True, "--local/--api", help="Compute indicators locally from prices"),
    limit: Optional[int] = typer.Option(None, help="Optional limit for symbols to ingest"),
    max_workers: Optional[int] = typer.Option(None, help="Parallel workers for fetch (auto if not set)"),
    writer_workers: Optional[int] = typer.Option(None, help="Parallel writers to DB"),
    calls_per_minute: int = typer.Option(75, help="AlphaVantage rate limit (premium default)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    listings_file: Optional[Path] = typer.Option(None, help="Optional path to listings CSV/JSON (bypass network fetch)"),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show progress updates"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Update all data: prices first, then indicators for the selected universe.
    """
    url = _db_url(db_url)
    if refresh:
        timeframe = "full"
        refresh_existing = True

    indicator_list = [s.strip().lower() for s in indicators.split(",") if s.strip()]
    unknown = [s for s in indicator_list if s not in INDICATOR_FUNCTIONS]
    if unknown:
        emit(f"Unknown indicators: {', '.join(unknown)}", json_output=json_output, error=True)
        raise typer.Exit(code=1)

    # Resolve universe
    symbols: List[str] = []
    client: Optional[AlphaVantageClient] = None
    try:
        if listings_file:
            listings = load_listings_from_file(listings_file)
            filtered = filter_listings(listings, exchange=exchange, status=status)
            symbols = [row["symbol"] for row in filtered]
        else:
            # If exchange not provided, try to infer from existing stocks
            if exchange is None:
                try:
                    with psycopg.connect(url) as conn:
                        conn.autocommit = True
                        schema.create_stocks_table(conn)
                        with conn.cursor() as cur:
                            cur.execute("SELECT DISTINCT symbol FROM stocks;")
                            symbols = [r[0] for r in cur.fetchall()]
                except Exception:
                    symbols = []
            if not symbols:
                try:
                    client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key, calls_per_minute=calls_per_minute)
                except ValueError as exc:
                    emit(str(exc), json_output=json_output, error=True)
                    raise typer.Exit(code=2)
                listings = fetch_listings(client)
                filtered = filter_listings(listings, exchange=exchange, status=status)
                symbols = [row["symbol"] for row in filtered]
    except req_exc.RequestException as exc:
        emit(f"Failed to fetch listings: {exc}", json_output=json_output, error=True)
        raise typer.Exit(code=2)
    if limit:
        symbols = symbols[:limit]
    if not symbols:
        emit("No symbols matched filters; nothing to ingest.", json_output=json_output, error=True)
        raise typer.Exit(code=1)

    available = _available_connections(url)
    price_fetch, price_writer = _plan_workers_for_stage(
        available,
        compute_locally=False,
        calls_per_minute=calls_per_minute,
        requested_fetch=max_workers,
        requested_writer=writer_workers,
        default_writer=writer_workers or 1,
    )
    feature_fetch, feature_writer = _plan_workers_for_stage(
        available,
        compute_locally=compute_locally,
        calls_per_minute=calls_per_minute,
        requested_fetch=max_workers,
        requested_writer=writer_workers,
        default_writer=writer_workers or 1,
    )

    # Save original symbols list for indicator filtering
    all_symbols = symbols.copy()

    # Bulk filter symbols that don't need price updates (skip API calls for up-to-date symbols)
    price_symbols = symbols
    price_skipped = 0
    if not refresh_existing:
        from g2.ingest.universe import _expected_market_date, filter_symbols_needing_update
        with psycopg.connect(url) as conn:
            schema.create_stocks_table(conn)
            schema.create_stock_ohlcv_table(conn)
            target_date = _expected_market_date()
            price_symbols = filter_symbols_needing_update(conn, symbols, target_date)
            price_skipped = len(symbols) - len(price_symbols)
            if price_skipped > 0 and not json_output:
                emit(f"Skipped {price_skipped} up-to-date symbols, processing {len(price_symbols)} symbols for prices", json_output=False)

    # Prices
    price_reporter = ProgressReporter(total=len(price_symbols), json_output=json_output, enabled=progress)
    price_reporter.skipped = price_skipped
    price_reporter.workers = price_fetch
    price_reporter.mode = "api"
    price_live: Optional[Live] = None
    if progress and not json_output:
        price_live = price_reporter.start_live()
        if price_live:
            price_live.__enter__()
    try:
        if client is None:
            client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key, calls_per_minute=calls_per_minute)
        price_inserted = 0
        for sym_chunk in chunked(price_symbols, 50):
            price_inserted += ingest_prices_for_symbols(
                db_url=url,
                client=client,
                symbols=sym_chunk,
                max_workers=price_fetch,
                writer_workers=price_writer,
                timeframe=timeframe,
                update_existing=refresh_existing,
                progress=price_reporter,
            )
        if price_live:
            price_live.update(price_reporter._build_table())
        price_reporter.complete(live=price_live)
    except Exception as exc:
        if price_live:
            price_live.__exit__(type(exc), exc, exc.__traceback__)
        emit_error(f"Price ingest failed: {exc}", json_output=json_output)
    finally:
        if price_live:
            price_live.__exit__(None, None, None)

    # Bulk filter symbols that don't need indicator updates
    indicator_symbols = all_symbols
    indicator_skipped = 0
    if not refresh_existing and not refresh:
        from g2.db.ingest import filter_symbols_needing_indicators
        from datetime import date
        with psycopg.connect(url) as conn:
            schema.create_stocks_table(conn)
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)
            target_date = date.today()
            indicator_symbols = filter_symbols_needing_indicators(conn, all_symbols, target_date)
            indicator_skipped = len(all_symbols) - len(indicator_symbols)
            if indicator_skipped > 0 and not json_output:
                emit(f"Skipped {indicator_skipped} up-to-date symbols, processing {len(indicator_symbols)} symbols for indicators", json_output=False)

    # Indicators
    indicator_reporter = ProgressReporter(total=len(indicator_symbols), json_output=json_output, enabled=progress)
    indicator_reporter.skipped = indicator_skipped
    indicator_reporter.workers = feature_fetch
    indicator_reporter.mode = "local" if compute_locally else "api"
    ind_live: Optional[Live] = None
    if progress and not json_output:
        ind_live = indicator_reporter.start_live()
        if ind_live:
            ind_live.__enter__()
    try:
        indicator_inserted = 0
        for sym_chunk in chunked(indicator_symbols, 50):
            indicator_inserted += ingest_indicators_for_symbols(
                db_url=url,
                client=client,
                symbols=sym_chunk,
                indicators=indicator_list,
                timeframe=timeframe,
                update_existing=refresh_existing,
                compute_locally=compute_locally,
                refresh=refresh,
                batch_size=feature_batch_size,
                progress=indicator_reporter,
                fetch_workers=feature_fetch,
                writer_workers=feature_writer,
            )
        if ind_live:
            ind_live.update(indicator_reporter._build_table())
        indicator_reporter.complete(live=ind_live)
    except Exception as exc:
        if ind_live:
            ind_live.__exit__(type(exc), exc, exc.__traceback__)
        emit_error(f"Indicator ingest failed: {exc}", json_output=json_output)
    finally:
        if ind_live:
            ind_live.__exit__(None, None, None)

    emit(
        "Update complete",
        data={
            "symbols": symbols,
            "price_inserted": price_inserted,
            "indicator_inserted": indicator_inserted,
            "price_fetch_workers": price_fetch,
            "price_writer_workers": price_writer,
            "feature_fetch_workers": feature_fetch,
            "feature_writer_workers": feature_writer,
        },
        json_output=json_output,
    )


def entrypoint() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
