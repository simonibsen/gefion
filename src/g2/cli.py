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
from g2.utils.adaptive import AdaptiveLimiter, chunked
from typing import Dict, Any


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


def _export_feature_functions(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, version, status, description, language, function_body, inputs,
                   output_name, output_type, param_schema, defaults, dependencies,
                   checksum, tags, min_app_version, enabled, created_by
            FROM feature_functions
            ORDER BY name, version;
            """
        )
        rows = cur.fetchall()
    data = []
    for r in rows:
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


def _export_feature_definitions(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, function_name, params, source_table, source_column,
                   store_table, store_column, store_type, active, version
            FROM feature_definitions
            ORDER BY name;
            """
        )
        rows = cur.fetchall()
    data = []
    for r in rows:
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
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Apply Timescale tuning: set chunk intervals and optional compression policies.
    Safe to re-run; ignores missing tables.
    """
    url = _db_url(db_url)
    tables = ["stock_ohlcv", "computed_features"]
    applied = {"chunk_interval": [], "compression": []}
    status = {}
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
    emit(
        "DB tuning applied",
        data={"chunk_interval": applied["chunk_interval"], "compression": applied["compression"], "table_status": status},
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
    dir: Path = typer.Option(..., "--dir", help="Directory to write feature data"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
) -> None:
    """
    Export feature_functions and feature_definitions to JSON files for source control.
    """
    target_dir = Path(dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    url = _db_url(db_url)
    try:
        # Export functions
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_functions_table(conn)
            functions = _export_feature_functions(conn)

        # Export definitions
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_definitions_table(conn)
            definitions = _export_feature_definitions(conn)

        (target_dir / "feature_functions.json").write_text(json.dumps(functions, indent=2))
        (target_dir / "feature_definitions.json").write_text(json.dumps(definitions, indent=2))
        emit(f"Exported {len(functions)} functions and {len(definitions)} definitions to {target_dir}")
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
    dir: Path = typer.Option(..., "--dir", help="Directory containing exported feature JSON files"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
) -> None:
    """
    Import feature_functions and feature_definitions from JSON files.
    Idempotent: re-running will upsert by (name, version) and refresh definitions.
    """
    src_dir = Path(dir)
    fx_path = src_dir / "feature_functions.json"
    defs_path = src_dir / "feature_definitions.json"
    if not fx_path.exists() and not defs_path.exists():
        emit_error(f"No feature files found in {src_dir}")
        return

    url = _db_url(db_url)

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True
            schema.create_feature_functions_table(conn)
            if fx_path.exists():
                fx_payload = json.loads(fx_path.read_text())
                for f in fx_payload:
                    _upsert_feature_function(conn, f)

        definitions = []
        if defs_path.exists():
            definitions = json.loads(defs_path.read_text())
            definitions = [_normalize_feature_definition(d) for d in definitions]

        if definitions:
            with psycopg.connect(url) as conn:
                conn.autocommit = True
                schema.create_feature_definitions_table(conn)
                schema.create_computed_features_table(conn)
                ensure_feature_definitions(conn, definitions)
                ensure_store_targets(conn, definitions)

        emit(
            "Imported features",
            data={
                "functions": len(json.loads(fx_path.read_text())) if fx_path.exists() else 0,
                "definitions": len(definitions),
            },
            json_output=True,
        )
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
    feature: str = typer.Option(..., "--feature", help="Comma-separated feature names to drop"),
    data_only: bool = typer.Option(False, "--data-only", help="Delete data rows only; keep feature definitions/schema"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Drop feature definitions and their data.
    WARNING: This deletes rows from computed_features and any custom store tables defined for the feature.
    Use --data-only to remove data rows without dropping definitions/schema.
    """
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
            if data_only:
                deleted = delete_feature_data_only(conn, names)
                emit(
                    f"Deleted data for features {', '.join(names)}",
                    data={"deleted_rows": deleted, "definitions_kept": True},
                    json_output=json_output,
                )
            else:
                deleted = drop_features(conn, names)
                emit(
                    f"Dropped features {', '.join(names)}",
                    data={"deleted": deleted},
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

            # Reserve at least 5 connections (2 for main, 3 buffer)
            budget = max(1, (available or 10) - 5) if available else 2

            # Default to 2 workers (conservative) unless user specifies more
            auto_workers = min(2, budget if budget > 0 else 2)
            max_w = max(1, max_workers or auto_workers)

            if not json_output and progress:
                emit(f"Available connections: {available or 'unknown'}, Max workers: {max_w}")

            # Adaptive worker scaling
            limiter = AdaptiveLimiter(start_workers=1, max_workers=max_w)

            total_inserted = 0
            errors = []

            # Set up progress reporting
            reporter = ProgressReporter(total=len(symbol_list), json_output=json_output, enabled=progress)
            reporter.mode = "dispatcher"
            reporter.workers = 1
            reporter.max_workers = max_w
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
                        with psycopg.connect(url) as worker_conn:
                            worker_conn.autocommit = True

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

                            # Compute features via dispatcher
                            result = compute_features(
                                worker_conn,
                                data_id=data_id,
                                function_names=function_name_list,
                                feature_names=feature_name_list,
                                incremental=incremental,
                                full_refresh=not incremental,
                                update_existing=update_existing,
                            )

                            inserted = result.get('summary', {}).get('total_inserted', 0)
                            has_errors = result.get('summary', {}).get('total_errors', 0) > 0

                            return {
                                "symbol": symbol,
                                "error": False,
                                "inserted": inserted,
                                "has_feature_errors": has_errors,
                                "feature_error_count": result.get('summary', {}).get('total_errors', 0),
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
                    reporter.workers = current_workers

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
                                    meta={"inserted": result["inserted"]}
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
                }
                emit_json(output)
            else:
                emit(f"\nTotal: {total_inserted} rows inserted across {len(symbol_list)} stocks")
                if errors:
                    emit(f"Errors: {len(errors)}")

    except Exception as exc:
        emit_error(f"Computation failed: {exc}", json_output=json_output)


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

    # Prices
    price_reporter = ProgressReporter(total=len(symbols), json_output=json_output, enabled=progress)
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
        for sym_chunk in chunked(symbols, 50):
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

    # Indicators
    indicator_reporter = ProgressReporter(total=len(symbols), json_output=json_output, enabled=progress)
    indicator_reporter.workers = feature_fetch
    indicator_reporter.mode = "local" if compute_locally else "api"
    ind_live: Optional[Live] = None
    if progress and not json_output:
        ind_live = indicator_reporter.start_live()
        if ind_live:
            ind_live.__enter__()
    try:
        indicator_inserted = 0
        for sym_chunk in chunked(symbols, 50):
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
