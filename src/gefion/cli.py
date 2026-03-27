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
import requests
import typer
from typer.core import TyperGroup
from requests import exceptions as req_exc
from rich.console import Console
from rich.table import Table

from gefion.alphavantage.catalog import parse_daily_adjusted
from gefion.alphavantage.client import AlphaVantageClient
from gefion.cli_helpers import (
    parse_comma_separated,
    upsert_feature_function as upsert_feature_function_helper,
    db_connection,
    init_schema_tables,
)
from gefion.features.dispatcher import compute_features
from gefion.config import load_settings
from gefion.db import schema
from psycopg.types.json import Json
from gefion.observability import create_span, set_attributes, add_event, get_current_span, shutdown as otel_shutdown
from gefion.db import migrate
from gefion import health
from gefion.db.ingest import (
    insert_stock_ohlcv,
    upsert_stock,
    ensure_feature_definitions,
    delete_feature_data_only,
    trim_feature_data,
    trim_stock_ohlcv,
    trim_all_computed_features,
    ensure_store_targets,
    drop_features,
    feature_ids_for_names,
)
from gefion.ingest.universe import (
    fetch_listings,
    filter_listings,
    ingest_prices_for_symbols,
    load_listings_from_file,
)
from gefion.utils.progress import ProgressReporter
from rich.live import Live
from gefion.utils.db_load import get_available_connections, plan_workers
from gefion.utils.adaptive import AdaptiveLimiter, ResourceAwareAdaptiveLimiter, chunked
from typing import Dict, Any
from gefion.db import pool as db_pool


class SortedGroup(TyperGroup):
    def list_commands(self, ctx):  # pragma: no cover - cosmetic
        return sorted(super().list_commands(ctx))


app = typer.Typer(
    help="Gefion — quantitative trading analysis",
    no_args_is_help=False,
    add_completion=True,
    invoke_without_command=True,
    cls=SortedGroup,
)
SETTINGS = load_settings()

ml_app = typer.Typer(help="ML workflow commands (dataset/build/train/predict/eval)")
app.add_typer(ml_app, name="ml", cls=SortedGroup)

backtest_app = typer.Typer(help="Backtesting commands (run/compare/analyze)")
app.add_typer(backtest_app, name="backtest", cls=SortedGroup)

strategy_app = typer.Typer(help="Strategy management commands (list/configs/create)")
app.add_typer(strategy_app, name="strategy", cls=SortedGroup)

volatility_app = typer.Typer(help="Volatility analysis commands (compute thresholds)")
app.add_typer(volatility_app, name="volatility", cls=SortedGroup)

experiment_app = typer.Typer(help="AI Experimentation Framework (propose/approve/run)")
app.add_typer(experiment_app, name="experiment", cls=SortedGroup)

chart_app = typer.Typer(help="Chart and visualization commands (price/predictions/features)")
app.add_typer(chart_app, name="chart", cls=SortedGroup)

data_app = typer.Typer(help="Data management commands (cull)")
app.add_typer(data_app, name="data", cls=SortedGroup)


def emit(
    message: str,
    data: Optional[dict] = None,
    json_output: Optional[bool] = None,
    error: bool = False,
) -> None:
    """Emit either plain text or JSON using unified Output interface."""
    from gefion.output import get_output
    out = get_output(json_output)
    if error:
        out.error(message, data)
    else:
        out.success(message, data)


def emit_error(message: str, json_output: Optional[bool] = None, data: Optional[dict] = None) -> None:
    """Emit error message and exit."""
    emit(message, data=data, json_output=json_output, error=True)
    raise typer.Exit(code=1)


def emit_json(payload: dict) -> None:
    """Emit JSON payload using unified Output interface."""
    from gefion.output import get_output
    out = get_output(json_mode=True)
    out.json(payload)


def _tempo_get_json(tempo_url: str, path: str, *, params: Optional[dict] = None, timeout_s: float = 3.0) -> dict:
    url = f"{tempo_url.rstrip('/')}{path}"
    resp = requests.get(url, params=params, timeout=timeout_s)
    resp.raise_for_status()
    return resp.json()


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


def export_definitions_to_directory(
    conn,
    directory: Path,
    feature_names: Optional[List[str]] = None
) -> int:
    """
    Export feature definitions to individual JSON files in a directory.

    Args:
        conn: Database connection
        directory: Target directory for exports (created if doesn't exist)
        feature_names: Optional list of specific feature names to export

    Returns:
        Number of definitions exported
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # Get definitions from database
    definitions = _export_feature_definitions(conn, feature_names)

    # Write each definition to its own file
    for defn in definitions:
        # Filename format: featurename.json (no version in filename since name is unique)
        name = defn["name"]
        filename = f"{name}.json"
        filepath = directory / filename

        filepath.write_text(json.dumps(defn, indent=2))

    return len(definitions)


def import_definitions_from_directory(
    conn,
    directory: Path,
    feature_names: Optional[List[str]] = None
) -> int:
    """
    Import feature definitions from JSON files in a directory.

    Args:
        conn: Database connection
        directory: Source directory containing JSON files
        feature_names: Optional list of specific feature names to import

    Returns:
        Number of definitions imported
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
            if feature_names and payload.get("name") not in feature_names:
                continue

            # Upsert the definition
            _upsert_feature_definition(conn, payload)
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


def _auto_workers(compute_locally: bool, calls_per_minute: int) -> int:
    """
    Calculate optimal worker count based on computation mode and rate limits.

    For local computation: uses CPU count (capped at 8)
    For API mode: respects rate limits to avoid throttling

    Args:
        compute_locally: True if computing features locally, False if using API
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
        # With shared RateLimiter, more workers = better utilization
        # Each worker waits for network I/O (~1-2s per call on average)
        # Formula: workers = calls_per_min / 10 (allows ~6 calls/min per worker)
        # This provides good throughput while RateLimiter enforces rate limits
        api_workers = max(2, min(10, calls_per_minute // 10))
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
    auto_fetch = _auto_workers(compute_locally, calls_per_minute)
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


@ml_app.command("init")
def ml_init(
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Initialize ML tables (datasets/runs/models/predictions)."""
    try:
        with db_connection(db_url) as conn:
            init_schema_tables(
                conn,
                [
                    "stocks",
                    "ml_datasets",
                    "ml_runs",
                    "ml_models",
                    "predictions",
                    "prediction_outcomes",
                    "model_performance",
                ],
            )
        emit("ML schema initialized", json_output=json_output)
    except psycopg.OperationalError as exc:  # pragma: no cover - infra guard
        emit_error(f"Database connection failed: {exc}", json_output=json_output)


@ml_app.command("device")
def ml_device(
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Report the compute device selection (GPU if available, else CPU)."""
    device = "cpu"
    torch_version: Optional[str] = None
    cuda_available = False
    try:
        import torch  # type: ignore

        torch_version = getattr(torch, "__version__", None)
        cuda_available = bool(torch.cuda.is_available())
        device = "cuda" if cuda_available else "cpu"
    except Exception:
        # Torch not installed or CUDA probing failed; default to CPU.
        pass

    emit(
        f"ML device: {device}",
        data={"device": device, "cuda_available": cuda_available, "torch_version": torch_version},
        json_output=json_output,
    )


@ml_app.command("dataset-build")
def ml_dataset_build(
    name: str = typer.Option(..., help="Dataset name (logical identifier)"),
    version: str = typer.Option(..., help="Dataset version (e.g., date tag)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbol list (optional)"),
    exchange: Optional[str] = typer.Option(None, help="Exchange name for universe selection (optional)"),
    limit: Optional[int] = typer.Option(None, help="Optional universe limit (exchange mode)"),
    lookback_days: int = typer.Option(200, help="Rolling window lookback days"),
    horizons: str = typer.Option("7,30,90", help="Comma-separated horizons in days"),
    weak_thresholds: str = typer.Option("0.02,0.05,0.10", help="Comma-separated weak thresholds (per horizon)"),
    strong_thresholds: str = typer.Option("0.05,0.10,0.20", help="Comma-separated strong thresholds (per horizon)"),
    features: Optional[str] = typer.Option(None, help="Comma-separated feature names to include (whitelist mode)"),
    exclude_features: Optional[str] = typer.Option(None, help="Comma-separated feature names to exclude (blacklist mode)"),
    format: str = typer.Option("csv", help="Export format: csv (default) or parquet"),
    out_dir: Path = typer.Option(Path("datasets"), help="Output directory for dataset manifest"),
    export: bool = typer.Option(False, "--export/--no-export", help="Export dataset artifacts (requires DB data)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing dataset if it exists"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Create a dataset manifest and register it in ml_datasets.

    Examples:
        # Build dataset for specific symbols
        gefion ml dataset-build --name tech_stocks --version v1 --symbols AAPL,MSFT,GOOGL

        # Build dataset for NASDAQ exchange (limited to 50 stocks)
        gefion ml dataset-build --name nasdaq_50 --version 2025-01 --exchange NASDAQ --limit 50

        # Build with custom horizons and thresholds
        gefion ml dataset-build --name custom --version v1 --symbols AAPL,MSFT \\
            --horizons 7,14,30 --weak-thresholds 0.02,0.03,0.05 --strong-thresholds 0.05,0.08,0.12
    """
    sym_list = parse_comma_separated(symbols) or []
    if not sym_list and not exchange:
        emit_error("Universe required: provide --symbols or --exchange", json_output=json_output)
        return

    # Feature selection validation
    feature_list = parse_comma_separated(features) or []
    exclude_list = parse_comma_separated(exclude_features) or []
    if feature_list and exclude_list:
        emit_error(
            "Cannot specify both --features and --exclude-features. Use one or the other.",
            json_output=json_output,
        )
        return

    # Format validation
    format_lower = format.lower()
    if format_lower not in ("csv", "parquet"):
        emit_error(
            f"Invalid --format '{format}'. Must be 'csv' or 'parquet'.",
            json_output=json_output,
        )
        return

    try:
        horizon_vals = [int(x) for x in (parse_comma_separated(horizons, required=True) or [])]
    except ValueError:
        raise typer.BadParameter("Invalid --horizons (expected comma-separated integers)")
    if not horizon_vals or any(h <= 0 for h in horizon_vals):
        raise typer.BadParameter("Invalid --horizons (all horizons must be positive integers)")

    try:
        weak_vals = [float(x) for x in (parse_comma_separated(weak_thresholds, required=True) or [])]
        strong_vals = [float(x) for x in (parse_comma_separated(strong_thresholds, required=True) or [])]
    except ValueError:
        raise typer.BadParameter("Invalid thresholds (expected comma-separated numbers)")

    if len(weak_vals) != len(horizon_vals) or len(strong_vals) != len(horizon_vals):
        emit_error(
            "Threshold list length mismatch: provide one weak+strong threshold per horizon",
            json_output=json_output,
        )
        return

    thresholds_by_horizon: dict[str, dict[str, float]] = {}
    for h, weak, strong in zip(horizon_vals, weak_vals, strong_vals):
        if weak <= 0:
            raise typer.BadParameter("Invalid thresholds (weak threshold must be > 0)")
        if strong < weak:
            raise typer.BadParameter("Invalid thresholds (strong threshold must be >= weak threshold)")
        thresholds_by_horizon[str(h)] = {"weak": float(weak), "strong": float(strong)}

    universe: dict[str, object] = {}
    if sym_list:
        universe["symbols"] = sym_list
    if exchange:
        universe["exchange"] = exchange
    if limit is not None:
        universe["limit"] = int(limit)

    label_spec = {"type": "forward_return_5class", "thresholds": thresholds_by_horizon}
    split_spec = {"type": "walk_forward", "note": "TBD", "horizons_days": horizon_vals}

    # Create a subdirectory for this dataset (so features/labels don't collide)
    dataset_dir = out_dir / f"{name}_{version}"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dataset_dir / "manifest.json"
    manifest = {
        "name": name,
        "version": version,
        "universe": universe,
        "feature_names": feature_list,
        "exclude_features": exclude_list,
        "format": format_lower,
        "lookback_days": int(lookback_days),
        "horizons_days": horizon_vals,
        "label_spec": label_spec,
        "split_spec": split_spec,
        "artifact_uri": str(manifest_path),
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    manifest_path.write_text(manifest_text)

    from gefion.ml.store import sha256_text, upsert_ml_dataset

    payload = dict(manifest)
    payload["checksum"] = sha256_text(manifest_text)

    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["ml_datasets"])

        # Check if dataset already exists
        if not force:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ml_datasets WHERE name = %s AND version = %s",
                    (name, version),
                )
                existing = cur.fetchone()
                if existing:
                    emit_error(
                        f"Dataset '{name}' version '{version}' already exists. "
                        "Use --force to overwrite.",
                        json_output=json_output,
                    )

        # If exporting and no feature_names specified, discover available features
        if export and not feature_list:
            with conn.cursor() as cur:
                # Get all feature names that exist for the selected symbols
                if sym_list:
                    cur.execute(
                        """
                        SELECT DISTINCT fd.name
                        FROM computed_features cf
                        JOIN feature_definitions fd ON fd.id = cf.feature_id
                        JOIN stocks s ON s.id = cf.data_id
                        WHERE s.symbol = ANY(%s)
                        ORDER BY fd.name;
                        """,
                        (sym_list,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT DISTINCT fd.name
                        FROM computed_features cf
                        JOIN feature_definitions fd ON fd.id = cf.feature_id
                        ORDER BY fd.name;
                        """
                    )
                discovered_features = [row[0] for row in cur.fetchall()]
                if discovered_features:
                    manifest["feature_names"] = discovered_features
                    payload["feature_names"] = discovered_features
                    emit(f"Discovered {len(discovered_features)} features", json_output=json_output)

        dataset_id = upsert_ml_dataset(conn, payload)
        if export:
            from gefion.ml.dataset import export_dataset_artifacts

            export_dataset_artifacts(
                conn,
                manifest=manifest,
                out_dir=dataset_dir,
                on_progress=lambda msg: emit(msg, json_output=json_output),
            )

    emit(
        f"Dataset registered: {name} {version}",
        data={"dataset_id": dataset_id, "artifact_uri": str(manifest_path)},
        json_output=json_output,
    )


@ml_app.command("dataset-delete")
def ml_dataset_delete(
    name: str = typer.Option(..., help="Dataset name to delete"),
    version: str = typer.Option(..., help="Dataset version to delete"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Delete a dataset and its artifacts.

    Will refuse to delete if models or runs depend on the dataset.
    Delete dependent models first before deleting the dataset.

    Examples:
        # Delete a dataset
        gefion ml dataset-delete --name training --version 20250101

        # Check what depends on a dataset first
        gefion ml dataset-delete --name training --version 20250101
        # Error: Cannot delete dataset. 2 model(s) depend on it:
        #   - my_model v1 (trained 2025-01-15)
        #   - my_model v2 (trained 2025-01-20)
        # Delete these models first with: gefion ml model-delete --name <name> --version <version>
    """
    import shutil

    with db_connection(db_url) as conn:
        with conn.cursor() as cur:
            # Find the dataset
            cur.execute(
                "SELECT id, artifact_uri FROM ml_datasets WHERE name = %s AND version = %s",
                (name, version),
            )
            row = cur.fetchone()

            if not row:
                emit_error(
                    f"Dataset not found: {name} {version}",
                    json_output=json_output,
                )
                return

            dataset_id, artifact_uri = row

            # Check for dependent models
            cur.execute(
                """
                SELECT m.name, m.version, m.created_at
                FROM ml_models m
                WHERE m.dataset_id = %s
                ORDER BY m.created_at DESC
                """,
                (dataset_id,),
            )
            dependent_models = cur.fetchall()

            if dependent_models:
                model_list = "\n".join(
                    f"  - {m[0]} {m[1]} (trained {m[2].strftime('%Y-%m-%d') if m[2] else 'unknown'})"
                    for m in dependent_models
                )
                emit_error(
                    f"Cannot delete dataset '{name} {version}'. "
                    f"{len(dependent_models)} model(s) depend on it:\n{model_list}\n\n"
                    f"Delete these models first with:\n"
                    f"  gefion ml model-delete --name <model_name> --version <model_version>",
                    json_output=json_output,
                )
                return

            # Check for dependent runs
            cur.execute(
                "SELECT COUNT(*) FROM ml_runs WHERE dataset_id = %s",
                (dataset_id,),
            )
            run_count = cur.fetchone()[0]

            # Delete from database (runs will be deleted by cascade if configured, otherwise warn)
            if run_count > 0:
                # Delete runs first
                cur.execute("DELETE FROM ml_runs WHERE dataset_id = %s", (dataset_id,))
                emit(f"Deleted {run_count} associated run record(s)", json_output=json_output)

            cur.execute("DELETE FROM ml_datasets WHERE id = %s", (dataset_id,))
            conn.commit()

            # Delete artifact files
            files_deleted = False
            if artifact_uri:
                artifact_path = Path(artifact_uri)
                # artifact_uri points to manifest.json, get parent directory
                dataset_dir = artifact_path.parent
                if dataset_dir.exists() and dataset_dir.is_dir():
                    shutil.rmtree(dataset_dir)
                    files_deleted = True

            emit(
                f"Deleted dataset: {name} {version}"
                + (f" (removed {dataset_dir})" if files_deleted else ""),
                data={
                    "deleted": True,
                    "name": name,
                    "version": version,
                    "files_removed": files_deleted,
                },
                json_output=json_output,
            )


@ml_app.command("dataset-inspect")
def ml_dataset_inspect(
    name: str = typer.Option(..., help="Dataset name"),
    version: str = typer.Option(..., help="Dataset version"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Inspect a dataset's metadata and show dependent models.

    Displays dataset configuration, feature list, horizons, thresholds,
    and lists all models trained on this dataset.

    Examples:
        # Inspect a dataset
        gefion ml dataset-inspect --name nasdaq_50 --version v1

        # Get JSON output for programmatic use
        gefion ml dataset-inspect --name nasdaq_50 --version v1 --json
    """
    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["ml_datasets", "ml_models"])

        with conn.cursor() as cur:
            # Fetch dataset info
            cur.execute(
                """
                SELECT id, name, version, created_at, universe, feature_names,
                       horizons_days, label_spec, artifact_uri
                FROM ml_datasets
                WHERE name = %s AND version = %s
                """,
                (name, version),
            )
            row = cur.fetchone()

            if not row:
                emit_error(
                    f"Dataset not found: {name} {version}",
                    json_output=json_output,
                )
                return

            dataset_id = row[0]
            dataset_info = {
                "id": row[0],
                "name": row[1],
                "version": row[2],
                "created_at": str(row[3]) if row[3] else None,
                "universe": row[4],
                "feature_names": row[5] or [],
                "horizons_days": row[6] or [],
                "label_spec": row[7],
                "artifact_uri": row[8],
            }

            # Fetch dependent models
            cur.execute(
                """
                SELECT name, version, algorithm, created_at
                FROM ml_models
                WHERE dataset_id = %s
                ORDER BY created_at DESC
                """,
                (dataset_id,),
            )
            models = [
                {
                    "name": m[0],
                    "version": m[1],
                    "algorithm": m[2],
                    "created_at": str(m[3]) if m[3] else None,
                }
                for m in cur.fetchall()
            ]
            dataset_info["models"] = models

        # Output
        if json_output:
            emit(
                f"Dataset: {name} {version}",
                data=dataset_info,
                json_output=json_output,
            )
        else:
            # Pretty print for CLI
            from rich.console import Console
            from rich.table import Table

            console = Console()
            console.print(f"\n[bold]Dataset: {name} {version}[/bold]")
            console.print(f"  Created: {dataset_info['created_at']}")

            universe = dataset_info.get("universe") or {}
            if isinstance(universe, dict):
                if universe.get("exchange"):
                    console.print(f"  Universe: {universe.get('exchange')} (limit: {universe.get('limit', 'all')})")
                elif universe.get("symbols"):
                    symbols = universe.get("symbols", [])
                    console.print(f"  Universe: {len(symbols)} symbols")
            console.print(f"  Horizons: {dataset_info['horizons_days']} days")
            console.print(f"  Features: {len(dataset_info['feature_names'])} features")

            label_spec = dataset_info.get("label_spec") or {}
            thresholds = label_spec.get("thresholds") or {}
            if thresholds:
                console.print("  Thresholds:")
                for horizon, thresh in thresholds.items():
                    console.print(f"    {horizon}d: weak={thresh.get('weak')}, strong={thresh.get('strong')}")

            console.print(f"  Artifact: {dataset_info['artifact_uri']}")

            # Models table
            console.print(f"\n[bold]Models using this dataset ({len(models)}):[/bold]")
            if models:
                table = Table(show_header=True)
                table.add_column("Name")
                table.add_column("Version")
                table.add_column("Algorithm")
                table.add_column("Created")
                for m in models:
                    table.add_row(m["name"], m["version"], m["algorithm"] or "-", m["created_at"] or "-")
                console.print(table)
            else:
                console.print("  (no models)")


@ml_app.command("model-inspect")
def ml_model_inspect(
    name: str = typer.Option(..., help="Model name"),
    version: str = typer.Option(..., help="Model version"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Inspect a model's metadata, training info, and predictions.

    Displays model configuration, algorithm, hyperparameters, training metrics,
    dataset used, and prediction statistics.

    Examples:
        # Inspect a model
        gefion ml model-inspect --name quantile --version 20260101

        # Get JSON output for programmatic use
        gefion ml model-inspect --name quantile --version 20260101 --json
    """
    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["ml_models", "ml_datasets", "predictions", "model_performance"])

        with conn.cursor() as cur:
            # Fetch model info with dataset join
            cur.execute(
                """
                SELECT m.id, m.name, m.version, m.created_at,
                       m.algorithm, m.hyperparams, m.metrics, m.artifact_uri,
                       m.active, d.id, d.name, d.version
                FROM ml_models m
                LEFT JOIN ml_datasets d ON d.id = m.dataset_id
                WHERE m.name = %s AND m.version = %s
                """,
                (name, version),
            )
            row = cur.fetchone()

            if not row:
                emit_error(
                    f"Model not found: {name} {version}",
                    json_output=json_output,
                )
                return

            model_id = row[0]
            model_info = {
                "id": row[0],
                "name": row[1],
                "version": row[2],
                "created_at": str(row[3]) if row[3] else None,
                "algorithm": row[4],
                "hyperparams": row[5] or {},
                "metrics": row[6] or {},
                "artifact_uri": row[7],
                "active": row[8],
                "dataset": {
                    "id": row[9],
                    "name": row[10],
                    "version": row[11],
                } if row[9] else None,
            }

            # Fetch prediction counts by horizon
            try:
                cur.execute(
                    """
                    SELECT horizon_days, COUNT(*), MIN(prediction_date), MAX(prediction_date)
                    FROM predictions
                    WHERE model_id = %s
                    GROUP BY horizon_days
                    ORDER BY horizon_days
                    """,
                    (model_id,),
                )
                predictions = [
                    {
                        "horizon_days": p[0],
                        "count": p[1],
                        "date_range": f"{p[2]} to {p[3]}" if p[2] else None,
                    }
                    for p in cur.fetchall()
                ]
                model_info["predictions"] = predictions
            except Exception:
                model_info["predictions"] = []

            # Fetch performance metrics
            try:
                cur.execute(
                    """
                    SELECT horizon_days, q10_calibration, q50_calibration, q90_calibration,
                           quantile_loss, updated_at
                    FROM model_performance
                    WHERE model_id = %s
                    ORDER BY horizon_days
                    """,
                    (model_id,),
                )
                performance = [
                    {
                        "horizon_days": p[0],
                        "q10_calibration": float(p[1]) if p[1] else None,
                        "q50_calibration": float(p[2]) if p[2] else None,
                        "q90_calibration": float(p[3]) if p[3] else None,
                        "quantile_loss": float(p[4]) if p[4] else None,
                        "updated_at": str(p[5]) if p[5] else None,
                    }
                    for p in cur.fetchall()
                ]
                model_info["performance"] = performance
            except Exception:
                model_info["performance"] = []

        # Output
        if json_output:
            emit(
                f"Model: {name} {version}",
                data=model_info,
                json_output=json_output,
            )
        else:
            # Pretty print for CLI
            from rich.console import Console
            from rich.table import Table

            console = Console()
            console.print(f"\n[bold]Model: {name} {version}[/bold]")
            console.print(f"  Created: {model_info['created_at']}")
            console.print(f"  Algorithm: {model_info['algorithm']}")
            console.print(f"  Active: {model_info['active']}")
            console.print(f"  Artifact: {model_info['artifact_uri']}")

            if model_info["dataset"]:
                ds = model_info["dataset"]
                console.print(f"  Dataset: {ds['name']} {ds['version']}")

            if model_info["hyperparams"]:
                console.print("  Hyperparameters:")
                for k, v in model_info["hyperparams"].items():
                    console.print(f"    {k}: {v}")

            if model_info["metrics"]:
                console.print("  Training Metrics:")
                for k, v in model_info["metrics"].items():
                    if isinstance(v, float):
                        console.print(f"    {k}: {v:.4f}")
                    else:
                        console.print(f"    {k}: {v}")

            # Predictions table
            predictions = model_info.get("predictions", [])
            console.print(f"\n[bold]Predictions ({sum(p['count'] for p in predictions)} total):[/bold]")
            if predictions:
                table = Table(show_header=True)
                table.add_column("Horizon")
                table.add_column("Count", justify="right")
                table.add_column("Date Range")
                for p in predictions:
                    table.add_row(
                        f"{p['horizon_days']}d",
                        f"{p['count']:,}",
                        p["date_range"] or "-",
                    )
                console.print(table)
            else:
                console.print("  (no predictions)")

            # Performance table
            performance = model_info.get("performance", [])
            if performance:
                console.print(f"\n[bold]Performance Metrics:[/bold]")
                table = Table(show_header=True)
                table.add_column("Horizon")
                table.add_column("Q10 Calib", justify="right")
                table.add_column("Q50 Calib", justify="right")
                table.add_column("Q90 Calib", justify="right")
                table.add_column("Loss", justify="right")
                for p in performance:
                    table.add_row(
                        f"{p['horizon_days']}d",
                        f"{p['q10_calibration']:.1f}%" if p['q10_calibration'] else "-",
                        f"{p['q50_calibration']:.1f}%" if p['q50_calibration'] else "-",
                        f"{p['q90_calibration']:.1f}%" if p['q90_calibration'] else "-",
                        f"{p['quantile_loss']:.4f}" if p['quantile_loss'] else "-",
                    )
                console.print(table)


@ml_app.command("model-delete")
def ml_model_delete(
    name: str = typer.Option(..., help="Model name to delete"),
    version: str = typer.Option(..., help="Model version to delete"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Delete a model and its artifacts.

    Deletes the model's database record, associated predictions, and artifact files.

    Examples:
        # Delete a model
        gefion ml model-delete --name quantile --version 20260101
    """
    import shutil

    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["ml_models", "predictions", "model_performance"])

        with conn.cursor() as cur:
            # Find the model
            cur.execute(
                "SELECT id, artifact_uri FROM ml_models WHERE name = %s AND version = %s",
                (name, version),
            )
            row = cur.fetchone()

            if not row:
                emit_error(
                    f"Model not found: {name} {version}",
                    json_output=json_output,
                )
                return

            model_id, artifact_uri = row

            # Count predictions to report
            prediction_count = 0
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM predictions WHERE model_id = %s",
                    (model_id,),
                )
                prediction_count += cur.fetchone()[0]
            except Exception:
                pass

            # Delete predictions
            try:
                cur.execute("DELETE FROM predictions WHERE model_id = %s", (model_id,))
            except Exception:
                pass

            # Delete performance records
            try:
                cur.execute("DELETE FROM model_performance WHERE model_id = %s", (model_id,))
            except Exception:
                pass

            # Delete model record
            cur.execute("DELETE FROM ml_models WHERE id = %s", (model_id,))
            conn.commit()

            # Delete artifact files
            files_deleted = False
            if artifact_uri:
                artifact_path = Path(artifact_uri)
                # artifact_uri might point to a file or directory
                model_dir = artifact_path if artifact_path.is_dir() else artifact_path.parent
                if model_dir.exists() and model_dir.is_dir():
                    shutil.rmtree(model_dir)
                    files_deleted = True

            emit(
                f"Deleted model: {name} {version}"
                + (f" ({prediction_count} predictions removed)" if prediction_count else "")
                + (f" (removed {model_dir})" if files_deleted else ""),
                data={
                    "deleted": True,
                    "name": name,
                    "version": version,
                    "predictions_removed": prediction_count,
                    "files_removed": files_deleted,
                },
                json_output=json_output,
            )


@ml_app.command("train")
def ml_train(
    dataset_name: str = typer.Option(..., help="Dataset name to train on"),
    dataset_version: str = typer.Option(..., help="Dataset version"),
    model_name: str = typer.Option(..., help="Model name (identifier)"),
    model_version: str = typer.Option(..., help="Model version (e.g., date tag)"),
    algorithm: str = typer.Option("quantile_regression", help="Algorithm: quantile_regression, xgboost, lightgbm"),
    device: str = typer.Option("auto", help="Compute device: auto, cpu, cuda (GPU)"),
    out_dir: Path = typer.Option(Path("models"), help="Output directory for model artifacts"),
    warm_start: bool = typer.Option(False, "--warm-start", help="Continue training from base model (10-100x faster)"),
    base_model: Optional[Path] = typer.Option(None, "--base-model", help="Path to base model for warm-start (required if --warm-start)"),
    # Hyperparameter options (use values from 'gefion ml tune' for optimal results)
    learning_rate: Optional[float] = typer.Option(
        None, "--learning-rate",
        help="Learning rate (step size). Lower = more stable but slower. Range: 0.001-0.3. Default: 0.1"
    ),
    n_estimators: Optional[int] = typer.Option(
        None, "--n-estimators",
        help="Number of boosting rounds (trees). More = better fit but slower/risk overfitting. Range: 50-500. Default: 100"
    ),
    max_depth: Optional[int] = typer.Option(
        None, "--max-depth",
        help="Max tree depth. Higher = more complex patterns but risk overfitting. Range: 3-12. Default: 6"
    ),
    min_child_weight: Optional[float] = typer.Option(
        None, "--min-child-weight",
        help="Min samples per leaf. Higher = more regularization. Range: 1-10. Default: 1"
    ),
    subsample: Optional[float] = typer.Option(
        None, "--subsample",
        help="Fraction of samples per tree. Lower = more regularization. Range: 0.5-1.0. Default: 1.0"
    ),
    colsample_bytree: Optional[float] = typer.Option(
        None, "--colsample-bytree",
        help="Fraction of features per tree. Lower = more regularization. Range: 0.5-1.0. Default: 1.0"
    ),
    reg_alpha: Optional[float] = typer.Option(
        None, "--reg-alpha",
        help="L1 regularization (Lasso). Higher = sparser model. Range: 0-10. Default: 0"
    ),
    reg_lambda: Optional[float] = typer.Option(
        None, "--reg-lambda",
        help="L2 regularization (Ridge). Higher = smaller weights. Range: 0-10. Default: 1"
    ),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Train a quantile regression model for multi-horizon return prediction.

    Examples:
        # Train a quantile regression model on a dataset
        gefion ml train --dataset-name tech_stocks --dataset-version v1 \\
            --model-name tech_qr --model-version v1

        # Train using XGBoost algorithm
        gefion ml train --dataset-name nasdaq_50 --dataset-version 2025-01 \\
            --model-name nasdaq_xgb --model-version v1 --algorithm xgboost

        # Train with tuned hyperparameters (from 'gefion ml tune')
        gefion ml train --dataset-name nasdaq_50 --dataset-version 2025-01 \\
            --model-name nasdaq_xgb --model-version v1 --algorithm xgboost \\
            --learning-rate 0.05 --n-estimators 200 --max-depth 8

        # Warm-start from existing model (XGBoost/LightGBM only)
        gefion ml train --dataset-name nasdaq_50 --dataset-version 2025-02 \\
            --model-name nasdaq_xgb --model-version v2 --algorithm xgboost \\
            --warm-start --base-model models/nasdaq_xgb_v1_h7

        # Train with custom output directory
        gefion ml train --dataset-name custom --dataset-version v1 \\
            --model-name custom_model --model-version v1 --out-dir ./my_models
    """
    from gefion.ml.store import get_ml_dataset
    from gefion.ml.models import load_dataset, train_quantile_model, save_model_artifact
    from gefion.ml.device import detect_device

    # Resolve device (auto-detect if "auto")
    if device == "auto":
        resolved_device = detect_device()
    else:
        resolved_device = device

    # Build hyperparams dict from CLI options (only include if explicitly set)
    hyperparams = {}
    if learning_rate is not None:
        hyperparams["learning_rate"] = learning_rate
    if n_estimators is not None:
        hyperparams["n_estimators"] = n_estimators
    if max_depth is not None:
        hyperparams["max_depth"] = max_depth
    if min_child_weight is not None:
        hyperparams["min_child_weight"] = min_child_weight
    if subsample is not None:
        hyperparams["subsample"] = subsample
    if colsample_bytree is not None:
        hyperparams["colsample_bytree"] = colsample_bytree
    if reg_alpha is not None:
        hyperparams["reg_alpha"] = reg_alpha
    if reg_lambda is not None:
        hyperparams["reg_lambda"] = reg_lambda

    # Validate warm-start options
    if warm_start and not base_model:
        emit_error("--warm-start requires --base-model path", json_output=json_output)
        return
    if warm_start and algorithm == "quantile_regression":
        emit_error("--warm-start not supported for quantile_regression (use xgboost or lightgbm)", json_output=json_output)
        return

    with db_connection(db_url) as conn:
        # Fetch dataset manifest
        dataset = get_ml_dataset(conn, name=dataset_name, version=dataset_version)
        if not dataset:
            emit_error(f"Dataset not found: {dataset_name} {dataset_version}", json_output=json_output)
            return

        # Train models for each horizon
        artifact_uri = Path(dataset["artifact_uri"])
        horizons = dataset["horizons_days"]
        all_train_metrics = {}

        if warm_start:
            emit(f"Warm-start training {algorithm} models from {base_model}", json_output=json_output)
        emit(f"Training {algorithm} models on {resolved_device} for horizons: {horizons}", json_output=json_output)
        if hyperparams:
            emit(f"  Using hyperparameters: {hyperparams}", json_output=json_output)

        for horizon in horizons:
            emit(f"Training model for {horizon}-day horizon...", json_output=json_output)

            # Load features and labels for this horizon
            X, y = load_dataset(artifact_uri, horizon)
            emit(f"  Loaded {len(X)} samples with {X.shape[1]} features", json_output=json_output)

            # Determine base model path for this horizon
            base_model_path = None
            if warm_start and base_model:
                # Try horizon-specific path first, then generic
                horizon_specific = base_model.parent / f"{base_model.name}_h{horizon}"
                if horizon_specific.exists():
                    base_model_path = horizon_specific
                elif base_model.exists():
                    base_model_path = base_model
                else:
                    emit(f"  Warning: Base model not found at {base_model}, training from scratch", json_output=json_output)

            # Train quantile models (q10, q50, q90)
            model_data = train_quantile_model(
                X, y,
                algorithm=algorithm,
                hyperparams=hyperparams if hyperparams else None,
                device=resolved_device,
                base_model_path=base_model_path
            )
            if model_data.get("warm_start"):
                emit(f"  Warm-started {len(model_data['models'])} quantile models", json_output=json_output)
            else:
                emit(f"  Trained {len(model_data['models'])} quantile models", json_output=json_output)

            # Save model artifact
            out_dir.mkdir(parents=True, exist_ok=True)
            model_path = out_dir / f"{model_name}_{model_version}_h{horizon}"
            save_model_artifact(
                model_data,
                model_path,
                metadata={
                    "model_name": model_name,
                    "model_version": model_version,
                    "horizon_days": horizon,
                    "dataset_name": dataset_name,
                    "dataset_version": dataset_version,
                    "hyperparams": hyperparams if hyperparams else {},
                }
            )
            emit(f"  Saved artifacts to {model_path}", json_output=json_output)

            all_train_metrics[f"h{horizon}"] = model_data["train_metrics"]

        # Register model in ml_models
        from psycopg.types.json import Json

        # Use first horizon's path as base artifact URI (individual horizons have _hN suffix)
        base_artifact_path = out_dir / f"{model_name}_{model_version}"

        with conn.cursor() as cur:
            # Create run record
            cur.execute(
                """
                INSERT INTO ml_runs (run_type, status, dataset_id, run_config, started_at)
                VALUES ('train', 'running', %s, %s, NOW())
                RETURNING id;
                """,
                (dataset["id"], Json({"algorithm": algorithm, "model_name": model_name})),
            )
            run_id = int(cur.fetchone()[0])

            # Register model
            cur.execute(
                """
                INSERT INTO ml_models
                  (name, version, train_run_id, dataset_id, algorithm, hyperparams, metrics, artifact_uri)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name, version) DO UPDATE SET
                  train_run_id = EXCLUDED.train_run_id,
                  dataset_id = EXCLUDED.dataset_id,
                  algorithm = EXCLUDED.algorithm,
                  hyperparams = EXCLUDED.hyperparams,
                  metrics = EXCLUDED.metrics,
                  artifact_uri = EXCLUDED.artifact_uri
                RETURNING id;
                """,
                (
                    model_name,
                    model_version,
                    run_id,
                    dataset["id"],
                    algorithm,
                    Json({"algorithm": algorithm}),
                    Json(all_train_metrics),
                    str(base_artifact_path),
                ),
            )
            model_id = int(cur.fetchone()[0])

            # Mark run as complete
            cur.execute(
                """
                UPDATE ml_runs SET status = 'completed', finished_at = NOW()
                WHERE id = %s;
                """,
                (run_id,),
            )

        conn.commit()

    emit(
        f"Model trained: {model_name} {model_version}",
        data={"model_id": model_id, "run_id": run_id, "artifact_uri": str(base_artifact_path), "horizons": horizons},
        json_output=json_output,
    )


@ml_app.command("predict")
def ml_predict(
    model_name: str = typer.Option(..., help="Model name to use for predictions"),
    model_version: str = typer.Option(..., help="Model version"),
    prediction_date: Optional[str] = typer.Option(None, "--prediction-date", "-d", help="Single date (YYYY-MM-DD). Auto-detects if not provided."),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="Start of date range (YYYY-MM-DD). Use with --end-date."),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End of date range (YYYY-MM-DD). Use with --start-date."),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbol list (optional)"),
    exchange: Optional[str] = typer.Option(None, help="Exchange name for universe selection (optional)"),
    limit: Optional[int] = typer.Option(None, help="Optional universe limit (exchange mode)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Generate predictions using a trained model.

    Supports single date or date range. Date-smart: automatically skips
    weekends and dates without computed features.

    Examples:
        # Generate predictions for specific symbols (auto-detect date)
        gefion ml predict --model-name tech_qr --model-version v1 --symbols AAPL,MSFT,GOOGL

        # Generate predictions for NASDAQ universe with explicit date
        gefion ml predict --model-name nasdaq_xgb --model-version v1 \\
            --prediction-date 2025-01-15 --exchange NASDAQ --limit 50

        # Generate predictions for a date range (date-smart)
        gefion ml predict --model-name quantile --model-version v1 \\
            --start-date 2025-11-01 --end-date 2025-12-31 --symbols AAPL,MSFT
    """
    import pandas as pd
    from gefion.ml.models import load_model_artifact, predict_quantiles
    from gefion.ml.store import get_ml_dataset

    sym_list = parse_comma_separated(symbols) or []
    if not sym_list and not exchange:
        emit_error("Universe required: provide --symbols or --exchange", json_output=json_output)
        return

    with db_connection(db_url) as conn:
        # Fetch model metadata
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset_id, artifact_uri, algorithm
                FROM ml_models
                WHERE name = %s AND version = %s;
                """,
                (model_name, model_version),
            )
            row = cur.fetchone()
            if not row:
                emit_error(f"Model not found: {model_name} {model_version}", json_output=json_output)
                return

            model_id, dataset_id, artifact_uri, algorithm = row[0], row[1], row[2], row[3]

        # Check if this is a classifier model - redirect to predict-classifier
        if algorithm and algorithm.startswith("classifier_"):
            emit_error(
                f"Model '{model_name}/{model_version}' is a classifier (algorithm={algorithm}).\n"
                f"Use 'gefion ml predict-classifier --model-path {artifact_uri}' instead.",
                json_output=json_output,
            )
            return

        # Check if this is an ensemble model - redirect to predict-ensemble
        if algorithm == "ensemble":
            emit_error(
                f"Model '{model_name}/{model_version}' is an ensemble.\n"
                f"Use 'gefion ml predict-ensemble --model-name {model_name} --model-version {model_version}' instead.",
                json_output=json_output,
            )
            return

        # Get dataset to know which features and horizons to use
        dataset = get_ml_dataset(conn, name="", version="")  # Need to query by dataset_id
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, version, feature_names, horizons_days
                FROM ml_datasets
                WHERE id = %s;
                """,
                (dataset_id,),
            )
            row = cur.fetchone()
            if not row:
                emit_error(f"Dataset not found for model (id={dataset_id})", json_output=json_output)
                return
            dataset_name, dataset_version, feature_names, horizons = row[0], row[1], row[2], row[3]

        # Build universe of symbols
        # Note: exchange param is stored for documentation but stocks table has no exchange column
        if exchange or (not sym_list and limit):
            with conn.cursor() as cur:
                limit_clause = f"LIMIT {limit}" if limit else ""
                cur.execute(
                    f"""
                    SELECT DISTINCT s.id, s.symbol
                    FROM stocks s
                    ORDER BY s.symbol
                    {limit_clause};
                    """
                )
                universe = [(row[0], row[1]) for row in cur.fetchall()]
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, symbol FROM stocks WHERE symbol = ANY(%s);
                    """,
                    (sym_list,),
                )
                universe = [(row[0], row[1]) for row in cur.fetchall()]

        if not universe:
            emit_error("No symbols found in universe", json_output=json_output)
            return

        data_ids = [u[0] for u in universe]

        # Validate date options
        if (start_date and not end_date) or (end_date and not start_date):
            emit_error("Both --start-date and --end-date must be provided for date range", json_output=json_output)
            return

        if prediction_date and (start_date or end_date):
            emit_error("Cannot use --prediction-date with --start-date/--end-date", json_output=json_output)
            return

        # Determine dates to process
        dates_to_process = []

        if start_date and end_date:
            # Date range mode - find all dates with features in range
            from datetime import datetime as dt
            start_dt = dt.strptime(start_date, "%Y-%m-%d").date()
            end_dt = dt.strptime(end_date, "%Y-%m-%d").date()

            if start_dt > end_dt:
                emit_error("--start-date must be before --end-date", json_output=json_output)
                return

            with conn.cursor() as cur:
                # Find all dates with features for these symbols in range
                cur.execute(
                    """
                    SELECT DISTINCT cf.date
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND fd.name = ANY(%s)
                      AND cf.date >= %s
                      AND cf.date <= %s
                      AND EXTRACT(DOW FROM cf.date) NOT IN (0, 6)  -- Skip weekends
                    ORDER BY cf.date;
                    """,
                    (data_ids, feature_names, start_date, end_date),
                )
                dates_to_process = [row[0].isoformat() for row in cur.fetchall()]

            if not dates_to_process:
                emit_error(
                    f"No trading days with features found between {start_date} and {end_date}. "
                    f"Run 'gefion data-update' to compute features.",
                    json_output=json_output,
                )
                return

            # Calculate skipped dates for reporting
            from datetime import timedelta
            total_days = (end_dt - start_dt).days + 1
            skipped = total_days - len(dates_to_process)

            emit(
                f"Date range: {start_date} to {end_date} "
                f"({len(dates_to_process)} trading days, skipped {skipped} days without features)",
                json_output=json_output,
            )

        elif prediction_date:
            # Single date mode
            dates_to_process = [prediction_date]
        else:
            # Auto-detect latest date
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT MAX(cf.date)
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND fd.name = ANY(%s);
                    """,
                    (data_ids, feature_names),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    emit_error(f"No features found for symbols. Ensure data-update has been run.", json_output=json_output)
                    return
                dates_to_process = [row[0].isoformat()]
                emit(f"Auto-detected prediction date: {dates_to_process[0]}", json_output=json_output)

        # Process each date
        grand_total_predictions = 0
        dates_processed = 0
        dates_skipped = 0

        # Import required modules for prediction loop
        from psycopg.types.json import Json
        from decimal import Decimal

        # Pre-load models (same for all dates)
        horizon_models = {}
        for horizon in horizons:
            horizon_model_path = Path(f"{artifact_uri}_h{horizon}")
            horizon_models[horizon] = load_model_artifact(horizon_model_path)

        # Create run record for batch
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ml_runs (run_type, status, dataset_id, run_config, started_at)
                VALUES ('predict', 'running', %s, %s, NOW())
                RETURNING id;
                """,
                (
                    dataset_id,
                    Json(
                        {
                            "model_name": model_name,
                            "model_version": model_version,
                            "dates": dates_to_process if len(dates_to_process) > 1 else dates_to_process[0],
                            "universe": {"symbols": sym_list} if sym_list else {"exchange": exchange},
                        }
                    ),
                ),
            )
            run_id = int(cur.fetchone()[0])

        for date_idx, current_date in enumerate(dates_to_process, 1):
            if len(dates_to_process) > 1:
                emit(
                    f"[{date_idx}/{len(dates_to_process)}] Processing {current_date}...",
                    data={"date_idx": date_idx, "total_dates": len(dates_to_process), "current_date": current_date},
                    json_output=json_output,
                )
            else:
                emit(f"Generating predictions for {len(universe)} symbols on {current_date}", json_output=json_output)

            # Fetch features for all symbols on current_date
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cf.data_id, fd.name, cf.value
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND cf.date = %s
                      AND fd.name = ANY(%s);
                    """,
                    (data_ids, current_date, feature_names),
                )
                features_data = cur.fetchall()

            if not features_data:
                # Skip dates without features in batch mode
                if len(dates_to_process) > 1:
                    dates_skipped += 1
                    continue
                else:
                    # Single date mode - show helpful error
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT MAX(date) FROM computed_features WHERE data_id = ANY(%s)",
                            (data_ids,),
                        )
                        row = cur.fetchone()
                        latest_date = row[0] if row else None

                    if latest_date:
                        emit_error(
                            f"No features found for {current_date}. "
                            f"Latest available: {latest_date}. "
                            f"Run 'gefion data-update' to compute features for more recent dates.",
                            json_output=json_output,
                        )
                    else:
                        emit_error(
                            f"No features found for {current_date}. "
                            f"Run 'gefion data-update' to compute features first.",
                            json_output=json_output,
                        )
                    return

            # Convert to DataFrame and pivot to wide format
            features_df = pd.DataFrame(features_data, columns=["data_id", "feature_name", "value"])
            features_wide = features_df.pivot_table(
                index="data_id",
                columns="feature_name",
                values="value",
                aggfunc="first"
            )

            date_predictions = 0
            for horizon in horizons:
                model_data = horizon_models[horizon]
                predictions = predict_quantiles(model_data, features_wide)

                # Insert predictions into database
                with conn.cursor() as cur:
                    for data_id in predictions.index:
                        q10 = Decimal(str(predictions.loc[data_id, "q10"]))
                        q50 = Decimal(str(predictions.loc[data_id, "q50"]))
                        q90 = Decimal(str(predictions.loc[data_id, "q90"]))

                        cur.execute(
                            """
                            INSERT INTO predictions
                              (model_id, data_id, prediction_date, horizon_days,
                               prediction_type, prediction_values, metadata, run_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (model_id, data_id, prediction_date, horizon_days, prediction_type)
                            DO UPDATE SET
                              prediction_values = EXCLUDED.prediction_values,
                              metadata = EXCLUDED.metadata,
                              run_id = EXCLUDED.run_id,
                              created_at = NOW();
                            """,
                            (model_id, int(data_id), current_date, horizon,
                             'quantile',
                             Json({"q10": float(q10), "q50": float(q50), "q90": float(q90)}),
                             Json({"model_version": model_version}),
                             run_id),
                        )
                        date_predictions += 1
                        grand_total_predictions += 1

            dates_processed += 1

            if len(dates_to_process) == 1:
                for horizon in horizons:
                    preds_per_horizon = len(features_wide)
                    emit(f"  Stored {preds_per_horizon} predictions for {horizon}-day horizon", json_output=json_output)

        # Mark run as complete
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ml_runs SET status = 'completed', finished_at = NOW()
                WHERE id = %s;
                """,
                (run_id,),
            )

        conn.commit()

    # Final summary
    if len(dates_to_process) > 1:
        emit(
            f"Batch complete: {grand_total_predictions} predictions across {dates_processed} dates",
            data={
                "model_id": model_id,
                "run_id": run_id,
                "total_predictions": grand_total_predictions,
                "dates_processed": dates_processed,
                "dates_skipped": dates_skipped,
                "horizons": horizons,
            },
            json_output=json_output,
        )
    else:
        emit(f"Generated {grand_total_predictions} predictions", json_output=json_output)
        emit(
            f"Predictions generated: {model_name} {model_version} for {dates_to_process[0]}",
            data={
                "model_id": model_id,
                "run_id": run_id,
                "prediction_date": dates_to_process[0],
                "total_predictions": grand_total_predictions,
                "horizons": horizons,
            },
            json_output=json_output,
        )


@ml_app.command("predict-list")
def ml_predict_list(
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Filter by model name"),
    model_version: Optional[str] = typer.Option(None, "--model-version", help="Filter by model version"),
    symbol: Optional[str] = typer.Option(None, "--symbol", help="Filter by symbol"),
    prediction_date: Optional[str] = typer.Option(None, "--date", help="Filter by prediction date (YYYY-MM-DD)"),
    prediction_type: Optional[str] = typer.Option(None, "--type", help="Filter by prediction type (e.g. quantile, trend_class)"),
    limit: int = typer.Option(50, help="Maximum number of results"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    List predictions with optional filters.

    Shows prediction summaries grouped by model, date, and symbol.

    Examples:
        # List all recent predictions
        gefion ml predict-list

        # Filter by model
        gefion ml predict-list --model-name quantile --model-version 20260101

        # Filter by symbol
        gefion ml predict-list --symbol AAPL

        # Filter by prediction type
        gefion ml predict-list --type quantile
    """
    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["predictions", "ml_models", "stocks"])

        with conn.cursor() as cur:
            # Build query with optional filters
            conditions = []
            params = []

            if model_name:
                conditions.append("m.name = %s")
                params.append(model_name)
            if model_version:
                conditions.append("m.version = %s")
                params.append(model_version)
            if symbol:
                conditions.append("s.symbol = %s")
                params.append(symbol)
            if prediction_date:
                conditions.append("p.prediction_date = %s")
                params.append(prediction_date)
            if prediction_type:
                conditions.append("p.prediction_type = %s")
                params.append(prediction_type)

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(limit)

            cur.execute(
                f"""
                SELECT m.name, m.version, s.symbol, p.prediction_date,
                       p.horizon_days, p.prediction_type, p.prediction_values
                FROM predictions p
                JOIN ml_models m ON p.model_id = m.id
                JOIN stocks s ON p.data_id = s.id
                {where_clause}
                ORDER BY p.prediction_date DESC, s.symbol, p.horizon_days
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

            if not rows:
                emit("No predictions found matching criteria", json_output=json_output)
                return

            predictions = []
            for r in rows:
                p_type = r[5]
                p_values = r[6] or {}
                entry = {
                    "model": f"{r[0]} {r[1]}",
                    "symbol": r[2],
                    "date": str(r[3]),
                    "horizon": r[4],
                    "type": p_type,
                }
                if p_type == "quantile":
                    entry["q10"] = float(p_values["q10"]) if "q10" in p_values else None
                    entry["q50"] = float(p_values["q50"]) if "q50" in p_values else None
                    entry["q90"] = float(p_values["q90"]) if "q90" in p_values else None
                elif p_type == "trend_class":
                    entry["predicted_class"] = p_values.get("predicted_class")
                    entry["margin"] = float(p_values["margin"]) if "margin" in p_values else None
                    entry["entropy"] = float(p_values["entropy"]) if "entropy" in p_values else None
                else:
                    entry.update(p_values)
                predictions.append(entry)

            if json_output:
                emit(
                    f"Found {len(predictions)} predictions",
                    data={"predictions": predictions, "count": len(predictions)},
                    json_output=json_output,
                )
            else:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                console.print(f"\n[bold]Predictions ({len(predictions)} found):[/bold]\n")

                table = Table(show_header=True)
                table.add_column("Model")
                table.add_column("Symbol")
                table.add_column("Date")
                table.add_column("Horizon")
                table.add_column("Type")
                table.add_column("Values", justify="right")

                for p in predictions:
                    if p.get("type") == "quantile":
                        values_str = (
                            f"q10={p['q10']:.2%} q50={p['q50']:.2%} q90={p['q90']:.2%}"
                            if p.get("q50") is not None else "-"
                        )
                    elif p.get("type") == "trend_class":
                        cls = p.get("predicted_class", "?")
                        margin = p.get("margin")
                        values_str = f"{cls} (margin={margin:.3f})" if margin is not None else cls
                    else:
                        values_str = str({k: v for k, v in p.items() if k not in ("model", "symbol", "date", "horizon", "type")})

                    table.add_row(
                        p["model"],
                        p["symbol"],
                        p["date"],
                        f"{p['horizon']}d",
                        p.get("type", "-"),
                        values_str,
                    )

                console.print(table)


@ml_app.command("predict-inspect")
def ml_predict_inspect(
    symbol: str = typer.Option(..., "--symbol", help="Symbol to inspect"),
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Model name (uses latest if not specified)"),
    model_version: Optional[str] = typer.Option(None, "--model-version", help="Model version"),
    prediction_date: Optional[str] = typer.Option(None, "--date", help="Prediction date (uses latest if not specified)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Inspect predictions for a specific symbol.

    Shows detailed prediction information including all horizons and price context.

    Examples:
        # Inspect latest predictions for a symbol
        gefion ml predict-inspect --symbol AAPL

        # Inspect predictions from specific model and date
        gefion ml predict-inspect --symbol AAPL --model-name quantile --date 2025-12-03
    """
    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["predictions", "ml_models", "stocks", "stock_ohlcv"])

        with conn.cursor() as cur:
            # Find the stock
            cur.execute("SELECT id FROM stocks WHERE symbol = %s", (symbol,))
            row = cur.fetchone()
            if not row:
                emit_error(f"Symbol not found: {symbol}", json_output=json_output)
                return
            data_id = row[0]

            # Build query for predictions
            conditions = ["p.data_id = %s"]
            params = [data_id]

            if model_name:
                conditions.append("m.name = %s")
                params.append(model_name)
            if model_version:
                conditions.append("m.version = %s")
                params.append(model_version)
            if prediction_date:
                conditions.append("p.prediction_date = %s")
                params.append(prediction_date)

            where_clause = " AND ".join(conditions)

            cur.execute(
                f"""
                SELECT m.name, m.version, p.prediction_date, p.horizon_days,
                       p.prediction_values->>'q10', p.prediction_values->>'q50',
                       p.prediction_values->>'q90', p.created_at
                FROM predictions p
                JOIN ml_models m ON p.model_id = m.id
                WHERE {where_clause} AND p.prediction_type = 'quantile'
                ORDER BY p.prediction_date DESC, p.horizon_days
                LIMIT 20
                """,
                params,
            )
            predictions = cur.fetchall()

            if not predictions:
                emit_error(f"No predictions found for {symbol}", json_output=json_output)
                return

            # Get latest price for context
            cur.execute(
                """
                SELECT date, close, adjusted_close
                FROM stock_ohlcv
                WHERE data_id = %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (data_id,),
            )
            price_row = cur.fetchone()
            latest_price = None
            price_date = None
            if price_row:
                price_date = str(price_row[0])
                latest_price = float(price_row[2] or price_row[1])

            prediction_data = [
                {
                    "model": f"{p[0]} {p[1]}",
                    "model_name": p[0],
                    "model_version": p[1],
                    "prediction_date": str(p[2]),
                    "horizon_days": p[3],
                    "q10": float(p[4]) if p[4] else None,
                    "q50": float(p[5]) if p[5] else None,
                    "q90": float(p[6]) if p[6] else None,
                    "created_at": str(p[7]) if p[7] else None,
                }
                for p in predictions
            ]

            result = {
                "symbol": symbol,
                "latest_price": latest_price,
                "price_date": price_date,
                "predictions": prediction_data,
            }

            if json_output:
                emit(
                    f"Predictions for {symbol}",
                    data=result,
                    json_output=json_output,
                )
            else:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                console.print(f"\n[bold]Predictions for {symbol}[/bold]")
                if latest_price:
                    console.print(f"  Latest price: ${latest_price:.2f} ({price_date})")
                console.print()

                # Group by prediction date
                dates = sorted(set(p["prediction_date"] for p in prediction_data), reverse=True)

                for pred_date in dates:
                    date_preds = [p for p in prediction_data if p["prediction_date"] == pred_date]
                    model = date_preds[0]["model"]
                    console.print(f"[bold]Date: {pred_date}[/bold] (Model: {model})")

                    table = Table(show_header=True)
                    table.add_column("Horizon")
                    table.add_column("Q10 (Downside)", justify="right")
                    table.add_column("Q50 (Median)", justify="right")
                    table.add_column("Q90 (Upside)", justify="right")
                    if latest_price:
                        table.add_column("Q50 Price", justify="right")

                    for p in sorted(date_preds, key=lambda x: x["horizon_days"]):
                        row = [
                            f"{p['horizon_days']}d",
                            f"{p['q10']:.2%}" if p["q10"] else "-",
                            f"{p['q50']:.2%}" if p["q50"] else "-",
                            f"{p['q90']:.2%}" if p["q90"] else "-",
                        ]
                        if latest_price and p["q50"]:
                            projected = latest_price * (1 + p["q50"])
                            row.append(f"${projected:.2f}")
                        elif latest_price:
                            row.append("-")
                        table.add_row(*row)

                    console.print(table)
                    console.print()


@ml_app.command("eval")
def ml_eval(
    model_name: str = typer.Option(..., help="Model name to evaluate"),
    model_version: str = typer.Option(..., help="Model version"),
    start_date: str = typer.Option(..., help="Evaluation start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., help="Evaluation end date (YYYY-MM-DD)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Evaluate model performance on historical predictions."""
    import pandas as pd
    from datetime import datetime, timedelta
    from decimal import Decimal
    from gefion.ml.evaluation import calculate_calibration_metrics, generate_evaluation_report

    with db_connection(db_url) as conn:
        # Fetch model
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset_id
                FROM ml_models
                WHERE name = %s AND version = %s;
                """,
                (model_name, model_version),
            )
            row = cur.fetchone()
            if not row:
                emit_error(f"Model not found: {model_name} {model_version}", json_output=json_output)
                return

            model_id, dataset_id = row[0], row[1]

        emit(f"Evaluating {model_name} {model_version} from {start_date} to {end_date}...", json_output=json_output)

        # Fetch predictions from predictions table
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.data_id, p.prediction_date, p.horizon_days,
                       (p.prediction_values->>'q10')::numeric,
                       (p.prediction_values->>'q50')::numeric,
                       (p.prediction_values->>'q90')::numeric,
                       s.symbol
                FROM predictions p
                JOIN stocks s ON p.data_id = s.id
                WHERE p.model_id = %s
                  AND p.prediction_date >= %s
                  AND p.prediction_date <= %s
                  AND p.prediction_type = 'quantile'
                ORDER BY p.prediction_date, p.data_id, p.horizon_days;
                """,
                (model_id, start_date, end_date),
            )
            predictions_data = cur.fetchall()

        if not predictions_data:
            emit_error(f"No predictions found for evaluation period", json_output=json_output)
            return

        emit(f"Found {len(predictions_data)} predictions to evaluate", json_output=json_output)

        # Convert to DataFrame
        predictions_df = pd.DataFrame(
            predictions_data,
            columns=["data_id", "prediction_date", "horizon_days", "q10", "q50", "q90", "symbol"]
        )

        # Calculate actual returns for each prediction
        emit("Calculating actual returns...", json_output=json_output)

        actual_returns = []
        for _, row in predictions_df.iterrows():
            data_id = row["data_id"]
            pred_date = row["prediction_date"]
            horizon = row["horizon_days"]

            # Calculate outcome date (prediction_date + horizon_days)
            outcome_date = pred_date + timedelta(days=horizon)

            # Fetch close prices for prediction_date and outcome_date
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date, close
                    FROM stock_ohlcv
                    WHERE data_id = %s
                      AND date IN (%s, %s)
                    ORDER BY date;
                    """,
                    (int(data_id), pred_date, outcome_date),
                )
                prices = cur.fetchall()

            if len(prices) == 2:
                start_price = float(prices[0][1])
                end_price = float(prices[1][1])
                actual_return = (end_price - start_price) / start_price
                actual_returns.append(actual_return)
            else:
                # Missing price data - skip this prediction
                actual_returns.append(None)

        predictions_df["actual_return"] = actual_returns

        # Filter out predictions with missing actual returns
        valid_predictions = predictions_df[predictions_df["actual_return"].notna()].copy()
        emit(f"Valid predictions with actual returns: {len(valid_predictions)}", json_output=json_output)

        if len(valid_predictions) == 0:
            emit_error("No valid predictions with actual returns found", json_output=json_output)
            return

        # Calculate metrics by horizon
        from psycopg.types.json import Json

        horizons = valid_predictions["horizon_days"].unique()
        all_metrics = {}

        for horizon in sorted(horizons):
            horizon_data = valid_predictions[valid_predictions["horizon_days"] == horizon]

            # Prepare predictions and actuals for metrics calculation
            preds = horizon_data[["q10", "q50", "q90"]].astype(float)
            actuals = horizon_data["actual_return"].astype(float)

            # Calculate calibration metrics
            metrics = calculate_calibration_metrics(preds, actuals)
            all_metrics[int(horizon)] = metrics

            emit(f"Horizon {horizon} days: {metrics['num_samples']} samples, "
                 f"q50_calibration={metrics.get('q50_calibration', 0):.1f}%", json_output=json_output)

        # Generate evaluation report
        report = generate_evaluation_report(model_name, all_metrics)
        emit(report, json_output=json_output)

        # Create run record
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ml_runs (run_type, status, dataset_id, run_config, started_at)
                VALUES ('eval', 'running', %s, %s, NOW())
                RETURNING id;
                """,
                (dataset_id, Json({"start_date": start_date, "end_date": end_date})),
            )
            run_id = int(cur.fetchone()[0])

        # Store metrics in model_performance (one row per model+horizon)
        if all_metrics:
            with conn.cursor() as cur:
                for horizon, metrics in all_metrics.items():
                    cur.execute(
                        """
                        INSERT INTO model_performance
                          (model_id, model_name, horizon_days, q10_calibration, q50_calibration, q90_calibration,
                           quantile_loss, avg_iqr, eval_start_date, eval_end_date, num_predictions, eval_run_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (model_id, horizon_days) DO UPDATE SET
                          q10_calibration = EXCLUDED.q10_calibration,
                          q50_calibration = EXCLUDED.q50_calibration,
                          q90_calibration = EXCLUDED.q90_calibration,
                          quantile_loss = EXCLUDED.quantile_loss,
                          avg_iqr = EXCLUDED.avg_iqr,
                          eval_start_date = EXCLUDED.eval_start_date,
                          eval_end_date = EXCLUDED.eval_end_date,
                          num_predictions = EXCLUDED.num_predictions,
                          eval_run_id = EXCLUDED.eval_run_id,
                          updated_at = NOW();
                        """,
                        (
                            model_id,
                            model_name,
                            horizon,
                            Decimal(str(metrics.get("q10_calibration", 0))),
                            Decimal(str(metrics.get("q50_calibration", 0))),
                            Decimal(str(metrics.get("q90_calibration", 0))),
                            Decimal(str(metrics.get("quantile_loss", 0))) if "quantile_loss" in metrics else None,
                            Decimal(str(metrics.get("avg_iqr", 0))) if "avg_iqr" in metrics else None,
                            start_date,
                            end_date,
                            metrics.get("num_samples", 0),
                            run_id,
                        ),
                    )

        # Mark run as complete
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ml_runs SET status = 'completed', finished_at = NOW()
                WHERE id = %s;
                """,
                (run_id,),
            )

        conn.commit()

    emit(
        f"Model evaluated: {model_name} {model_version}",
        data={"model_id": model_id, "run_id": run_id, "eval_period": f"{start_date} to {end_date}", "horizons": list(all_metrics.keys())},
        json_output=json_output,
    )


@ml_app.command("calibrate")
def ml_calibrate(
    model_name: str = typer.Option(..., help="Model name to calibrate"),
    model_version: str = typer.Option(..., help="Model version"),
    start_date: str = typer.Option(..., help="Calibration period start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., help="Calibration period end date (YYYY-MM-DD)"),
    out_dir: Path = typer.Option(Path("models"), help="Directory containing model artifacts"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Calibrate a quantile model using conformal prediction.

    Computes additive shift corrections from a holdout period so that
    predicted quantiles achieve their nominal coverage rates (10%, 50%, 90%).

    Saves calibration.json alongside each horizon's model artifacts.
    Future predictions automatically apply calibration shifts.

    Examples:
        gefion ml calibrate --model-name quantile --model-version 20260202 \\
            --start-date 2025-06-01 --end-date 2025-12-31

        gefion ml calibrate --model-name nasdaq_xgb --model-version v1 \\
            --start-date 2025-01-01 --end-date 2025-06-30 --json
    """
    import pandas as pd
    from datetime import timedelta
    from decimal import Decimal
    from gefion.ml.evaluation import calculate_calibration_metrics
    from gefion.ml.calibration import (
        compute_calibration_shifts,
        apply_calibration_shifts,
        save_calibration,
        generate_calibration_report,
    )

    with create_span("cli.ml-calibrate", model_name=model_name, model_version=model_version):
        with db_connection(db_url) as conn:
            # Fetch model metadata
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, dataset_id, artifact_uri
                    FROM ml_models
                    WHERE name = %s AND version = %s;
                    """,
                    (model_name, model_version),
                )
                row = cur.fetchone()
                if not row:
                    emit_error(f"Model not found: {model_name} {model_version}", json_output=json_output)
                    return

                model_id, dataset_id, artifact_uri = row[0], row[1], row[2]

            # Get dataset horizons
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT horizons_days FROM ml_datasets WHERE id = %s;",
                    (dataset_id,),
                )
                ds_row = cur.fetchone()
                if not ds_row:
                    emit_error(f"Dataset not found for model (id={dataset_id})", json_output=json_output)
                    return
                horizons = ds_row[0]

            emit(
                f"Calibrating {model_name} {model_version} using period {start_date} to {end_date}...",
                json_output=json_output,
            )

            # Fetch stored predictions for calibration period
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.data_id, p.prediction_date, p.horizon_days,
                           (p.prediction_values->>'q10')::numeric,
                           (p.prediction_values->>'q50')::numeric,
                           (p.prediction_values->>'q90')::numeric,
                           s.symbol
                    FROM predictions p
                    JOIN stocks s ON p.data_id = s.id
                    WHERE p.model_id = %s
                      AND p.prediction_date >= %s
                      AND p.prediction_date <= %s
                      AND p.prediction_type = 'quantile'
                    ORDER BY p.prediction_date, p.data_id, p.horizon_days;
                    """,
                    (model_id, start_date, end_date),
                )
                predictions_data = cur.fetchall()

            if not predictions_data:
                emit_error(
                    f"No predictions found for calibration period {start_date} to {end_date}. "
                    f"Run 'gefion ml predict' for this period first.",
                    json_output=json_output,
                )
                return

            predictions_df = pd.DataFrame(
                predictions_data,
                columns=["data_id", "prediction_date", "horizon_days", "q10", "q50", "q90", "symbol"],
            )

            emit(f"Found {len(predictions_df)} predictions", json_output=json_output)

            # Calculate actual returns for each prediction
            actual_returns = []
            for _, row in predictions_df.iterrows():
                data_id = row["data_id"]
                pred_date = row["prediction_date"]
                horizon = row["horizon_days"]
                outcome_date = pred_date + timedelta(days=horizon)

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT date, close
                        FROM stock_ohlcv
                        WHERE data_id = %s AND date IN (%s, %s)
                        ORDER BY date;
                        """,
                        (int(data_id), pred_date, outcome_date),
                    )
                    prices = cur.fetchall()

                if len(prices) == 2:
                    start_price = float(prices[0][1])
                    end_price = float(prices[1][1])
                    actual_returns.append((end_price - start_price) / start_price)
                else:
                    actual_returns.append(None)

            predictions_df["actual_return"] = actual_returns
            valid = predictions_df[predictions_df["actual_return"].notna()].copy()

            if len(valid) == 0:
                emit_error("No valid predictions with actual returns found", json_output=json_output)
                return

            emit(f"Valid predictions with actuals: {len(valid)}", json_output=json_output)

            # Calibrate per horizon
            shifts_by_horizon: Dict[int, Dict[str, Any]] = {}
            all_horizons = sorted(valid["horizon_days"].unique())

            for h in all_horizons:
                h_data = valid[valid["horizon_days"] == h]
                preds = h_data[["q10", "q50", "q90"]].astype(float)
                actuals = h_data["actual_return"].astype(float)

                # Before calibration metrics
                before_metrics = calculate_calibration_metrics(preds, actuals)

                # Compute shifts
                shifts = compute_calibration_shifts(preds, actuals)

                # After calibration metrics
                calibrated_preds = apply_calibration_shifts(preds, shifts)
                after_metrics = calculate_calibration_metrics(calibrated_preds, actuals)

                # Save calibration.json to artifact directory
                artifact_path = Path(f"{artifact_uri}_h{h}")
                if artifact_path.exists():
                    cal_metadata = {
                        "calibration_period": {"start_date": start_date, "end_date": end_date},
                        "num_samples": len(h_data),
                        "before_metrics": before_metrics,
                        "after_metrics": after_metrics,
                    }
                    save_calibration(shifts, artifact_path, cal_metadata)
                    emit(f"Horizon {h}: saved calibration.json ({len(h_data)} samples)", json_output=json_output)
                else:
                    emit(f"Horizon {h}: artifact dir not found at {artifact_path}, skipping save", json_output=json_output)

                shifts_by_horizon[int(h)] = {
                    "shifts": shifts,
                    "before": before_metrics,
                    "after": after_metrics,
                }

            # Print report
            report = generate_calibration_report(model_name, shifts_by_horizon)
            emit(report, json_output=json_output)

            # Update model metrics JSONB with calibrated flag
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ml_models
                    SET metrics = COALESCE(metrics, '{}'::jsonb) || %s
                    WHERE id = %s;
                    """,
                    (Json({"calibrated": True, "calibration_period": f"{start_date} to {end_date}"}), model_id),
                )
            conn.commit()

        if json_output:
            emit(
                "Calibration complete",
                data={
                    "model_name": model_name,
                    "model_version": model_version,
                    "horizons": list(shifts_by_horizon.keys()),
                    "shifts_by_horizon": shifts_by_horizon,
                },
                json_output=json_output,
            )
        else:
            emit(f"Calibration complete for {model_name} {model_version}")


@ml_app.command("feature-importance")
def ml_feature_importance(
    model_name: str = typer.Option(..., help="Model name"),
    model_version: str = typer.Option(..., help="Model version"),
    horizon: int = typer.Option(..., help="Horizon in days (e.g., 7, 30, 90)"),
    quantile: str = typer.Option("q50", help="Quantile to analyze (q10, q50, q90)"),
    top_k: int = typer.Option(20, help="Number of top features to display"),
    out_dir: Path = typer.Option(Path("models"), help="Directory containing model artifacts"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output as JSON"),
) -> None:
    """
    Compute SHAP-based feature importance for a trained model.

    Shows which features contribute most to model predictions.
    Requires the model to be trained with XGBoost or LightGBM for
    fast TreeSHAP computation. Falls back to permutation importance
    for sklearn models.

    Examples:
        # Show top 20 features for 7-day horizon
        gefion ml feature-importance --model-name mvp --model-version 20251228 --horizon 7

        # Show top 10 features as JSON
        gefion ml feature-importance --model-name mvp --model-version 20251228 --horizon 7 --top-k 10 --json
    """
    from gefion.ml.importance import get_feature_importance, format_importance_table

    # Build model artifact path
    model_dir = out_dir / f"{model_name}_{model_version}_h{horizon}"

    if not model_dir.exists():
        emit_error(f"Model not found: {model_dir}", json_output=json_output)
        return

    emit(f"Computing feature importance for {model_name} {model_version} (horizon={horizon})...", json_output=json_output)

    try:
        result = get_feature_importance(
            model_path=model_dir,
            quantile=quantile,
            top_k=top_k,
        )

        if json_output:
            emit(
                "Feature importance computed",
                data=result,
                json_output=json_output,
            )
        else:
            # Pretty print as table
            table = format_importance_table(result["importance"], top_k=top_k)
            emit(table)
            emit(f"\nAlgorithm: {result['algorithm']}")
            emit(f"Total features: {result['num_features']}")

    except ImportError as e:
        emit_error(str(e), json_output=json_output)
    except FileNotFoundError as e:
        emit_error(str(e), json_output=json_output)
    except Exception as e:
        emit_error(f"Failed to compute importance: {e}", json_output=json_output)


@ml_app.command("tune")
def ml_tune(
    dataset_name: str = typer.Option(..., help="Dataset name to use for tuning"),
    dataset_version: str = typer.Option(..., help="Dataset version"),
    algorithm: str = typer.Option("xgboost", help="Algorithm: xgboost, lightgbm, or sklearn"),
    model_type: str = typer.Option("quantile", help="Model type: quantile or classifier"),
    horizon: int = typer.Option(7, help="Horizon in days for quantile models"),
    quantile: float = typer.Option(0.5, help="Quantile to optimize (0.1, 0.5, 0.9)"),
    n_trials: int = typer.Option(50, help="Number of optimization trials"),
    cv_splits: int = typer.Option(5, help="Number of time-series CV splits"),
    timeout: Optional[int] = typer.Option(None, help="Timeout in seconds"),
    scoring: str = typer.Option("pinball", help="Scoring: pinball (default) or mae"),
    out_dir: Path = typer.Option(Path("models"), help="Output directory for results"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output as JSON"),
) -> None:
    """
    Tune hyperparameters using Optuna with time-series cross-validation.

    Uses Bayesian optimization to find optimal hyperparameters while
    preventing data leakage through time-series CV splits.

    Examples:
        # Tune XGBoost quantile model with 50 trials
        gefion ml tune --dataset-name mvp --dataset-version v1 \\
          --algorithm xgboost --n-trials 50

        # Tune with pinball loss scoring (default)
        gefion ml tune --dataset-name mvp --dataset-version v1 --scoring pinball

        # Tune classifier with LightGBM
        gefion ml tune --dataset-name mvp --dataset-version v1 \\
          --algorithm lightgbm --model-type classifier --n-trials 100

        # Quick tuning with timeout
        gefion ml tune --dataset-name mvp --dataset-version v1 --timeout 300
    """
    from gefion.ml.tuning import (
        tune_quantile_model,
        tune_classifier,
        save_tuning_results,
    )

    # Load dataset
    datasets_dir = Path("datasets")
    dataset_dir = datasets_dir / f"{dataset_name}_{dataset_version}"
    manifest_path = dataset_dir / "manifest.json"

    if not manifest_path.exists():
        emit_error(
            f"Dataset not found: {manifest_path}\n"
            f"Build dataset first with: gefion ml dataset-build --name {dataset_name} --version {dataset_version} --export",
            json_output=json_output
        )
        return

    emit(f"Loading dataset {dataset_name}/{dataset_version}...", json_output=json_output)

    try:
        import pandas as pd
        import json as json_module

        # Load manifest
        with open(manifest_path) as f:
            manifest = json_module.load(f)

        # Load features
        features_path = dataset_dir / "features.csv"
        if not features_path.exists():
            features_path = dataset_dir / "features.parquet"

        if features_path.suffix == ".parquet":
            features_df = pd.read_parquet(features_path)
        else:
            features_df = pd.read_csv(features_path)

        # Load labels
        labels_path = dataset_dir / "labels.csv"
        if not labels_path.exists():
            labels_path = dataset_dir / "labels.parquet"

        if labels_path.suffix == ".parquet":
            labels_df = pd.read_parquet(labels_path)
        else:
            labels_df = pd.read_csv(labels_path)

        # Pivot features to wide format
        X = features_df.pivot_table(
            index=['symbol', 'date'],
            columns='feature_name',
            values='value',
            aggfunc='first'
        ).reset_index()

        # Filter labels by horizon
        if model_type == "quantile":
            labels_filtered = labels_df[labels_df['horizon_days'] == horizon].copy()
            y = labels_filtered.set_index(['symbol', 'date'])['forward_return']
        else:
            labels_filtered = labels_df[labels_df['horizon_days'] == horizon].copy()
            y = labels_filtered.set_index(['symbol', 'date'])['label']

        # Align X and y
        X = X.set_index(['symbol', 'date'])
        common_idx = X.index.intersection(y.index)
        X = X.loc[common_idx]
        y = y.loc[common_idx]

        if len(X) < 50:
            emit_error(
                f"Insufficient data for tuning: {len(X)} samples (need >= 50)",
                json_output=json_output
            )
            return

        emit(f"Tuning {algorithm} {model_type} model with {len(X)} samples...", json_output=json_output)
        emit(f"Running {n_trials} trials with {cv_splits}-fold time-series CV...", json_output=json_output)

        # Progress callback for displaying trial progress
        def progress_callback(trial_num: int, total_trials: int, best_value: float) -> None:
            pct = (trial_num / total_trials) * 100
            emit(
                f"Trial {trial_num}/{total_trials} ({pct:.0f}%) - Best score: {best_value:.6f}",
                json_output=json_output
            )

        # Run tuning
        if model_type == "quantile":
            result = tune_quantile_model(
                X=X,
                y=y,
                algorithm=algorithm,
                quantile=quantile,
                n_trials=n_trials,
                cv_splits=cv_splits,
                timeout=timeout,
                progress_callback=progress_callback,
                scoring=scoring,
            )
        else:
            result = tune_classifier(
                X=X,
                y=y,
                algorithm=algorithm,
                n_trials=n_trials,
                cv_splits=cv_splits,
                timeout=timeout,
                progress_callback=progress_callback,
            )

        # Save results
        out_dir.mkdir(parents=True, exist_ok=True)
        results_path = out_dir / f"tuning_{dataset_name}_{dataset_version}_{algorithm}.json"
        save_tuning_results(result, results_path)

        if json_output:
            emit("Tuning complete", data=result, json_output=json_output)
        else:
            emit("\n" + "=" * 50)
            emit("TUNING RESULTS")
            emit("=" * 50)
            emit(f"Algorithm: {result['algorithm']}")
            emit(f"Best score: {result['best_score']:.6f}")
            emit(f"Trials completed: {result['n_trials']}")
            emit(f"\nBest parameters:")
            for k, v in result['best_params'].items():
                emit(f"  {k}: {v}")
            emit(f"\nResults saved to: {results_path}")

    except ImportError as e:
        emit_error(f"Missing dependency: {e}", json_output=json_output)
    except Exception as e:
        emit_error(f"Tuning failed: {e}", json_output=json_output)


@ml_app.command("train-classifier")
def ml_train_classifier(
    dataset_name: str = typer.Option(..., help="Dataset name to train on"),
    dataset_version: str = typer.Option(..., help="Dataset version"),
    model_name: str = typer.Option(..., help="Model name (identifier)"),
    model_version: str = typer.Option(..., help="Model version (e.g., date tag)"),
    algorithm: str = typer.Option("sklearn", help="Algorithm: sklearn, xgboost, lightgbm"),
    device: str = typer.Option("auto", help="Compute device: auto, cpu, cuda (GPU)"),
    horizon: int = typer.Option(..., help="Horizon in days for classification"),
    out_dir: Path = typer.Option(Path("models"), help="Output directory for model artifacts"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Train a multi-class classifier for trend prediction (5-class labels)."""
    import joblib
    from gefion.ml.store import get_ml_dataset
    from gefion.ml.classifier import load_dataset_for_classifier, train_classifier, evaluate_classifier
    from gefion.ml.device import detect_device

    # Resolve device (auto-detect if "auto")
    if device == "auto":
        resolved_device = detect_device()
    else:
        resolved_device = device

    with db_connection(db_url) as conn:
        # Fetch dataset manifest
        dataset = get_ml_dataset(conn, name=dataset_name, version=dataset_version)
        if not dataset:
            emit_error(f"Dataset not found: {dataset_name} {dataset_version}", json_output=json_output)
            return

        emit(f"Training {algorithm} classifier on {resolved_device} for {horizon}-day horizon...", json_output=json_output)

        # Load features and labels for this horizon
        artifact_uri = Path(dataset["artifact_uri"])
        X, y = load_dataset_for_classifier(artifact_uri, horizon)
        emit(f"  Loaded {len(X)} samples with {X.shape[1]} features", json_output=json_output)
        emit(f"  Label distribution: {y.value_counts().to_dict()}", json_output=json_output)

        # Train classifier
        model_artifacts = train_classifier(X, y, algorithm=algorithm, device=resolved_device)
        emit(f"  Training accuracy: {model_artifacts['train_metrics']['train_accuracy']:.4f}", json_output=json_output)

        # Evaluate
        eval_metrics = evaluate_classifier(model_artifacts, X, y)
        emit(f"  Accuracy: {eval_metrics['accuracy']:.4f}", json_output=json_output)

        # Save model artifact
        out_dir.mkdir(parents=True, exist_ok=True)
        model_path = out_dir / f"{model_name}_{model_version}_h{horizon}_classifier"
        model_path.mkdir(parents=True, exist_ok=True)
        joblib.dump(model_artifacts, model_path / "classifier.pkl")
        (model_path / "metadata.json").write_text(
            json.dumps({
                "model_name": model_name,
                "model_version": model_version,
                "horizon_days": horizon,
                "dataset_name": dataset_name,
                "dataset_version": dataset_version,
                "algorithm": algorithm,
                "feature_names": dataset["feature_names"],
                "train_metrics": model_artifacts["train_metrics"],
                "eval_metrics": eval_metrics,
            }, indent=2)
        )
        emit(f"  Saved artifacts to {model_path}", json_output=json_output)

        # Register model in ml_models
        from psycopg.types.json import Json

        with conn.cursor() as cur:
            # Create run record
            cur.execute(
                """
                INSERT INTO ml_runs (run_type, status, dataset_id, run_config, started_at)
                VALUES ('train_classifier', 'running', %s, %s, NOW())
                RETURNING id;
                """,
                (dataset["id"], Json({"algorithm": algorithm, "model_name": model_name, "horizon": horizon})),
            )
            run_id = int(cur.fetchone()[0])

            # Register model
            cur.execute(
                """
                INSERT INTO ml_models
                  (name, version, train_run_id, dataset_id, algorithm, hyperparams, metrics, artifact_uri)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name, version) DO UPDATE SET
                  train_run_id = EXCLUDED.train_run_id,
                  dataset_id = EXCLUDED.dataset_id,
                  algorithm = EXCLUDED.algorithm,
                  hyperparams = EXCLUDED.hyperparams,
                  metrics = EXCLUDED.metrics,
                  artifact_uri = EXCLUDED.artifact_uri
                RETURNING id;
                """,
                (
                    model_name,
                    model_version,
                    run_id,
                    dataset["id"],
                    f"classifier_{algorithm}",
                    Json({"algorithm": algorithm, "horizon": horizon}),
                    Json({"train": model_artifacts["train_metrics"], "eval": eval_metrics}),
                    str(model_path),
                ),
            )
            model_id = int(cur.fetchone()[0])

            # Mark run as complete
            cur.execute(
                """
                UPDATE ml_runs SET status = 'completed', finished_at = NOW()
                WHERE id = %s;
                """,
                (run_id,),
            )

        conn.commit()

    emit(
        f"Classifier trained: {model_name} {model_version}",
        data={"model_id": model_id, "run_id": run_id, "artifact_uri": str(model_path), "horizon": horizon},
        json_output=json_output,
    )


@ml_app.command("predict-classifier")
def ml_predict_classifier(
    model_path: Path = typer.Option(..., help="Path to classifier model directory"),
    prediction_date: Optional[str] = typer.Option(None, help="Date to generate predictions for (YYYY-MM-DD). Auto-detects if not provided."),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="Start date for batch predictions (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date for batch predictions (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (optional)"),
    exchange: Optional[str] = typer.Option(None, help="Exchange name for universe selection (optional)"),
    limit: Optional[int] = typer.Option(None, help="Optional universe limit"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Generate trend class predictions using a trained classifier.

    Examples:
        # Generate predictions for specific date
        gefion ml predict-classifier --model-path models/classifier_v1_h7 \\
            --prediction-date 2025-01-15 --symbols AAPL,MSFT,GOOGL

        # Generate predictions for a date range (batch backfill)
        gefion ml predict-classifier --model-path models/classifier_v1_h7 \\
            --start-date 2025-01-01 --end-date 2025-01-31 --exchange NASDAQ --limit 50
    """
    import pandas as pd
    import joblib
    from gefion.ml.classifier import predict_classifier
    from decimal import Decimal
    from psycopg.types.json import Json
    from datetime import datetime, timedelta

    # Handle date range vs single date
    if start_date and end_date:
        # Date range mode
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            emit_error("Invalid date format. Use YYYY-MM-DD", json_output=json_output)
            return

        if start_dt > end_dt:
            emit_error("start-date must be before end-date", json_output=json_output)
            return

        # Generate list of dates
        prediction_dates = []
        current = start_dt
        while current <= end_dt:
            prediction_dates.append(current.isoformat())
            current += timedelta(days=1)
        emit(f"Batch prediction mode: {len(prediction_dates)} dates from {start_date} to {end_date}", json_output=json_output)
    elif prediction_date:
        prediction_dates = [prediction_date]
    else:
        # Will auto-detect later
        prediction_dates = [None]

    # Load model
    model_artifacts = joblib.load(model_path / "classifier.pkl")
    metadata_path = model_path / "metadata.json"
    if not metadata_path.exists():
        emit_error(f"No metadata.json found in {model_path}", json_output=json_output)
        return

    metadata = json.loads(metadata_path.read_text())
    emit(f"Loaded classifier: {metadata['model_name']} {metadata['model_version']}", json_output=json_output)
    emit(f"  Horizon: {metadata['horizon_days']} days", json_output=json_output)
    emit(f"  Algorithm: {metadata['algorithm']}", json_output=json_output)

    feature_names = metadata.get("feature_names", [])
    horizon = metadata["horizon_days"]
    model_name = metadata["model_name"]
    model_version = metadata["model_version"]

    sym_list = parse_comma_separated(symbols) or []
    if not sym_list and not exchange:
        emit_error("Universe required: provide --symbols or --exchange", json_output=json_output)
        return

    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["predictions"])

        # Build universe of symbols
        if exchange or (not sym_list and limit):
            with conn.cursor() as cur:
                limit_clause = f"LIMIT {limit}" if limit else ""
                cur.execute(f"""
                    SELECT DISTINCT s.id, s.symbol
                    FROM stocks s
                    ORDER BY s.symbol
                    {limit_clause};
                """)
                universe = [(row[0], row[1]) for row in cur.fetchall()]
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, symbol FROM stocks WHERE symbol = ANY(%s);",
                    (sym_list,),
                )
                universe = [(row[0], row[1]) for row in cur.fetchall()]

        if not universe:
            emit_error("No symbols found in universe", json_output=json_output)
            return

        data_ids = [u[0] for u in universe]
        symbol_map = {u[0]: u[1] for u in universe}

        # Auto-detect prediction date if not provided (single date mode with None)
        if prediction_dates == [None]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT MAX(cf.date)
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND fd.name = ANY(%s);
                    """,
                    (data_ids, feature_names),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    emit_error("No features found for symbols. Ensure data-update has been run.", json_output=json_output)
                    return
                prediction_dates = [row[0].isoformat()]
                emit(f"Auto-detected prediction date: {prediction_dates[0]}", json_output=json_output)

        # Process each prediction date
        grand_total_predictions = 0
        dates_processed = 0
        dates_skipped = 0

        # Get or create model record (once, outside the loop)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM ml_models WHERE name = %s AND version = %s;",
                (model_name, model_version),
            )
            row = cur.fetchone()
            if row:
                model_id = row[0]
            else:
                # Insert model record if not exists
                cur.execute(
                    """
                    INSERT INTO ml_models (name, version, algorithm, artifact_uri)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (model_name, model_version, f"classifier_{metadata['algorithm']}", str(model_path)),
                )
                model_id = cur.fetchone()[0]

        for pred_date in prediction_dates:
            emit(f"Generating predictions for {len(universe)} symbols on {pred_date}", json_output=json_output)

            # Fetch features for all symbols on pred_date
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cf.data_id, fd.name, cf.value
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND cf.date = %s
                      AND fd.name = ANY(%s);
                    """,
                    (data_ids, pred_date, feature_names),
                )
                features_data = cur.fetchall()

            if not features_data:
                # Skip this date in batch mode, error in single date mode
                if len(prediction_dates) > 1:
                    emit(f"  Skipping {pred_date}: no features available", json_output=json_output)
                    dates_skipped += 1
                    continue
                else:
                    # Find latest available date to help user
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT MAX(date) FROM computed_features WHERE data_id = ANY(%s)",
                            (data_ids,),
                        )
                        row = cur.fetchone()
                        latest_date = row[0] if row else None

                    if latest_date:
                        emit_error(
                            f"No features found for {pred_date}. "
                            f"Latest available: {latest_date}. "
                            f"Run 'gefion data-update' to compute features for more recent dates.",
                            json_output=json_output,
                        )
                    else:
                        emit_error(
                            f"No features found for {pred_date}. "
                            f"Run 'gefion data-update' to compute features first.",
                            json_output=json_output,
                        )
                    return

            # Convert to DataFrame and pivot to wide format
            features_df = pd.DataFrame(features_data, columns=["data_id", "feature_name", "value"])
            features_wide = features_df.pivot_table(
                index="data_id",
                columns="feature_name",
                values="value",
                aggfunc="first"
            )

            emit(f"  Loaded features: {features_wide.shape[0]} symbols x {features_wide.shape[1]} features", json_output=json_output)

            # Generate predictions (preserve data_id from features index)
            predictions = predict_classifier(model_artifacts, features_wide)
            # Set predictions index to match features_wide data_ids
            predictions.index = features_wide.index

            # Store predictions in database
            total_predictions = 0
            with conn.cursor() as cur:
                for data_id in predictions.index:
                    pred_row = predictions.loc[data_id]
                    predicted_class = pred_row["predicted_class"]

                    # Get class probabilities (columns start with "probability_")
                    prob_cols = [c for c in predictions.columns if c.startswith("probability_")]
                    class_probs = {c.replace("probability_", ""): float(pred_row[c]) for c in prob_cols}

                    # Extract individual probabilities for table columns
                    p_strong_up = class_probs.get("strong_up", 0.0)
                    p_weak_up = class_probs.get("weak_up", 0.0)
                    p_neutral = class_probs.get("flat", 0.0)
                    p_weak_down = class_probs.get("weak_down", 0.0)
                    p_strong_down = class_probs.get("strong_down", 0.0)

                    # Calculate margin (difference between top 2 probabilities)
                    sorted_probs = sorted(class_probs.values(), reverse=True)
                    margin = sorted_probs[0] - sorted_probs[1] if len(sorted_probs) > 1 else sorted_probs[0]

                    # Calculate entropy: -sum(p * log(p)) for non-zero p
                    import math
                    entropy = -sum(p * math.log(p) for p in class_probs.values() if p > 0)

                    cur.execute(
                        """
                        INSERT INTO predictions
                          (model_id, data_id, prediction_date, horizon_days,
                           prediction_type, prediction_values, metadata, run_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (model_id, data_id, prediction_date, horizon_days, prediction_type)
                        DO UPDATE SET
                          prediction_values = EXCLUDED.prediction_values,
                          metadata = EXCLUDED.metadata,
                          run_id = EXCLUDED.run_id,
                          created_at = NOW();
                        """,
                        (model_id, int(data_id), pred_date, horizon,
                         'trend_class',
                         Json({
                             "predicted_class": predicted_class,
                             "p_strong_up": p_strong_up,
                             "p_weak_up": p_weak_up,
                             "p_neutral": p_neutral,
                             "p_weak_down": p_weak_down,
                             "p_strong_down": p_strong_down,
                             "entropy": entropy,
                             "margin": margin,
                         }),
                         Json({}),
                         None),
                    )
                    total_predictions += 1

            conn.commit()
            grand_total_predictions += total_predictions
            dates_processed += 1
            emit(f"  Stored {total_predictions} predictions", json_output=json_output)

    # Summary
    if len(prediction_dates) > 1:
        emit(f"Batch complete: {dates_processed} dates processed, {dates_skipped} skipped, {grand_total_predictions} total predictions", json_output=json_output)
        emit(
            f"Classifier predictions generated: {model_name} {model_version}",
            data={"model_id": model_id, "dates_processed": dates_processed, "dates_skipped": dates_skipped, "total_predictions": grand_total_predictions, "horizon": horizon},
            json_output=json_output,
        )
    else:
        emit(f"Generated {grand_total_predictions} predictions", json_output=json_output)
        emit(
            f"Classifier predictions generated: {model_name} {model_version} for {prediction_dates[0]}",
            data={"model_id": model_id, "prediction_date": prediction_dates[0], "total_predictions": grand_total_predictions, "horizon": horizon},
            json_output=json_output,
        )


@ml_app.command("train-ensemble")
def ml_train_ensemble(
    dataset_name: str = typer.Option(..., help="Dataset name to train on"),
    dataset_version: str = typer.Option(..., help="Dataset version"),
    model_name: str = typer.Option(..., help="Ensemble model name (identifier)"),
    model_version: str = typer.Option(..., help="Model version (e.g., date tag)"),
    algorithms: str = typer.Option(
        "quantile_regression,quantile_regression",
        help="Comma-separated algorithms: quantile_regression, xgboost, lightgbm"
    ),
    weights: Optional[str] = typer.Option(None, help="Comma-separated weights (must sum to 1.0). Defaults to equal weights."),
    out_dir: Path = typer.Option(Path("models"), help="Output directory for model artifacts"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Train an ensemble of quantile regression models for multi-horizon return prediction.

    Ensembles combine predictions from multiple algorithms for improved accuracy.
    Supports weighted averaging of predictions.

    Examples:
        # Train ensemble with two sklearn models (different regularization)
        gefion ml train-ensemble --dataset-name tech_stocks --dataset-version v1 \\
            --model-name tech_ensemble --model-version v1 \\
            --algorithms quantile_regression,quantile_regression

        # Train ensemble with XGBoost and LightGBM
        gefion ml train-ensemble --dataset-name nasdaq_50 --dataset-version 2025-01 \\
            --model-name nasdaq_ensemble --model-version v1 \\
            --algorithms xgboost,lightgbm

        # Train ensemble with custom weights
        gefion ml train-ensemble --dataset-name custom --dataset-version v1 \\
            --model-name custom_ensemble --model-version v1 \\
            --algorithms xgboost,lightgbm,quantile_regression \\
            --weights 0.5,0.3,0.2
    """
    from gefion.ml.store import get_ml_dataset
    from gefion.ml.models import load_dataset
    from gefion.ml.ensemble import train_ensemble

    # Parse algorithms
    algo_list = [a.strip() for a in algorithms.split(",")]
    if not algo_list:
        emit_error("At least one algorithm must be specified", json_output=json_output)
        return

    # Parse weights
    weight_list = None
    if weights:
        try:
            weight_list = [float(w.strip()) for w in weights.split(",")]
            if len(weight_list) != len(algo_list):
                emit_error(
                    f"Number of weights ({len(weight_list)}) must match number of algorithms ({len(algo_list)})",
                    json_output=json_output
                )
                return
            if not abs(sum(weight_list) - 1.0) < 0.001:
                emit_error(f"Weights must sum to 1.0, got {sum(weight_list)}", json_output=json_output)
                return
        except ValueError:
            emit_error(f"Invalid weights format: {weights}", json_output=json_output)
            return

    with db_connection(db_url) as conn:
        # Fetch dataset manifest
        dataset = get_ml_dataset(conn, name=dataset_name, version=dataset_version)
        if not dataset:
            emit_error(f"Dataset not found: {dataset_name} {dataset_version}", json_output=json_output)
            return

        # Train ensembles for each horizon
        artifact_uri = Path(dataset["artifact_uri"])
        horizons = dataset["horizons_days"]
        all_train_metrics = {}

        emit(f"Training ensemble ({', '.join(algo_list)}) for horizons: {horizons}", json_output=json_output)
        if weight_list:
            emit(f"  Using weights: {weight_list}", json_output=json_output)
        else:
            emit(f"  Using equal weights", json_output=json_output)

        for horizon in horizons:
            emit(f"Training ensemble for {horizon}-day horizon...", json_output=json_output)

            # Load features and labels for this horizon
            X, y = load_dataset(artifact_uri, horizon)
            emit(f"  Loaded {len(X)} samples with {X.shape[1]} features", json_output=json_output)

            # Train ensemble
            ensemble_path = out_dir / f"{model_name}_{model_version}_h{horizon}"
            result = train_ensemble(
                X=X,
                y=y,
                algorithms=algo_list,
                weights=weight_list,
                output_dir=ensemble_path,
            )

            emit(f"  Trained {len(result['base_models'])} base models", json_output=json_output)
            emit(f"  Saved artifacts to {ensemble_path}", json_output=json_output)

            all_train_metrics[f"h{horizon}"] = result["metrics"]

        # Register model in ml_models
        from psycopg.types.json import Json

        base_artifact_path = out_dir / f"{model_name}_{model_version}"

        with conn.cursor() as cur:
            # Create run record
            cur.execute(
                """
                INSERT INTO ml_runs (run_type, status, dataset_id, run_config, started_at)
                VALUES ('train_ensemble', 'running', %s, %s, NOW())
                RETURNING id;
                """,
                (dataset["id"], Json({"algorithms": algo_list, "model_name": model_name, "weights": weight_list})),
            )
            run_id = int(cur.fetchone()[0])

            # Register model
            cur.execute(
                """
                INSERT INTO ml_models
                  (name, version, train_run_id, dataset_id, algorithm, hyperparams, metrics, artifact_uri)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name, version) DO UPDATE SET
                  train_run_id = EXCLUDED.train_run_id,
                  dataset_id = EXCLUDED.dataset_id,
                  algorithm = EXCLUDED.algorithm,
                  hyperparams = EXCLUDED.hyperparams,
                  metrics = EXCLUDED.metrics,
                  artifact_uri = EXCLUDED.artifact_uri
                RETURNING id;
                """,
                (
                    model_name,
                    model_version,
                    run_id,
                    dataset["id"],
                    "ensemble",
                    Json({"algorithms": algo_list, "weights": weight_list or [1.0/len(algo_list)]*len(algo_list)}),
                    Json(all_train_metrics),
                    str(base_artifact_path),
                ),
            )
            model_id = int(cur.fetchone()[0])

            # Mark run as complete
            cur.execute(
                """
                UPDATE ml_runs SET status = 'completed', finished_at = NOW()
                WHERE id = %s;
                """,
                (run_id,),
            )

        conn.commit()

    emit(
        f"Ensemble trained: {model_name} {model_version}",
        data={
            "model_id": model_id,
            "run_id": run_id,
            "artifact_uri": str(base_artifact_path),
            "horizons": horizons,
            "algorithms": algo_list,
        },
        json_output=json_output,
    )


@ml_app.command("predict-ensemble")
def ml_predict_ensemble(
    model_name: str = typer.Option(..., help="Ensemble model name"),
    model_version: str = typer.Option(..., help="Model version"),
    prediction_date: Optional[str] = typer.Option(None, help="Date to generate predictions for (YYYY-MM-DD). Auto-detects latest date with features if not provided."),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="Start date for batch predictions (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date for batch predictions (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbol list (optional)"),
    exchange: Optional[str] = typer.Option(None, help="Exchange name for universe selection (optional)"),
    limit: Optional[int] = typer.Option(None, help="Optional universe limit (exchange mode)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Generate predictions using a trained ensemble model.

    Examples:
        # Generate predictions for specific symbols (auto-detect date)
        gefion ml predict-ensemble --model-name tech_ensemble --model-version v1 --symbols AAPL,MSFT,GOOGL

        # Generate predictions for NASDAQ universe with explicit date
        gefion ml predict-ensemble --model-name nasdaq_ensemble --model-version v1 \\
            --prediction-date 2025-01-15 --exchange NASDAQ --limit 50

        # Generate predictions for a date range (batch backfill)
        gefion ml predict-ensemble --model-name nasdaq_ensemble --model-version v1 \\
            --start-date 2025-01-01 --end-date 2025-01-31 --exchange NASDAQ --limit 50
    """
    import pandas as pd
    from datetime import datetime, timedelta
    from gefion.ml.ensemble import load_ensemble, predict_ensemble
    from gefion.ml.store import get_ml_dataset

    sym_list = parse_comma_separated(symbols) or []
    if not sym_list and not exchange:
        emit_error("Universe required: provide --symbols or --exchange", json_output=json_output)
        return

    # Handle date range vs single date
    if start_date and end_date:
        # Date range mode
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            emit_error("Invalid date format. Use YYYY-MM-DD", json_output=json_output)
            return

        if start_dt > end_dt:
            emit_error("start-date must be before end-date", json_output=json_output)
            return

        # Generate list of dates
        prediction_dates = []
        current = start_dt
        while current <= end_dt:
            prediction_dates.append(current.isoformat())
            current += timedelta(days=1)
        emit(f"Batch prediction mode: {len(prediction_dates)} dates from {start_date} to {end_date}", json_output=json_output)
    elif prediction_date:
        prediction_dates = [prediction_date]
    else:
        # Will auto-detect later
        prediction_dates = [None]

    with db_connection(db_url) as conn:
        # Fetch model metadata
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset_id, artifact_uri, algorithm, hyperparams
                FROM ml_models
                WHERE name = %s AND version = %s;
                """,
                (model_name, model_version),
            )
            row = cur.fetchone()
            if not row:
                emit_error(f"Model not found: {model_name} {model_version}", json_output=json_output)
                return

            model_id, dataset_id, artifact_uri, algorithm, hyperparams = row[0], row[1], row[2], row[3], row[4]

        if algorithm != "ensemble":
            emit_error(f"Model is not an ensemble (algorithm={algorithm}). Use 'ml predict' instead.", json_output=json_output)
            return

        # Get dataset to know which features and horizons to use
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, version, feature_names, horizons_days
                FROM ml_datasets
                WHERE id = %s;
                """,
                (dataset_id,),
            )
            row = cur.fetchone()
            if not row:
                emit_error(f"Dataset not found for model (id={dataset_id})", json_output=json_output)
                return
            dataset_name, dataset_version, feature_names, horizons = row[0], row[1], row[2], row[3]

        # Build universe of symbols
        # Note: exchange param is stored for documentation but stocks table has no exchange column
        if exchange or (not sym_list and limit):
            with conn.cursor() as cur:
                limit_clause = f"LIMIT {limit}" if limit else ""
                cur.execute(
                    f"""
                    SELECT DISTINCT s.id, s.symbol
                    FROM stocks s
                    ORDER BY s.symbol
                    {limit_clause};
                    """
                )
                universe = [(row[0], row[1]) for row in cur.fetchall()]
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, symbol FROM stocks WHERE symbol = ANY(%s);
                    """,
                    (sym_list,),
                )
                universe = [(row[0], row[1]) for row in cur.fetchall()]

        if not universe:
            emit_error("No symbols found in universe", json_output=json_output)
            return

        data_ids = [u[0] for u in universe]

        # Auto-detect prediction date if not provided (single date mode with None)
        if prediction_dates == [None]:
            with conn.cursor() as cur:
                # Find the latest date that has features for these symbols
                cur.execute(
                    """
                    SELECT MAX(cf.date)
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND fd.name = ANY(%s);
                    """,
                    (data_ids, feature_names),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    emit_error(f"No features found for symbols. Ensure data-update has been run.", json_output=json_output)
                    return
                prediction_dates = [row[0].isoformat()]
                emit(f"Auto-detected prediction date: {prediction_dates[0]}", json_output=json_output)

        # Process each prediction date
        grand_total_predictions = 0
        dates_processed = 0
        dates_skipped = 0

        for pred_date in prediction_dates:
            emit(f"Generating ensemble predictions for {len(universe)} symbols on {pred_date}", json_output=json_output)

            # Fetch features for all symbols on pred_date
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cf.data_id, fd.name, cf.value
                    FROM computed_features cf
                    JOIN feature_definitions fd ON cf.feature_id = fd.id
                    WHERE cf.data_id = ANY(%s)
                      AND cf.date = %s
                      AND fd.name = ANY(%s);
                    """,
                    (data_ids, pred_date, feature_names),
                )
                features_data = cur.fetchall()

            if not features_data:
                # Skip this date in batch mode, error in single date mode
                if len(prediction_dates) > 1:
                    emit(f"  Skipping {pred_date}: no features available", json_output=json_output)
                    dates_skipped += 1
                    continue
                else:
                    # Find latest available date to help user
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT MAX(date) FROM computed_features WHERE data_id = ANY(%s)",
                            (data_ids,),
                        )
                        row = cur.fetchone()
                        latest_date = row[0] if row else None

                    if latest_date:
                        emit_error(
                            f"No features found for {pred_date}. "
                            f"Latest available: {latest_date}. "
                            f"Run 'gefion data-update' to compute features for more recent dates.",
                            json_output=json_output,
                        )
                    else:
                        emit_error(
                            f"No features found for {pred_date}. "
                            f"Run 'gefion data-update' to compute features first.",
                            json_output=json_output,
                        )
                    return

            # Convert to DataFrame and pivot to wide format
            features_df = pd.DataFrame(features_data, columns=["data_id", "feature_name", "value"])
            features_wide = features_df.pivot_table(
                index="data_id",
                columns="feature_name",
                values="value",
                aggfunc="first"
            )

            emit(f"Loaded features: {features_wide.shape[0]} symbols x {features_wide.shape[1]} features", json_output=json_output)

            # Generate predictions for each horizon
            from psycopg.types.json import Json
            from decimal import Decimal

            # Create run record
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ml_runs (run_type, status, dataset_id, run_config, started_at)
                    VALUES ('predict_ensemble', 'running', %s, %s, NOW())
                    RETURNING id;
                    """,
                    (
                        dataset_id,
                        Json(
                            {
                                "model_name": model_name,
                                "model_version": model_version,
                                "prediction_date": pred_date,
                                "universe": {"symbols": sym_list} if sym_list else {"exchange": exchange},
                            }
                        ),
                    ),
                )
                run_id = int(cur.fetchone()[0])

            total_predictions = 0
            for horizon in horizons:
                emit(f"Predicting for {horizon}-day horizon...", json_output=json_output)

                # Load ensemble for this horizon
                # Ensemble artifacts are saved as {artifact_uri}_h{horizon} (sibling dirs, not subdirs)
                horizon_ensemble_path = Path(f"{artifact_uri}_h{horizon}")
                try:
                    ensemble = load_ensemble(horizon_ensemble_path)
                except FileNotFoundError:
                    emit(f"  Warning: Ensemble not found at {horizon_ensemble_path}, skipping", json_output=json_output)
                    continue

                # Generate predictions
                predictions = predict_ensemble(ensemble, features_wide)

                # Insert predictions into database
                with conn.cursor() as cur:
                    for data_id in predictions.index:
                        q10 = Decimal(str(predictions.loc[data_id, "q10"]))
                        q50 = Decimal(str(predictions.loc[data_id, "q50"]))
                        q90 = Decimal(str(predictions.loc[data_id, "q90"]))

                        cur.execute(
                            """
                            INSERT INTO predictions
                              (model_id, data_id, prediction_date, horizon_days,
                               prediction_type, prediction_values, metadata, run_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (model_id, data_id, prediction_date, horizon_days, prediction_type)
                            DO UPDATE SET
                              prediction_values = EXCLUDED.prediction_values,
                              metadata = EXCLUDED.metadata,
                              run_id = EXCLUDED.run_id,
                              created_at = NOW();
                            """,
                            (model_id, int(data_id), pred_date, horizon,
                             'quantile',
                             Json({"q10": float(q10), "q50": float(q50), "q90": float(q90)}),
                             Json({"model_version": model_version}),
                             run_id),
                        )
                        total_predictions += 1

                emit(f"  Stored {len(predictions)} predictions for {horizon}-day horizon", json_output=json_output)

            # Mark run as complete
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ml_runs SET status = 'completed', finished_at = NOW()
                    WHERE id = %s;
                    """,
                    (run_id,),
                )

            conn.commit()
            grand_total_predictions += total_predictions
            dates_processed += 1

    # Summary
    if len(prediction_dates) > 1:
        emit(f"Batch complete: {dates_processed} dates processed, {dates_skipped} skipped, {grand_total_predictions} total predictions", json_output=json_output)
        emit(
            f"Ensemble predictions generated: {model_name} {model_version}",
            data={"model_id": model_id, "dates_processed": dates_processed, "dates_skipped": dates_skipped, "total_predictions": grand_total_predictions, "horizons": horizons},
            json_output=json_output,
        )
    else:
        emit(f"Generated {grand_total_predictions} predictions", json_output=json_output)
        emit(
            f"Ensemble predictions generated: {model_name} {model_version} for {prediction_dates[0]}",
            data={"model_id": model_id, "run_id": run_id, "prediction_date": prediction_dates[0], "total_predictions": grand_total_predictions, "horizons": horizons},
            json_output=json_output,
        )


@ml_app.command("e2e-test")
def ml_e2e_test(
    exchange: str = typer.Option("NASDAQ", help="Exchange to test with"),
    limit: int = typer.Option(10, help="Number of symbols to use (default: 10 for fast testing)"),
    name: str = typer.Option("e2e_test", help="Test name prefix for artifacts"),
    skip_data_update: bool = typer.Option(False, "--skip-data-update", help="Skip data update step"),
    cleanup: bool = typer.Option(False, "--cleanup", help="Remove test artifacts after completion"),
    db_url: Optional[str] = typer.Option(None, help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output as JSON"),
) -> None:
    """
    Run end-to-end ML pipeline test.

    This command runs the full ML pipeline to validate system functionality:
    1. Data Update - Fetch price data from AlphaVantage
    2. Dataset Build - Create ML dataset with features and labels
    3. Train Model - Train single XGBoost model
    4. Train Ensemble - Train ensemble combining XGBoost and LightGBM
    5. Predict - Generate predictions with single model
    6. Predict Ensemble - Generate predictions with ensemble

    Examples:
        # Quick smoke test with defaults (10 NASDAQ symbols)
        gefion ml e2e-test

        # Test with more symbols
        gefion ml e2e-test --limit 50

        # Test on NYSE with cleanup
        gefion ml e2e-test --exchange NYSE --limit 20 --cleanup

        # Skip data update (if data is already fresh)
        gefion ml e2e-test --skip-data-update
    """
    from gefion.ml.e2e import run_e2e_test, E2E_STEPS

    emit(f"Starting E2E ML pipeline test", json_output=json_output)
    emit(f"Exchange: {exchange}, Limit: {limit}, Name: {name}", json_output=json_output)
    emit("", json_output=json_output)

    # Show steps
    emit("Pipeline steps:", json_output=json_output)
    for i, (step_name, step_desc) in enumerate(E2E_STEPS.items(), 1):
        status = "[SKIP]" if step_name == "data_update" and skip_data_update else ""
        emit(f"  {i}. {step_desc} {status}", json_output=json_output)
    emit("", json_output=json_output)

    # Progress callback to show step status
    step_num = {"current": 0}
    def progress_callback(step: str, status: str, message: str = "") -> None:
        step_num["current"] += 1 if status == "starting" else 0
        step_idx = step_num["current"]
        if status == "starting":
            emit(f"[{step_idx}/6] {message}", json_output=json_output)
        elif status == "completed":
            detail = f" - {message}" if message else ""
            emit(f"  ✓ {step} completed{detail}", json_output=json_output)
        elif status == "failed":
            emit(f"  ✗ {step} FAILED", json_output=json_output, error=True)
        elif status == "skipped":
            emit(f"  - {step} skipped", json_output=json_output)

    url = _db_url(db_url)
    try:
        with db_connection(url) as conn:
            result = run_e2e_test(
                exchange=exchange,
                limit=limit,
                name=name,
                skip_data_update=skip_data_update,
                cleanup=cleanup,
                conn=conn,
                progress_callback=progress_callback,
            )
    except Exception as e:
        emit("", json_output=json_output)
        emit_error(f"E2E Test FAILED with exception: {e}", json_output=json_output)
        import traceback
        emit(traceback.format_exc(), json_output=json_output)
        raise typer.Exit(code=1)

    # Report results
    if result.success:
        emit("", json_output=json_output)
        emit("E2E Test PASSED", json_output=json_output)
        emit(f"Duration: {result.duration_seconds}s", json_output=json_output)
        emit(f"Steps completed: {len(result.steps_completed)}/{len(E2E_STEPS)}", json_output=json_output)

        if result.artifacts:
            emit("", json_output=json_output)
            emit("Artifacts created:", json_output=json_output)
            for key, value in result.artifacts.items():
                emit(f"  {key}: {value}", json_output=json_output)

        if json_output:
            emit("", data=result.to_dict(), json_output=json_output)
    else:
        emit("", json_output=json_output)
        emit("E2E Test FAILED", json_output=json_output, error=True)
        emit(f"Duration: {result.duration_seconds}s", json_output=json_output)
        emit(f"Steps completed: {result.steps_completed}", json_output=json_output)
        emit(f"Steps failed: {result.steps_failed}", json_output=json_output)

        if result.errors:
            emit("", json_output=json_output)
            emit("Errors:", json_output=json_output)
            for step, error in result.errors.items():
                emit(f"  {step}: {error}", json_output=json_output)

        if json_output:
            emit("", data=result.to_dict(), json_output=json_output)
        raise typer.Exit(code=1)


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
    """
    Ingest daily adjusted prices from AlphaVantage.

    If --input is provided, load from file; otherwise fetch via API.

    Examples:
        # Fetch latest prices for AAPL from API
        gefion prices-ingest --symbol AAPL

        # Ingest from a local JSON file
        gefion prices-ingest --symbol AAPL --input prices.json

        # Fetch full history and refresh existing data
        gefion prices-ingest --symbol MSFT --timeframe full --refresh-existing
    """
    with create_span("cli.prices-ingest", symbol=symbol, timeframe=timeframe):
        if input:
            payload = json.loads(input.read_text())
            rows = parse_daily_adjusted(symbol=symbol, payload=payload)
            if not rows:
                emit("No rows parsed; nothing to ingest.", json_output=json_output, error=True)
                raise typer.Exit(code=1)
            try:
                with db_connection(db_url) as conn:
                    init_schema_tables(conn, ["stocks", "stock_ohlcv"])
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
            url = _db_url(db_url)
            try:
                client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key)
            except ValueError as exc:
                emit(str(exc), json_output=json_output, error=True)
                raise typer.Exit(code=2)
            reporter = ProgressReporter(total=1, json_output=json_output, enabled=not json_output)
            reporter.mode = "api"

            # Calculate target date to prevent inserting partial/future data
            from gefion.ingest.universe import _expected_market_date
            target_date = _expected_market_date()

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
                    target_date=target_date,
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
    """
    Fetch listing status and ingest prices for the filtered universe.

    Examples:
        # Ingest all active NASDAQ stocks (limited to first 10 for testing)
        gefion universe-ingest --exchange NASDAQ --limit 10

        # Full refresh of NYSE universe with custom rate limit
        gefion universe-ingest --exchange NYSE --refresh --calls-per-minute 75

        # Ingest from a saved listings file
        gefion universe-ingest --exchange NASDAQ --listings-file listings.csv
    """
    with create_span("cli.universe-ingest", exchange=exchange, timeframe=timeframe):
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

        # Calculate target date to prevent inserting partial/future data
        from gefion.ingest.universe import _expected_market_date, filter_symbols_needing_update
        target_date = _expected_market_date()

        # Do bulk filtering ONCE for all symbols before chunking
        # This is much faster than filtering each chunk separately
        symbols_before = len(symbols)
        skipped = 0
        if not update_existing:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["stocks", "stock_ohlcv"])
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
                    target_date=target_date,
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
    migrations_dir: Optional[Path] = typer.Option(None, help="Migrations directory (default: sql/migrations)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Report database health: connections, tables, indexes, and migration status.

    Checks database configuration, table presence, chunk intervals, BRIN indexes,
    compression status, and pending migrations.
    """
    with create_span("cli.db-health"):
        _db_health_impl(db_url, migrations_dir, json_output)


def _db_health_impl(db_url, migrations_dir, json_output):
    """Implementation of db-health (separated for tracing)."""
    from gefion.db.migrate import check_pending_migrations, get_applied_migrations
    from pathlib import Path as PathLib
    import gefion

    url = _db_url(db_url)

    # Find migrations directory
    if migrations_dir is None:
        package_dir = PathLib(gefion.__file__).parent.parent.parent
        migrations_dir = package_dir / "sql" / "migrations"

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
                        SELECT h.hypertable_name, d.time_interval
                        FROM timescaledb_information.hypertables h
                        LEFT JOIN timescaledb_information.dimensions d
                          ON h.hypertable_name = d.hypertable_name
                        WHERE h.hypertable_name = ANY(%s) AND (d.time_interval IS NOT NULL OR d.column_name = 'date');
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

                # Check migration status
                migration_status = {}
                try:
                    # Check if schema_migrations table exists
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables
                            WHERE table_name = 'schema_migrations'
                        );
                    """)
                    has_migrations_table = cur.fetchone()[0]
                    migration_status["migrations_table_exists"] = has_migrations_table

                    # Check for pending migrations (this will internally get applied migrations)
                    if migrations_dir.exists():
                        pending = check_pending_migrations(conn, migrations_dir)
                        # Calculate applied count from migration files
                        from gefion.db.migrate import scan_migration_files
                        all_migrations = scan_migration_files(migrations_dir)
                        migration_status["applied_count"] = len(all_migrations) - len(pending)
                        migration_status["pending_count"] = len(pending)
                        migration_status["pending_list"] = [
                            {"version": m["version"], "name": m["name"]}
                            for m in pending
                        ]
                    else:
                        # No migrations directory - check DB for applied count
                        if has_migrations_table:
                            from gefion.db.migrate import get_applied_migrations
                            applied = get_applied_migrations(conn)
                            migration_status["applied_count"] = len(applied)
                        else:
                            migration_status["applied_count"] = 0
                        migration_status["pending_count"] = 0
                        migration_status["pending_list"] = []

                    # Check compression status
                    try:
                        cur.execute("""
                            SELECT hypertable_name, compression_enabled
                            FROM timescaledb_information.hypertables
                            WHERE hypertable_name IN ('stock_ohlcv', 'computed_features');
                        """)
                        compression_info = cur.fetchall()
                        if compression_info:
                            enabled_count = sum(1 for _, enabled in compression_info if enabled)
                            migration_status["compression_status"] = f"{enabled_count}/{len(compression_info)} hypertables"
                        else:
                            migration_status["compression_status"] = "no hypertables"
                    except Exception:
                        migration_status["compression_status"] = "unavailable"

                except Exception as e:
                    migration_status["error"] = str(e)

                health["migrations"] = migration_status

    except Exception as exc:
        emit_error(f"DB health failed: {exc}", json_output=json_output)
        return

    # Enhanced output
    if not json_output:
        emit("Database Health Report:")
        emit(f"  Connections: {health.get('available_connections', 'unknown')}")

        tables = health.get("tables", {})
        for table, exists in tables.items():
            status = "✓" if exists else "✗"
            emit(f"  Table {table}: {status}")

        migrations = health.get("migrations", {})
        if migrations:
            applied = migrations.get("applied_count", 0)
            pending = migrations.get("pending_count", 0)
            emit(f"  Applied migrations: {applied}")

            if pending > 0:
                emit(f"  ⚠️  Pending migrations: {pending}")
                for m in migrations.get("pending_list", []):
                    emit(f"      - {m['version']}_{m['name']}")
                emit("  Run 'gefion db-migrate' to apply pending migrations")
            else:
                emit(f"  ✓ Pending migrations: 0")

            compression = migrations.get("compression_status", "unknown")
            emit(f"  Compression: {compression}")

        emit("", data=health, json_output=False)
    else:
        emit("DB health", data=health, json_output=json_output)


def _span_check_impl(
    backend: str,
    tempo_url: Optional[str],
    service_name: Optional[str],
    limit: int,
    trace_id: Optional[str],
    show_spans: bool,
    json_output: Optional[bool],
) -> None:
    if backend != "tempo":
        emit_error(f"Unsupported backend: {backend}", json_output=json_output)

    tempo_url = tempo_url or os.getenv("TEMPO_URL", "http://localhost:3200")
    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "gefion")

    try:
        search = _tempo_get_json(
            tempo_url,
            "/api/search",
            params={"tags": f"service.name={service_name}", "limit": limit},
        )
    except Exception as exc:
        emit_error(
            f"Tempo search failed: {exc}",
            json_output=json_output,
            data={"hint": f"Ensure Tempo is running and reachable at {tempo_url} (docker compose -f docker/tempo/docker-compose.tempo.yml up -d)"},
        )

    traces = search.get("traces") or []
    trace_count = (search.get("metrics") or {}).get("inspectedTraces", 0)
    if not traces:
        emit(
            f"No traces found for service '{service_name}'",
            data={
                "tempo_url": tempo_url,
                "hint": "Generate traces with: export $(cat .env.example | xargs) && .venv/bin/python tests/test_otel_smoke.py",
            },
            json_output=json_output,
        )
        return

    selected_trace_id = trace_id or traces[0].get("traceID")
    if not selected_trace_id:
        emit_error("Tempo search returned traces without traceID", json_output=json_output)

    try:
        detail = _tempo_get_json(tempo_url, f"/api/traces/{selected_trace_id}")
    except Exception as exc:
        emit_error(f"Tempo trace fetch failed: {exc}", json_output=json_output)

    spans: list[dict] = []
    for batch in detail.get("batches") or []:
        for scope_spans in batch.get("scopeSpans") or []:
            scope_name = ((scope_spans.get("scope") or {}).get("name")) or ""
            for span in scope_spans.get("spans") or []:
                spans.append({"scope": scope_name, "span": span})

    def _is_error_status(status_code: object) -> bool:
        return status_code in ("STATUS_CODE_ERROR", 2, "2")

    app_span_count = sum(1 for s in spans if s["scope"] == "gefion.observability")
    db_span_count = sum(1 for s in spans if str(s["scope"]).startswith("opentelemetry.instrumentation"))
    error_count = sum(1 for s in spans if _is_error_status(((s["span"].get("status") or {}).get("code"))))

    result = {
        "backend": backend,
        "tempo_url": tempo_url,
        "service_name": service_name,
        "trace_count": trace_count,
        "selected_trace_id": selected_trace_id,
        "tempo_trace_api_url": f"{tempo_url.rstrip('/')}/api/traces/{selected_trace_id}",
        "total_spans": len(spans),
        "application_spans": app_span_count,
        "database_spans": db_span_count,
        "error_spans": error_count,
        "recent_traces": [
            {
                "rootTraceName": t.get("rootTraceName"),
                "durationMs": t.get("durationMs"),
                "traceID": t.get("traceID"),
            }
            for t in traces[:limit]
        ],
    }

    if json_output:
        emit_json({"status": "ok", **result})
        return

    console = Console()
    console.print("Span check", style="bold")
    console.print(f"Backend: {backend}", style="dim")
    console.print(f"Tempo URL: {tempo_url}", style="dim")
    console.print(f"Service: {service_name}", style="dim")
    console.print(f"Traces found: {trace_count}", style="dim")
    console.print("")
    console.print("Recent traces:", style="bold")
    for t in traces[: min(limit, len(traces))]:
        tid = (t.get("traceID") or "")[:16]
        console.print(f"  {t.get('rootTraceName')} - {t.get('durationMs')}ms (trace_id: {tid}...)", style="dim")

    console.print("")
    console.print(f"Selected trace: {selected_trace_id}", style="bold")
    console.print(f"Tempo trace API: {tempo_url.rstrip('/')}/api/traces/{selected_trace_id}", style="dim")
    console.print(f"Total spans: {len(spans)}", style="dim")
    console.print(f"Application spans (gefion.observability): {app_span_count}", style="dim")
    console.print(f"Database spans (auto-instrumented): {db_span_count}", style="dim")
    console.print(f"Error spans: {error_count}", style="dim")

    if show_spans:
        console.print("")
        console.print("Spans:", style="bold")
        for s in spans:
            span = s["span"]
            start = int(span.get("startTimeUnixNano") or 0)
            end = int(span.get("endTimeUnixNano") or 0)
            duration_ms = round((end - start) / 1_000_000) if end and start else 0
            has_parent = bool(span.get("parentSpanId"))
            attrs = span.get("attributes") or []
            prefix = "  └─" if has_parent else "┌─"
            console.print(f"  {prefix} {span.get('name')} ({duration_ms}ms, {len(attrs)} attrs) [{s['scope']}]", style="dim")

    console.print("")
    console.print("View in Grafana: http://localhost:3000/explore", style="dim")
    console.print('Query: service.name = "gefion"', style="dim")


@app.command("span-check")
def span_check(
    backend: str = typer.Option("tempo", help="Trace backend (default: tempo)"),
    tempo_url: Optional[str] = typer.Option(None, help="Tempo base URL (default: $TEMPO_URL or http://localhost:3200)"),
    service_name: Optional[str] = typer.Option(None, help="Service name tag (default: $OTEL_SERVICE_NAME or gefion)"),
    limit: int = typer.Option(10, min=1, max=100, help="Number of recent traces to inspect"),
    trace_id: Optional[str] = typer.Option(None, help="Specific trace ID to inspect (default: most recent)"),
    show_spans: bool = typer.Option(True, "--show-spans/--no-show-spans", help="Print a span list"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Check recent traces in the configured backend (Tempo by default)."""
    with create_span("cli.span-check"):
        otel_enabled = os.getenv("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
        if not otel_enabled:
            emit(
                "OTEL_ENABLED is not true; traces may be missing.",
                data={"hint": "export $(cat .env.example | xargs)"},
                json_output=json_output,
            )
        _span_check_impl(backend, tempo_url, service_name, limit, trace_id, show_spans, json_output)


@app.command("health")
def health_check(
    service: Optional[str] = typer.Option(None, help="Check specific service (postgres, tempo, docker)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Check health of Gefion infrastructure services.

    Checks PostgreSQL, Tempo, and Docker availability with helpful error messages
    and suggestions for fixing issues.

    Examples:
        # Check all services
        gefion health

        # Check specific service
        gefion health --service postgres

        # JSON output
        gefion health --json
    """
    with create_span("cli.health"):
        from gefion.output import get_output
        out = get_output(json_output)

        if service:
            # Check specific service
            service_lower = service.lower()
            if service_lower == "postgres":
                status = health.check_postgres_health()
            elif service_lower == "tempo":
                status = health.check_tempo_health()
            elif service_lower == "docker":
                status = health.check_docker_services()
            else:
                out.error(f"Unknown service: {service}. Valid options: postgres, tempo, docker")
                raise typer.Exit(code=1)

            if out.json_mode:
                out.json({"status": "ok" if status["running"] else "error", "service": service_lower, **status})
            else:
                status_icon = "✓" if status["running"] else "✗"
                style = "bold green" if status["running"] else "bold red"
                out.console.print(f"\n{status_icon} {service_lower.upper()}: {status['message']}\n", style=style)

                if not status["running"] and "suggestion" in status:
                    out.console.print(f"   → {status['suggestion']}\n")
                elif status["running"] and "version" in status:
                    out.console.print(f"   Version: {status['version']}\n")
        else:
            # Check all services
            all_status = health.check_all_services()

            if out.json_mode:
                out.json({"status": "ok", "services": all_status})
            else:
                report = health.format_health_report(all_status)
                out.console.print(report)


@app.command("init")
def init(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Initialize Gefion — the single command to get a working system.

    Sets up the database schema, runs migrations, imports feature functions
    and definitions from git, seeds strategies, and verifies infrastructure
    health. Safe to run multiple times (idempotent).

    Examples:
        # Full initialization
        gefion init

        # JSON output
        gefion init --json
    """
    with create_span("cli.init"):
        if not json_output:
            emit("=== gefion init ===")
        _db_init_impl(db_url, json_output)
        if not json_output:
            emit("")
            emit("=== Health Check ===")
        all_status = health.check_all_services()
        if json_output:
            emit("", data={"health": all_status}, json_output=True)
        else:
            report = health.format_health_report(all_status)
            from gefion.output import get_output
            out = get_output(json_output)
            out.console.print(report)


@app.command("db-init")
def db_init(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Initialize database schema from sql/schema.sql.

    Creates all tables, hypertables, and indexes. Safe to run multiple times (idempotent).

    Examples:
        # Initialize database with default connection
        gefion db-init

        # Initialize with custom database URL
        gefion db-init --db-url postgresql://user:pass@localhost:5432/mydb

        # Check results in JSON format
        gefion db-init --json
    """
    with create_span("cli.db-init"):
        _db_init_impl(db_url, json_output)


def _db_init_impl(db_url, json_output):
    """Implementation of db-init (separated for tracing)."""
    url = _db_url(db_url)

    # Find the schema.sql file relative to the package
    try:
        import gefion
        package_dir = Path(gefion.__file__).parent.parent.parent
        schema_path = package_dir / "sql" / "schema.sql"

        if not schema_path.exists():
            emit_error(f"Schema file not found at {schema_path}", json_output=json_output)
            return
    except Exception as exc:
        emit_error(f"Failed to locate schema file: {exc}", json_output=json_output)
        return

    try:
        if not json_output:
            emit("Initializing database schema...")

        # Read and execute schema SQL via psycopg (provides trace visibility)
        schema_sql = schema_path.read_text()

        # Filter out psql meta-commands (same pattern as migration system)
        lines = schema_sql.split('\n')
        filtered_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip psql meta-commands (lines starting with \)
            if stripped.startswith('\\'):
                continue
            filtered_lines.append(line)

        filtered_sql = '\n'.join(filtered_lines)

        # Execute schema SQL via psycopg connection (enables proper tracing)
        with db_connection(url) as conn:
            with conn.cursor() as cur:
                cur.execute(filtered_sql)
            conn.commit()

        emit(
            "Database initialized successfully",
            data={"schema_file": str(schema_path)},
            json_output=json_output
        )

        # Seed feature functions and definitions from JSON files
        fx_dir = package_dir / "feature-functions"
        def_dir = package_dir / "feature-definitions"

        fx_count = 0
        def_count = 0

        if fx_dir.exists():
            with db_connection(url) as conn:
                init_schema_tables(conn, ["feature_functions"])
                fx_count = import_functions_from_directory(conn, fx_dir, None)

        if def_dir.exists():
            with db_connection(url) as conn:
                init_schema_tables(conn, ["feature_definitions", "computed_features"])
                def_count = import_definitions_from_directory(conn, def_dir, None)

        if fx_count > 0 or def_count > 0:
            emit(
                f"Seeded {fx_count} feature function(s) and {def_count} feature definition(s)",
                json_output=json_output
            )

        # Seed built-in trading strategies
        from gefion.strategies.dispatcher import seed_builtin_strategies
        with db_connection(url) as conn:
            init_schema_tables(conn, ["strategy_registry"])
            strat_count = seed_builtin_strategies(conn)
            if strat_count > 0:
                emit(
                    f"Seeded {strat_count} trading strategy(ies)",
                    json_output=json_output
                )

        # Run migrations to ensure schema is up-to-date
        # This handles existing databases that may be missing new columns
        from gefion.db.migrate import run_migrations
        migrations_dir = package_dir / "sql" / "migrations"
        if migrations_dir.exists():
            with db_connection(url) as conn:
                result = run_migrations(conn, migrations_dir, dry_run=False)
                if result['applied'] > 0:
                    emit(
                        f"Applied {result['applied']} migration(s)",
                        json_output=json_output
                    )

    except Exception as exc:
        emit_error(f"Initialization failed: {exc}", json_output=json_output)


@app.command("db-cleanup")
def db_cleanup(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted without deleting"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Remove orphaned data from database tables.

    Cleans up data that references non-existent stocks (e.g., after stocks table was reset).
    This includes computed_features, stock_ohlcv, and predictions.

    Examples:
        # Preview what would be deleted
        gefion db-cleanup --dry-run

        # Remove orphaned data
        gefion db-cleanup
    """
    with create_span("cli.db-cleanup", dry_run=dry_run):
        url = _db_url(db_url)

        orphan_queries = [
            ("computed_features", "DELETE FROM computed_features WHERE data_id NOT IN (SELECT id FROM stocks)"),
            ("stock_ohlcv", "DELETE FROM stock_ohlcv WHERE data_id NOT IN (SELECT id FROM stocks)"),
            ("predictions", "DELETE FROM predictions WHERE data_id NOT IN (SELECT id FROM stocks)"),
        ]

        count_queries = [
            ("computed_features", "SELECT COUNT(*) FROM computed_features WHERE data_id NOT IN (SELECT id FROM stocks)"),
            ("stock_ohlcv", "SELECT COUNT(*) FROM stock_ohlcv WHERE data_id NOT IN (SELECT id FROM stocks)"),
            ("predictions", "SELECT COUNT(*) FROM predictions WHERE data_id NOT IN (SELECT id FROM stocks)"),
        ]

        try:
            with db_connection(url) as conn:
                total_orphans = 0
                results = {}

                # Count orphans first
                with conn.cursor() as cur:
                    for table_name, query in count_queries:
                        try:
                            cur.execute(query)
                            count = cur.fetchone()[0]
                            results[table_name] = count
                            total_orphans += count
                        except Exception:
                            results[table_name] = 0  # Table might not exist

                if total_orphans == 0:
                    emit("No orphaned data found", json_output=json_output)
                    return

                # Report findings
                emit(f"Found {total_orphans} orphaned record(s):", json_output=json_output)
                for table_name, count in results.items():
                    if count > 0:
                        emit(f"  {table_name}: {count}", json_output=json_output)

                if dry_run:
                    emit("Dry run - no data deleted", json_output=json_output)
                    return

                # Delete orphans
                deleted = {}
                with conn.cursor() as cur:
                    for table_name, query in orphan_queries:
                        if results.get(table_name, 0) > 0:
                            try:
                                cur.execute(query)
                                deleted[table_name] = cur.rowcount
                            except Exception as e:
                                emit(f"  Warning: Could not clean {table_name}: {e}", json_output=json_output)

                conn.commit()

                total_deleted = sum(deleted.values())
                emit(f"Deleted {total_deleted} orphaned record(s)", json_output=json_output)
                emit(
                    "Cleanup complete",
                    data={"deleted": deleted, "total": total_deleted},
                    json_output=json_output,
                )

        except Exception as exc:
            emit_error(f"Cleanup failed: {exc}", json_output=json_output)


@app.command("db-migrate")
def db_migrate(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    migrations_dir: Optional[Path] = typer.Option(None, help="Migrations directory (default: sql/migrations)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show pending migrations without applying"),
    status: bool = typer.Option(False, "--status", help="Show migration status"),
    verify: bool = typer.Option(False, "--verify", help="Verify applied migrations created expected schema objects"),
    repair: Optional[str] = typer.Option(None, "--repair", help="Repair a specific migration version (e.g., 20251227_000001)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Run database migrations from sql/migrations/ directory.

    Migrations are applied in order (001, 002, 003, etc.) and tracked
    in the schema_migrations table. Already-applied migrations are
    automatically skipped. Safe to run multiple times (idempotent).

    Examples:
        # Run all pending migrations
        gefion db-migrate

        # Show pending migrations without applying
        gefion db-migrate --dry-run

        # Show migration status
        gefion db-migrate --status

        # Verify applied migrations created expected schema
        gefion db-migrate --verify

        # Repair a failed migration
        gefion db-migrate --repair 20251227_000001

        # Run migrations on specific database
        gefion db-migrate --db-url postgresql://user:pass@host:5432/db

        # Use custom migrations directory
        gefion db-migrate --migrations-dir /path/to/migrations
    """
    with create_span("cli.db-migrate", dry_run=dry_run, status=status, verify=verify, repair=repair):
        _db_migrate_impl(db_url, migrations_dir, dry_run, status, verify, repair, json_output)


def _db_migrate_impl(db_url, migrations_dir, dry_run, status, verify, repair, json_output):
    """Implementation of db-migrate (separated for tracing)."""
    from gefion.db.migrate import (
        run_migrations,
        get_migration_status,
        scan_migration_files,
        parse_migration_schema_changes,
        verify_schema_objects,
        repair_migration,
        get_applied_migrations,
    )
    from pathlib import Path as PathLib
    import gefion

    url = _db_url(db_url)

    # Find migrations directory
    if migrations_dir is None:
        package_dir = PathLib(gefion.__file__).parent.parent.parent
        migrations_dir = package_dir / "sql" / "migrations"

    if not migrations_dir.exists():
        emit_error(
            f"Migrations directory not found: {migrations_dir}",
            json_output=json_output
        )
        return

    try:
        with psycopg.connect(url) as conn:
            conn.autocommit = True

            # Handle --status flag
            if status:
                result = get_migration_status(conn, migrations_dir)

                if json_output:
                    emit(
                        "Migration status retrieved",
                        data=result,
                        json_output=json_output
                    )
                else:
                    emit(f"Migration Status ({result['total']} total)")
                    emit("=" * 40)
                    emit("")
                    if result['applied']:
                        emit(f"Applied ({result['applied_count']}):")
                        for m in result['applied']:
                            applied_at = m.get('applied_at', 'unknown')
                            if hasattr(applied_at, 'strftime'):
                                applied_at = applied_at.strftime('%Y-%m-%d %H:%M:%S')
                            emit(f"  ✓ {m['version']}_{m['name']}  [applied {applied_at}]")
                    emit("")
                    if result['pending']:
                        emit(f"Pending ({result['pending_count']}):")
                        for m in result['pending']:
                            emit(f"  ○ {m['version']}_{m['name']}  [not applied]")
                        emit("")
                        emit("Run `gefion db-migrate` to apply pending migrations.")
                    else:
                        emit("No pending migrations.")
                    emit("")
                    emit("Run `gefion db-migrate --verify` to check applied migrations.")
                return

            # Handle --verify flag
            if verify:
                all_migrations = scan_migration_files(migrations_dir)
                applied = get_applied_migrations(conn)

                applied_migrations = [m for m in all_migrations if m['version'] in applied]
                issues = []

                if not json_output:
                    emit(f"Verifying {len(applied_migrations)} applied migrations...")
                    emit("")

                for m in applied_migrations:
                    sql = m['path'].read_text()
                    expected = parse_migration_schema_changes(sql)
                    missing = verify_schema_objects(conn, expected)

                    if missing:
                        issues.append({
                            'version': m['version'],
                            'name': m['name'],
                            'missing': missing
                        })
                        if not json_output:
                            emit(f"  ✗ {m['version']}_{m['name']}  [FAILED]")
                            for obj in missing:
                                if obj['type'] == 'column':
                                    emit(f"    - Missing column: {obj['table']}.{obj['name']}")
                                else:
                                    emit(f"    - Missing {obj['type']}: {obj['name']}")
                    else:
                        if not json_output:
                            emit(f"  ✓ {m['version']}_{m['name']}  [OK]")

                if json_output:
                    emit(
                        f"Verified {len(applied_migrations)} migrations",
                        data={
                            'verified': len(applied_migrations),
                            'issues_count': len(issues),
                            'issues': issues,
                        },
                        json_output=json_output
                    )
                else:
                    emit("")
                    if issues:
                        emit(f"{len(issues)} migration(s) have issues. Run:")
                        for issue in issues:
                            emit(f"  gefion db-migrate --repair {issue['version']}")
                    else:
                        emit("All migrations verified successfully.")
                return

            # Handle --repair flag
            if repair:
                if not json_output:
                    emit(f"Repairing migration {repair}...")

                result = repair_migration(conn, repair, migrations_dir)

                if result['success']:
                    if json_output:
                        emit(
                            "Migration repaired successfully",
                            data=result,
                            json_output=json_output
                        )
                    else:
                        emit(f"  - Removed from schema_migrations")
                        emit(f"  - Re-applying migration...")
                        emit(f"  ✓ Migration applied successfully")
                else:
                    if json_output:
                        emit_error(
                            f"Repair failed: {result.get('error', 'Unknown error')}",
                            json_output=json_output
                        )
                    else:
                        emit_error(f"  ✗ Repair failed: {result.get('error', 'Unknown error')}")
                    raise typer.Exit(code=1)
                return

            # Default: run migrations
            if not json_output:
                if dry_run:
                    emit("Checking for pending migrations...")
                else:
                    emit("Running database migrations...")

            result = run_migrations(conn, migrations_dir, dry_run=dry_run)

            # Format output
            if dry_run:
                total = len(result['migrations'])
                pending_count = len([m for m in result['migrations'] if m['status'] == 'pending'])
                message = f"Found {total} total migrations: {result['skipped']} already applied, {pending_count} pending"
            else:
                message = f"Migrations complete: {result['applied']} applied, {result['skipped']} skipped"

            emit(
                message,
                data={
                    "applied": result["applied"],
                    "skipped": result["skipped"],
                    "migrations": result["migrations"],
                    "migrations_dir": str(migrations_dir),
                    "dry_run": dry_run
                },
                json_output=json_output
            )

    except Exception as exc:
        emit_error(f"Migration failed: {exc}", json_output=json_output)
        raise typer.Exit(code=1)


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
    with create_span(
        "cli.db-tune",
        chunk_days=chunk_days,
        compress_after_days=compress_after_days,
    ):
        _db_tune_impl(db_url, chunk_days, compress_after_days, show_chunk_ranges, json_output)


def _db_tune_impl(db_url, chunk_days, compress_after_days, show_chunk_ranges, json_output):
    """Implementation of db-tune (separated for tracing)."""
    from gefion.utils.timescale import get_chunk_date_range

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


@app.command("backup")
def backup_data(
    output: Path = typer.Option(..., "-o", "--output", help="Output directory path"),
    data_types: str = typer.Option(
        "all",
        "--data-types",
        help="Comma-separated data types: ohlcv, features, definitions, functions, strategies, ml, predictions, experiments, meta, all",
    ),
    start_date: Optional[str] = typer.Option(None, "--start-date", "--after", help="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", "--before", help="End date (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbols to backup"),
    incremental: bool = typer.Option(False, "--incremental", help="Only backup data since last backup"),
    compress: bool = typer.Option(True, "--compress/--no-compress", help="Compress output files"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show size estimate without creating backup"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Backup database data to parquet files.

    Creates a backup directory with parquet files for each table
    and a manifest.json with metadata.

    Examples:

        # Full backup
        gefion backup --output ./backups/full_backup

        # Backup only OHLCV data for specific symbols
        gefion backup -o ./backups/prices --data-types ohlcv --symbols AAPL,MSFT

        # Backup with date range
        gefion backup -o ./backups/2024 --start-date 2024-01-01 --end-date 2024-12-31

        # Show size estimate without creating backup
        gefion backup -o ./backups/test --dry-run

        # Incremental backup (only new data since last backup)
        gefion backup -o ./backups/incremental --incremental
    """
    with create_span("cli.backup", data_types=data_types, dry_run=dry_run):
        _backup_impl(
            output, data_types, start_date, end_date, symbols,
            incremental, compress, dry_run, db_url, json_output
        )


def _backup_impl(
    output, data_types, start_date, end_date, symbols,
    incremental, compress, dry_run, db_url, json_output
):
    """Implementation of backup command."""
    from datetime import datetime
    from gefion.backup import (
        estimate_backup_size, check_disk_space, create_backup,
        get_last_backup_info
    )

    url = _db_url(db_url)

    # Parse data types
    types_list = [t.strip() for t in data_types.split(",")]

    # Parse dates
    parsed_start = None
    parsed_end = None
    if start_date:
        parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Parse symbols
    symbols_list = None
    if symbols:
        symbols_list = [s.strip().upper() for s in symbols.split(",")]

    # Get last backup info for incremental
    last_backup_date = None
    if incremental:
        last_info = get_last_backup_info(str(output.parent) if output.parent.exists() else str(output))
        if last_info:
            last_backup_date = last_info.get("created_at")
            if not json_output:
                typer.echo(f"Last backup: {last_backup_date}")

    try:
        with psycopg.connect(url) as conn:
            # Estimate size
            estimate = estimate_backup_size(
                conn,
                data_types=types_list,
                start_date=parsed_start,
                end_date=parsed_end,
                symbols=symbols_list,
            )

            if dry_run:
                # Just show estimate
                size_mb = estimate["total_bytes"] / (1024 * 1024)
                emit(
                    f"Backup estimate: {estimate['total_rows']:,} rows, {size_mb:.1f} MB",
                    data={"estimate": estimate, "dry_run": True},
                    json_output=json_output,
                )
                return

            # Check disk space
            if not check_disk_space(str(output), estimate["total_bytes"]):
                emit_error(
                    f"Insufficient disk space. Need ~{estimate['total_bytes'] / (1024*1024):.1f} MB",
                    json_output=json_output,
                )
                return

            # Create backup
            result = create_backup(
                conn=conn,
                output_path=str(output),
                data_types=types_list,
                start_date=parsed_start,
                end_date=parsed_end,
                symbols=symbols_list,
                incremental=incremental,
                last_backup_date=last_backup_date,
                compress=compress,
            )

            size_mb = result["total_bytes"] / (1024 * 1024)
            emit(
                f"Backup complete: {result['total_rows']:,} rows, {size_mb:.1f} MB",
                data=result,
                json_output=json_output,
            )

    except Exception as exc:
        emit_error(f"Backup failed: {exc}", json_output=json_output)


@app.command("restore")
def restore_data(
    input_path: Path = typer.Option(..., "-i", "--input", help="Input backup directory path"),
    mode: str = typer.Option("merge", "--mode", help="Restore mode: merge (skip conflicts) or replace"),
    data_types: Optional[str] = typer.Option(None, "--data-types", help="Filter data types to restore"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be restored without restoring"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Verify backup integrity before restoring"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Restore database data from a backup.

    Reads parquet files from a backup directory and imports them
    into the database.

    Examples:

        # Restore all data (merge mode - skip conflicts)
        gefion restore --input ./backups/full_backup

        # Restore with replace mode (overwrite existing)
        gefion restore -i ./backups/full_backup --mode replace

        # Restore only OHLCV data
        gefion restore -i ./backups/full_backup --data-types ohlcv

        # Preview what would be restored
        gefion restore -i ./backups/full_backup --dry-run
    """
    with create_span("cli.restore", mode=mode, dry_run=dry_run):
        _restore_impl(input_path, mode, data_types, dry_run, verify, db_url, json_output)


def _restore_impl(input_path, mode, data_types, dry_run, verify, db_url, json_output):
    """Implementation of restore command."""
    from gefion.backup import restore_backup, verify_backup
    import json as json_lib

    url = _db_url(db_url)

    # Verify backup if requested
    if verify:
        verify_result = verify_backup(str(input_path))
        if not verify_result.get("valid"):
            emit_error(
                f"Backup verification failed: {verify_result.get('error', 'Unknown error')}",
                data=verify_result,
                json_output=json_output,
            )
            return

        if not json_output:
            typer.echo("Backup verified successfully")

    # Read manifest for dry run info
    manifest_path = input_path / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json_lib.load(f)
    else:
        emit_error("No manifest.json found in backup directory", json_output=json_output)
        return

    if dry_run:
        # Show what would be restored
        tables_info = manifest.get("tables", {})
        total_rows = sum(t.get("rows", 0) for t in tables_info.values())

        emit(
            f"Would restore {total_rows:,} rows from {len(tables_info)} tables",
            data={"tables": tables_info, "mode": mode, "dry_run": True},
            json_output=json_output,
        )
        return

    # Parse data types filter
    types_list = None
    if data_types:
        types_list = [t.strip() for t in data_types.split(",")]

    try:
        with psycopg.connect(url) as conn:
            result = restore_backup(
                conn=conn,
                input_path=str(input_path),
                mode=mode,
                data_types=types_list,
            )

            emit(
                f"Restore complete: {result['total_rows']:,} rows restored",
                data=result,
                json_output=json_output,
            )

    except Exception as exc:
        emit_error(f"Restore failed: {exc}", json_output=json_output)


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


@app.command("feat-fx-list")
def list_functions(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
    feature: Optional[str] = typer.Option(None, "--feature", help="Optional function name to filter"),
    show_body: bool = typer.Option(False, "--show-body/--no-show-body", help="Include function_body in output"),
) -> None:
    """List registered feature functions."""
    with create_span("cli.feat-fx-list"):
        from gefion.output import Column, get_output

        out = get_output(json_output)

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_functions"])
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

            if not data:
                out.warning("No feature functions found")
                if out.json_mode:
                    out.json({"functions": [], "count": 0})
                return

            if out.json_mode:
                out.json({"status": "ok", "functions": data, "count": len(data)})
                return

            if show_body:
                for d in data:
                    header = f"[bold]{d['name']}[/bold] v{d['version']} ({d['status']})"
                    header += f" [{'ENABLED' if d['enabled'] else 'DISABLED'}]"
                    header += f" [{d['language']}]"
                    out.console.print(header)
                    if d.get("tags"):
                        out.console.print(f"tags: {', '.join(d['tags'])}", style="blue")
                    if d.get("updated_at"):
                        out.console.print(f"updated: {d['updated_at']}", style="dim")
                    if d.get("description"):
                        out.console.print(d["description"])
                    body = d.get("function_body") or ""
                    out.console.print(body, style="cyan")
                    out.console.print()
            else:
                out.table(
                    columns=[
                        Column("Name", style="white", json_key="name"),
                        Column("Version", style="magenta", json_key="version"),
                        Column("Status", style="green", json_key="status"),
                        Column("Enabled", style="yellow", json_key="enabled"),
                        Column("Language", style="cyan", json_key="language"),
                        Column("Tags", style="blue", json_key="tags"),
                        Column("Updated", style="dim", json_key="updated_at"),
                    ],
                    rows=[
                        [
                            d["name"] or "",
                            d["version"] or "",
                            d["status"] or "",
                            str(d["enabled"]),
                            d["language"] or "",
                            ",".join(d["tags"]) if d.get("tags") else "",
                            d["updated_at"] or "",
                        ]
                        for d in data
                    ],
                    title="Feature Functions",
                    data_key="functions",
                    json_data=data,
                )
        except Exception as exc:
            out.error(f"List functions failed: {exc}")
            raise typer.Exit(code=1)


@app.command("feat-fx-export")
def features_fx_export(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory to write feature files (default: feature-functions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    functions: Optional[str] = typer.Option(None, "--functions", help="Comma-separated list of function names to export"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Export feature_functions to individual JSON files (one per function).

    By default, exports all functions to the 'feature-functions/' directory.
    Each function is saved as <name>_v<version>.json.
    """
    with create_span("cli.feat-fx-export"):
        target_dir = Path(dir) if dir else Path("feature-functions")
        fx_filter = parse_comma_separated(functions)

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_functions"])
                exported_count = export_functions_to_directory(conn, target_dir, fx_filter)

            emit(
                f"Exported {exported_count} function(s) to {target_dir}",
                data={"exported_count": exported_count, "target_dir": str(target_dir)},
                json_output=json_output,
            )
        except Exception as exc:
            emit_error(f"Export failed: {exc}", json_output=json_output)


def _upsert_feature_function(conn: psycopg.Connection, payload: dict) -> None:
    """Upsert feature function using consolidated helper."""
    required = ["name", "version", "language", "function_body"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"Missing required keys for feature_function: {', '.join(missing)}")

    upsert_feature_function_helper(conn, payload, return_id=False)


def _upsert_feature_definition(conn: psycopg.Connection, payload: dict) -> None:
    """Upsert feature definition."""
    required = ["name", "function_name", "store_table", "store_column"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"Missing required keys for feature_definition: {', '.join(missing)}")

    normalized = _normalize_feature_definition(payload)
    ensure_feature_definitions(conn, [normalized])
    ensure_store_targets(conn, [normalized])


@app.command("feat-fx-import")
def features_fx_import(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory containing feature JSON files (default: feature-functions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    functions: Optional[str] = typer.Option(None, "--functions", help="Comma-separated list of function names to import"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Import feature_functions from individual JSON files.

    By default, imports all JSON files from the 'feature-functions/' directory.
    Idempotent: re-running will upsert by (name, version).
    """
    with create_span("cli.feat-fx-import"):
        src_dir = Path(dir) if dir else Path("feature-functions")
        fx_filter = parse_comma_separated(functions)

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_functions"])
                imported_count = import_functions_from_directory(conn, src_dir, fx_filter)

            if imported_count == 0:
                emit(f"No functions found in {src_dir}", json_output=json_output)
            else:
                emit(
                    f"Imported {imported_count} function(s) from {src_dir}",
                    data={"imported_count": imported_count, "source_dir": str(src_dir)},
                    json_output=json_output,
                )
        except Exception as exc:
            emit_error(f"Import failed: {exc}", json_output=json_output)


@app.command("feat-def-export")
def feat_def_export(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory to write feature definition files (default: feature-definitions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    features: Optional[str] = typer.Option(None, "--features", help="Comma-separated list of feature names to export"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Export feature_definitions to individual JSON files (one per feature).

    By default, exports all definitions to the 'feature-definitions/' directory.
    Each definition is saved as <name>.json.
    """
    with create_span("cli.feat-def-export"):
        target_dir = Path(dir) if dir else Path("feature-definitions")
        feat_filter = parse_comma_separated(features)

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_definitions"])
                exported_count = export_definitions_to_directory(conn, target_dir, feat_filter)

            emit(
                f"Exported {exported_count} definition(s) to {target_dir}",
                data={"exported_count": exported_count, "target_dir": str(target_dir)},
                json_output=json_output,
            )
        except Exception as exc:
            emit_error(f"Export failed: {exc}", json_output=json_output)


@app.command("feat-def-import")
def feat_def_import(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory containing feature definition JSON files (default: feature-definitions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    features: Optional[str] = typer.Option(None, "--features", help="Comma-separated list of feature names to import"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Import feature_definitions from individual JSON files.

    By default, imports all JSON files from the 'feature-definitions/' directory.
    Idempotent: re-running will upsert by name.
    """
    with create_span("cli.feat-def-import"):
        src_dir = Path(dir) if dir else Path("feature-definitions")
        feat_filter = parse_comma_separated(features)

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_definitions", "computed_features"])
                imported_count = import_definitions_from_directory(conn, src_dir, feat_filter)

            if imported_count == 0:
                emit(f"No definitions found in {src_dir}", json_output=json_output)
            else:
                emit(
                    f"Imported {imported_count} definition(s) from {src_dir}",
                    data={"imported_count": imported_count, "source_dir": str(src_dir)},
                    json_output=json_output,
                )
        except Exception as exc:
            emit_error(f"Import failed: {exc}", json_output=json_output)


@app.command("feat-trim")
def trim_features(
    feature: str = typer.Option(..., "--feature", help="Comma-separated feature names to trim"),
    before: Optional[str] = typer.Option(None, help="Drop rows before this date (YYYY-MM-DD)"),
    after: Optional[str] = typer.Option(None, help="Drop rows after this date (YYYY-MM-DD)"),
    trim_prices: bool = typer.Option(False, "--trim-prices/--no-trim-prices", help="Also trim stock_ohlcv for date window (default: False)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Trim computed_features for given feature names.
    Use --before for left-trim, --after for right-trim.
    By default, only features are trimmed. Use --trim-prices to also trim underlying price data.
    """
    with create_span("cli.feat-trim", before=before, after=after):
        if not before and not after:
            if json_output:
                emit_error("Specify --before and/or --after", json_output=True)
            else:
                raise typer.BadParameter("Missing option '--before' or '--after'", param_hint="'--before' / '--after'")
        before_dt = _parse_date_or_error(before, json_output)
        after_dt = _parse_date_or_error(after, json_output)

        names = parse_comma_separated(feature, required=True)
        if not names:
            emit_error("No feature names provided", json_output=json_output)
            return

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_definitions", "computed_features"])
                deleted = trim_feature_data(conn, names, before=before_dt, after=after_dt)
                prices_deleted = 0
                if trim_prices:
                    init_schema_tables(conn, ["stock_ohlcv"])
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
    trim_features: bool = typer.Option(True, "--trim-features/--no-trim-features", help="Also trim computed_features for date window (default: True)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Trim stock_ohlcv by date (optionally limited to symbols).
    By default, also trims all derived features. Use --no-trim-features to keep features.
    """
    with create_span("cli.prices-trim", before=before, after=after):
        if not before and not after:
            if json_output:
                emit_error("Specify --before and/or --after", json_output=True)
            else:
                raise typer.BadParameter("Missing option '--before' or '--after'", param_hint="'--before' / '--after'")
        before_dt = _parse_date_or_error(before, json_output)
        after_dt = _parse_date_or_error(after, json_output)
        sym_list = None
        if symbols:
            sym_list = parse_comma_separated(symbols, required=True)
            if not sym_list:
                sym_list = None
        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["stocks", "stock_ohlcv"])
                deleted = trim_stock_ohlcv(conn, before=before_dt, after=after_dt, symbols=sym_list)
                features_deleted = 0
                if trim_features:
                    init_schema_tables(conn, ["computed_features"])
                    features_deleted = trim_all_computed_features(conn, before=before_dt, after=after_dt, symbols=sym_list)
            emit(
                "Trimmed stock_ohlcv" + (" and computed_features" if trim_features else ""),
                data={
                    "deleted_prices": deleted,
                    "deleted_features": features_deleted,
                    "before": before,
                    "after": after,
                    "symbols": sym_list if sym_list else "All",
                },
                json_output=json_output,
            )
        except Exception as exc:
            emit_error(f"Trim prices failed: {exc}", json_output=json_output)


@app.command("feat-drop")
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
        gefion features-drop --feature indicator_rsi_14,indicator_macd

        # Drop all feature data but keep definitions
        gefion features-drop --all --data-only

        # Drop all features completely (DANGEROUS!)
        gefion features-drop --all
    """
    with create_span("cli.feat-drop", all_features=all_features, data_only=data_only):
        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_definitions", "computed_features"])

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
                    names = parse_comma_separated(feature, required=True)
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


@app.command("feat-def-list")
def features_list(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """List feature definitions."""
    with create_span("cli.feat-def-list"):
        from gefion.output import Column, get_output

        out = get_output(json_output)

        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_definitions"])
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

            if not data:
                out.warning("No features found")
                if out.json_mode:
                    out.json({"status": "ok", "features": [], "count": 0})
                return

            if out.json_mode:
                out.json({"status": "ok", "features": data, "count": len(data)})
                return

            table_rows = [
                [
                    d["name"] or "",
                    d["function"] or "",
                    d.get("source_table") or "",
                    d.get("source_column") or "",
                    d["store_table"] or "",
                    d["store_column"] or "",
                    str(d["active"]),
                    d["created_at"] or "",
                ]
                for d in data
            ]

            out.table(
                columns=[
                    Column("Name", style="white", json_key="name"),
                    Column("Function", style="magenta", json_key="function"),
                    Column("Source", style="cyan", json_key="source_table"),
                    Column("Source Col", style="cyan", json_key="source_column"),
                    Column("Store", style="green", json_key="store_table"),
                    Column("Column", style="blue", json_key="store_column"),
                    Column("Active", style="yellow", json_key="active"),
                    Column("Created", style="dim", json_key="created_at"),
                ],
                rows=table_rows,
                title="Features",
            )
        except Exception as exc:
            out.error(f"List failed: {exc}")
            raise typer.Exit(code=1)


@app.command("feat-def-show")
def features_show(
    feature: str = typer.Option(..., "--feature", help="Feature name"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Show a single feature definition."""
    with create_span("cli.feat-def-show", feature=feature):
        try:
            with db_connection(db_url) as conn:
                init_schema_tables(conn, ["feature_definitions"])
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


@app.command("feat-compute")
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
        gefion features-compute --symbols AAPL --function-names indicator

        # Compute specific features for multiple stocks
        gefion features-compute --symbols AAPL,MSFT --features indicator_rsi_14,derivative_rsi_14_slope_5

        # Full refresh of all features for all stocks
        gefion features-compute --all-features --full
    """
    with create_span(
        "cli.feat-compute",
        symbols=symbols or "all",
        features=features or "all",
        function_names=function_names or "all",
        incremental=incremental,
        parallel_functions=parallel_functions,
        writer_workers=writer_workers
    ):
        return _features_compute_impl(
            symbols, features, all_features, function_names, incremental, update_existing,
            max_workers, feature_batch_size, profile, sync_commit, writer_workers,
            parallel_functions, max_parallel_functions, db_url, json_output, progress
        )


def _features_compute_impl(
    symbols: Optional[str],
    features: Optional[str],
    all_features: bool,
    function_names: Optional[str],
    incremental: bool,
    update_existing: bool,
    max_workers: Optional[int],
    feature_batch_size: int,
    profile: bool,
    sync_commit: bool,
    writer_workers: int,
    parallel_functions: bool,
    max_parallel_functions: Optional[int],
    db_url: Optional[str],
    json_output: bool,
    progress: bool,
) -> None:
    """Internal implementation of features_compute."""
    from gefion.features.dispatcher import compute_features

    url = _db_url(db_url)

    # Parse feature names
    feature_name_list = parse_comma_separated(features)

    # Parse function names
    function_name_list = parse_comma_separated(function_names)

    # Parse symbols
    symbol_list = parse_comma_separated(symbols)

    pool_needed = False  # Initialize early to avoid UnboundLocalError in finally block
    try:
        with db_connection(db_url) as conn:
            init_schema_tables(conn, ["feature_definitions", "computed_features"])

            # If all_features, get all active feature names
            if all_features and not feature_name_list:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM feature_definitions WHERE active = TRUE ORDER BY name;"
                    )
                    feature_name_list = [r[0] for r in cur.fetchall()]

            # Validate that we have features to compute
            if not feature_name_list and not function_name_list:
                emit_error(
                    "No features specified.\n"
                    "\n"
                    "To fix this:\n"
                    "  → Specify features: --features indicator_rsi_14,indicator_macd\n"
                    "  → Or use function names: --function-names indicator\n"
                    "  → Or compute all features: --all-features\n"
                    "\n"
                    "First, ensure features are defined:\n"
                    "  → Run: gefion feat-def-list\n"
                    "  → Or import definitions: gefion feat-def-import --dir feature-definitions",
                    json_output=json_output
                )
                return

            # If no symbols specified, get all stocks
            if not symbol_list:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol FROM stocks ORDER BY symbol;")
                    symbol_list = [r[0] for r in cur.fetchall()]

            if not symbol_list:
                emit_error(
                    "No stocks found in database.\n"
                    "\n"
                    "To fix this:\n"
                    "  → Ingest a single stock: gefion prices-ingest --symbol AAPL\n"
                    "  → Or ingest a universe: gefion universe-ingest --exchange NASDAQ --limit 10\n"
                    "  → Or run full workflow: gefion data-update --exchange NASDAQ --limit 10",
                    json_output=json_output
                )
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
            start_workers = min(2, max_w)  # Start conservatively but respect max_workers

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
                        # Capture context for propagation to worker threads
                        from gefion.observability import is_enabled
                        if is_enabled():
                            from opentelemetry import context as otel_context
                            worker_ctx = otel_context.get_current()

                            def make_process_stock_with_context(symbol):
                                """Create context-aware wrapper for process_stock."""
                                def worker_with_context():
                                    token = otel_context.attach(worker_ctx)
                                    try:
                                        return process_stock(symbol)
                                    finally:
                                        otel_context.detach(token)
                                return worker_with_context

                            futures = {executor.submit(make_process_stock_with_context(sym)): sym for sym in batch_symbols}
                        else:
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

            # Add metrics to trace span
            current_span = get_current_span()
            set_attributes(current_span,
                total_inserted=total_inserted,
                stocks_processed=len(symbol_list),
                error_count=len(errors),
                success_rate=(len(symbol_list) - len(errors)) / max(1, len(symbol_list))
            )

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
        # Flush telemetry to ensure root span is sent before exit
        # This prevents "root span not yet received" in large traces
        from gefion.observability import flush_telemetry
        flush_telemetry()

        # Only close pool if we initialized it (don't close pools managed by caller)
        if pool_needed:
            db_pool.close_pool()


@app.command("fundamentals-update")
def fundamentals_update(
    exchange: Optional[str] = typer.Option(None, help="Exchange filter (e.g., NASDAQ, NYSE). If omitted, update all stocks."),
    limit: Optional[int] = typer.Option(None, help="Limit number of symbols to update"),
    max_age_days: int = typer.Option(30, "--max-age", help="Skip stocks updated within this many days"),
    force: bool = typer.Option(False, "--force", help="Update all stocks regardless of age"),
    calls_per_minute: int = typer.Option(75, help="AlphaVantage rate limit"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output as JSON"),
) -> None:
    """
    Update company fundamentals (sector, industry, name) from AlphaVantage.

    Fetches OVERVIEW data for stocks and updates the stocks table.
    By default, skips stocks updated within --max-age days (default: 30).

    Examples:
        # Update stale fundamentals for all stocks
        gefion fundamentals-update

        # Force update all NASDAQ stocks
        gefion fundamentals-update --exchange NASDAQ --force

        # Update up to 10 stocks
        gefion fundamentals-update --limit 10
    """
    with create_span(
        "cli.fundamentals-update",
        exchange=exchange or "all",
        max_age_days=max_age_days,
        force=force,
        limit=limit or 0,
    ):
        _fundamentals_update_impl(
            exchange, limit, max_age_days, force, calls_per_minute, db_url, json_output
        )


def _fundamentals_update_impl(
    exchange: Optional[str],
    limit: Optional[int],
    max_age_days: int,
    force: bool,
    calls_per_minute: int,
    db_url: Optional[str],
    json_output: Optional[bool],
) -> None:
    """Implementation of fundamentals-update."""
    from datetime import datetime, timedelta
    from gefion.alphavantage.client import AlphaVantageClient

    url = _db_url(db_url)

    try:
        client = AlphaVantageClient(calls_per_minute=calls_per_minute)
    except ValueError as e:
        emit_error(str(e), json_output=json_output)
        return

    # Get stocks to update
    with db_connection(url) as conn:
        with conn.cursor() as cur:
            query = "SELECT id, symbol, updated_at FROM stocks WHERE 1=1"
            params: list = []

            if exchange:
                # Note: We don't have exchange column yet, so this is a placeholder
                # In future, filter by exchange
                pass

            if not force:
                # Skip recently updated stocks
                cutoff = datetime.now() - timedelta(days=max_age_days)
                query += " AND (updated_at IS NULL OR updated_at < %s)"
                params.append(cutoff)

            query += " ORDER BY updated_at ASC NULLS FIRST"

            if limit:
                query += f" LIMIT {limit}"

            cur.execute(query, params)
            stocks = cur.fetchall()

    if not stocks:
        if json_output:
            emit_json({"success": True, "updated": 0, "message": "All stocks are up to date"})
        else:
            emit("[green]All stocks are up to date[/green]")
        return

    if not json_output:
        emit(f"Updating fundamentals for {len(stocks)} stocks...")

    updated = 0
    errors = 0

    for stock_id, symbol, _ in stocks:
        try:
            # Fetch overview from AlphaVantage
            overview = client.fetch_overview(symbol)

            # Check for API errors
            if "Error Message" in overview or "Note" in overview:
                if not json_output:
                    emit(f"[yellow]⚠[/yellow] {symbol}: API limit or error, skipping")
                errors += 1
                continue

            # Extract fields
            name = overview.get("Name", "")
            sector = overview.get("Sector", "")
            industry = overview.get("Industry", "")

            # Update database
            with db_connection(url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE stocks
                        SET name = %s, sector = %s, industry = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (name or None, sector or None, industry or None, stock_id))
                conn.commit()

            updated += 1
            if not json_output:
                sector_display = sector[:20] if sector else "N/A"
                emit(f"[green]✓[/green] {symbol}: {sector_display}")

        except Exception as e:
            errors += 1
            if not json_output:
                emit(f"[red]✗[/red] {symbol}: {e}")

    if json_output:
        emit_json({"success": True, "updated": updated, "errors": errors})
    else:
        emit("")
        emit(f"[bold]Complete:[/bold] {updated} updated, {errors} errors")


@app.command("cross-sectional-compute")
def cross_sectional_compute(
    feature: str = typer.Option(..., "--feature", "-f", help="Feature name to compute rankings for (e.g., indicator_rsi_14)"),
    date: Optional[str] = typer.Option(None, "--date", help="Target date (YYYY-MM-DD). Defaults to latest available."),
    include_market: bool = typer.Option(True, "--market/--no-market", help="Include market-wide rankings"),
    include_sectors: bool = typer.Option(True, "--sectors/--no-sectors", help="Include sector-relative rankings"),
    include_industries: bool = typer.Option(False, "--industries", help="Include industry-relative rankings"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    Compute cross-sectional rankings for a feature.

    Cross-sectional features compare stocks to their peers at the same point in time.
    Rankings are computed for different comparison groups:
    - market: rank vs all stocks
    - sector:X: rank vs sector peers
    - industry:X: rank vs industry peers

    Results are stored in the cross_sectional_features table.

    Examples:
        # Compute RSI rankings (market + sectors)
        gefion cross-sectional-compute --feature indicator_rsi_14

        # Include industry rankings
        gefion cross-sectional-compute --feature indicator_rsi_14 --industries

        # Market-only rankings
        gefion cross-sectional-compute --feature indicator_rsi_14 --no-sectors
    """
    with create_span(
        "cli.cross-sectional-compute",
        feature=feature,
        date=date or "latest",
        include_market=include_market,
        include_sectors=include_sectors,
        include_industries=include_industries,
    ):
        _cross_sectional_compute_impl(
            feature, date, include_market, include_sectors, include_industries, db_url, json_output
        )


def _cross_sectional_compute_impl(
    feature: str,
    target_date: Optional[str],
    include_market: bool,
    include_sectors: bool,
    include_industries: bool,
    db_url: Optional[str],
    json_output: bool,
) -> None:
    """Implementation of cross-sectional-compute."""
    from datetime import datetime
    from gefion.compute.cross_sectional import compute_and_store_rankings

    url = _db_url(db_url)

    # Parse date if provided
    parsed_date = None
    if target_date:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            emit_error(f"Invalid date format: {target_date}. Use YYYY-MM-DD.", json_output=json_output)
            return

    try:
        with db_connection(url) as conn:
            result = compute_and_store_rankings(
                conn=conn,
                feature_name=feature,
                target_date=parsed_date,
                include_market=include_market,
                include_sectors=include_sectors,
                include_industries=include_industries,
            )

            if json_output:
                emit_json(result)
            elif result.get("success"):
                emit(f"[bold green]Cross-sectional rankings computed[/bold green]")
                emit(f"  Feature: {result['feature_name']}")
                emit(f"  Date: {result['date']}")
                emit(f"  Stocks: {result['stocks_count']}")
                emit(f"  Rankings: {result['total_rankings']}")
                emit(f"  Groups: {', '.join(result['groups'])}")
            else:
                emit_error(result.get("error", "Unknown error"), json_output=json_output)

    except Exception as e:
        emit_error(f"Failed to compute rankings: {e}", json_output=json_output)


@app.command("data-update")
def update_all(
    exchange: Optional[str] = typer.Option(None, help="Exchange filter (e.g., NASDAQ, NYSE). If omitted, infer from stocks table."),
    status: str = typer.Option("Active", help="Listing status filter"),
    timeframe: str = typer.Option("auto", help="compact, full, or auto"),
    feature_batch_size: int = typer.Option(200, help="DB insert batch size for computed_features"),
    refresh_existing: bool = typer.Option(
        False,
        "--refresh-existing/--no-refresh-existing",
        "--update-existing/--no-update-existing",
        help="Refresh existing rows on conflict (upsert)",
    ),
    refresh: bool = typer.Option(False, help="Shortcut for full timeframe + refresh existing rows"),
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
    Update all data: prices first, then ALL active features.

    This is the main workflow command that ingests price data and computes
    all active features (indicators, derivatives, etc.) in one step.

    Feature definitions must exist in the database before running this command.
    Use 'gefion feat-def-import' to import feature definitions from JSON files.

    Examples:
        # First time setup: import feature definitions
        gefion feat-def-import --dir feature-definitions

        # Update data for existing stocks in database (inferred from stocks table)
        gefion data-update

        # Update NASDAQ stocks (limited to 20 for testing)
        gefion data-update --exchange NASDAQ --limit 20

        # Full refresh of all features
        gefion data-update --exchange NYSE --refresh

        # Incremental update for all stocks
        gefion data-update
    """
    with create_span(
        "cli.data-update",
        exchange=exchange or "inferred",
        timeframe=timeframe,
        refresh=refresh,
        limit=limit or 0,
    ):
        _update_all_impl(
            exchange, status, timeframe, feature_batch_size, refresh_existing,
            refresh, limit, max_workers, writer_workers, calls_per_minute,
            db_url, listings_file, progress, json_output
        )


def _update_all_impl(
    exchange, status, timeframe, feature_batch_size, refresh_existing,
    refresh, limit, max_workers, writer_workers, calls_per_minute,
    db_url, listings_file, progress, json_output
):
    """Implementation of data-update (separated for tracing)."""
    url = _db_url(db_url)
    if refresh:
        timeframe = "full"
        refresh_existing = True
    main_span = get_current_span()
    set_attributes(
        main_span,
        calls_per_minute=calls_per_minute,
        feature_batch_size=feature_batch_size,
        refresh_existing=refresh_existing,
        refresh=refresh,
        timeframe=timeframe,
    )

    # Check for pending migrations before running data operations
    from gefion.db.migrate import check_pending_migrations
    from pathlib import Path as PathLib
    import gefion
    try:
        package_dir = PathLib(gefion.__file__).parent.parent.parent
        migrations_dir = package_dir / "sql" / "migrations"

        if migrations_dir.exists():
            with create_span("cli.check_migrations"):
                with db_connection(url) as conn:
                    pending = check_pending_migrations(conn, migrations_dir)
                    if pending:
                        warning_msg = f"⚠️  Warning: {len(pending)} pending migration(s) detected. Database schema may be out of sync."
                        if not json_output:
                            emit(warning_msg)
                            for m in pending:
                                emit(f"  - {m['version']}_{m['name']}")
                            emit("  Run 'gefion db-migrate' to apply migrations before proceeding.")
                            emit("")
                        set_attributes(main_span, pending_migrations=len(pending), migrations_warning=True)
    except Exception:
        # Don't fail data-update if migration check fails
        pass

    # Resolve universe
    symbols: List[str] = []
    client: Optional[AlphaVantageClient] = None
    universe_source = "unknown"
    try:
        with create_span(
            "data_update.resolve_universe",
            exchange=exchange or "inferred",
            status=status,
            listings_file=bool(listings_file),
        ) as resolve_span:
            if listings_file:
                universe_source = "file"
                listings = load_listings_from_file(listings_file)
                filtered = filter_listings(listings, exchange=exchange, status=status)
                symbols = [row["symbol"] for row in filtered]
            else:
                # If exchange not provided, try to infer from existing stocks
                if exchange is None:
                    try:
                        with db_connection(url) as conn:
                            init_schema_tables(conn, ["stocks"])
                            with conn.cursor() as cur:
                                cur.execute("SELECT DISTINCT symbol FROM stocks;")
                                symbols = [r[0] for r in cur.fetchall()]
                        if symbols:
                            universe_source = "stocks_table"
                    except Exception:
                        symbols = []
                if not symbols:
                    universe_source = "alphavantage"
                    try:
                        client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key, calls_per_minute=calls_per_minute)
                    except ValueError as exc:
                        emit(str(exc), json_output=json_output, error=True)
                        raise typer.Exit(code=2)
                    listings = fetch_listings(client)
                    filtered = filter_listings(listings, exchange=exchange, status=status)
                    symbols = [row["symbol"] for row in filtered]
            set_attributes(
                resolve_span,
                source=universe_source,
                symbol_count=len(symbols),
                limit=limit or 0,
            )
    except req_exc.RequestException as exc:
        emit(f"Failed to fetch listings: {exc}", json_output=json_output, error=True)
        raise typer.Exit(code=2)
    if limit:
        symbols = symbols[:limit]
    set_attributes(main_span, symbol_count=len(symbols), universe_source=universe_source)
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
        compute_locally=True,  # Always compute locally (not from API)
        calls_per_minute=calls_per_minute,
        requested_fetch=max_workers,
        requested_writer=writer_workers,
        default_writer=writer_workers or 1,
    )

    # Save original symbols list for indicator filtering
    all_symbols = symbols.copy()

    # Calculate target date to prevent inserting partial/future data
    from gefion.ingest.universe import _expected_market_date, filter_symbols_needing_update
    target_date = _expected_market_date()
    set_attributes(main_span, target_date=str(target_date))

    # Bulk filter symbols that don't need price updates (skip API calls for up-to-date symbols)
    price_symbols = symbols
    price_skipped = 0
    if not refresh_existing:
        with create_span(
            "data_update.price_filter",
            total_symbols=len(symbols),
            target_date=str(target_date),
        ) as filter_span:
            with db_connection(db_url) as conn:
                with create_span("price_filter.schema_init"):
                    init_schema_tables(conn, ["stocks", "stock_ohlcv"])
                with create_span("price_filter.filter_symbols", symbol_count=len(symbols)):
                    price_symbols = filter_symbols_needing_update(conn, symbols, target_date)
                price_skipped = len(symbols) - len(price_symbols)
                if price_skipped > 0 and not json_output:
                    emit(f"Skipped {price_skipped} up-to-date symbols, processing {len(price_symbols)} symbols for prices", json_output=False)
            set_attributes(
                filter_span,
                price_symbols=len(price_symbols),
                skipped=price_skipped,
            )
    set_attributes(main_span, price_symbols=len(price_symbols), price_skipped=price_skipped)

    # Prices
    price_reporter = ProgressReporter(total=len(price_symbols), json_output=json_output, enabled=progress)
    price_reporter.skipped = price_skipped
    price_reporter.workers = price_fetch
    price_reporter.writer_workers = price_writer
    price_reporter.phase = "prices"
    price_reporter.mode = "api"
    price_live: Optional[Live] = None
    if progress and not json_output:
        price_live = price_reporter.start_live()
        if price_live:
            price_live.__enter__()
    try:
        with create_span(
            "data_update.price_ingest",
            symbol_count=len(price_symbols),
            timeframe=timeframe,
            update_existing=refresh_existing,
            fetch_workers=price_fetch,
            writer_workers=price_writer,
        ) as price_span:
            if client is None:
                client = AlphaVantageClient(api_key=SETTINGS.alphavantage_api_key, calls_per_minute=calls_per_minute)
            price_inserted = 0
            for chunk_index, sym_chunk in enumerate(chunked(price_symbols, 50), start=1):
                chunk_start = time.monotonic()
                before_inserted = price_inserted
                price_inserted += ingest_prices_for_symbols(
                    db_url=url,
                    client=client,
                    symbols=sym_chunk,
                    max_workers=price_fetch,
                    writer_workers=price_writer,
                    timeframe=timeframe,
                    update_existing=refresh_existing,
                    progress=price_reporter,
                    target_date=target_date,
                )
                add_event(
                    price_span,
                    "price_chunk_complete",
                    chunk_index=chunk_index,
                    chunk_size=len(sym_chunk),
                    inserted=price_inserted - before_inserted,
                    duration_ms=int((time.monotonic() - chunk_start) * 1000),
                )
            if price_live:
                price_live.update(price_reporter._build_table())
            price_reporter.complete(live=price_live)
            set_attributes(
                price_span,
                inserted=price_inserted,
                errors=price_reporter.errors,
                skipped=price_skipped,
            )
    except Exception as exc:
        if price_live:
            price_live.__exit__(type(exc), exc, exc.__traceback__)
        emit_error(f"Price ingest failed: {exc}", json_output=json_output)
    finally:
        if price_live:
            price_live.__exit__(None, None, None)

    # Features - compute ALL active features
    # Feature definitions must already exist (imported via gefion feat-def-import)
    active_feature_defs: Optional[int] = None
    with create_span("data_update.feature_defs") as defs_span:
        try:
            with db_connection(url) as conn:
                init_schema_tables(conn, ["feature_definitions"])
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM feature_definitions WHERE active = TRUE;")
                    active_feature_defs = cur.fetchone()[0]
            set_attributes(defs_span, active_feature_defs=active_feature_defs)
        except Exception as exc:
            set_attributes(defs_span, error=True)
            defs_span.record_exception(exc)
    if active_feature_defs is not None:
        set_attributes(main_span, active_feature_defs=active_feature_defs)

    features_inserted = 0
    feature_errors = 0
    feat_live: Optional[Live] = None
    feature_reporter: Optional[ProgressReporter] = None
    if active_feature_defs == 0:
        with create_span(
            "data_update.feature_compute",
            symbol_count=len(all_symbols),
            batch_size=feature_batch_size,
            incremental=not refresh,
            update_existing=refresh_existing,
            fetch_workers=feature_fetch,
            writer_workers=feature_writer,
            active_feature_defs=0,
            skipped=True,
        ):
            if not json_output:
                emit("No active feature definitions; skipping feature computation", json_output=False)
    else:
        # Compute all active features using generic dispatcher
        feature_reporter = ProgressReporter(total=len(all_symbols), json_output=json_output, enabled=progress)
        feature_reporter.workers = feature_fetch
        feature_reporter.writer_workers = feature_writer
        feature_reporter.phase = "features"
        feature_reporter.mode = "local"
        if progress and not json_output:
            feat_live = feature_reporter.start_live()
            if feat_live:
                feat_live.__enter__()
    try:
        if active_feature_defs != 0:
            with create_span(
                "data_update.feature_compute",
                symbol_count=len(all_symbols),
                batch_size=feature_batch_size,
                incremental=not refresh,
                update_existing=refresh_existing,
                fetch_workers=feature_fetch,
                writer_workers=feature_writer,
                active_feature_defs=active_feature_defs,
            ) as feature_span:
                for symbol in all_symbols:
                    with create_span("data_update.feature_symbol", symbol=symbol) as symbol_span:
                        try:
                            with db_connection(url) as conn:
                                from gefion.db.ingest import upsert_stock
                                data_id = upsert_stock(conn, symbol)
                                set_attributes(symbol_span, data_id=data_id)

                                # Compute ALL active features (indicators, derivatives, etc.)
                                # Note: Features are computed in arbitrary order. For dependency ordering,
                                # add a depends_on column to feature_definitions and topological sort.
                                result = compute_features(
                                    conn,
                                    data_id=data_id,
                                    function_names=None,  # None = compute all active features
                                    incremental=not refresh,
                                    update_existing=refresh_existing,
                                    feature_batch_size=feature_batch_size,
                                )

                                inserted = result.get("summary", {}).get("total_inserted", 0)
                                features_inserted += inserted
                                set_attributes(symbol_span, inserted=inserted, error=False)

                                if feature_reporter:
                                    feature_reporter.step_done(
                                        symbol,
                                        error=False,
                                        meta={"inserted": inserted},
                                    )
                        except Exception as exc:
                            symbol_span.record_exception(exc)
                            set_attributes(symbol_span, error=True)
                            if feature_reporter:
                                feature_reporter.step_done(
                                    symbol,
                                    error=True,
                                    meta={"inserted": 0, "reason": str(exc)},
                                )

                if feat_live and feature_reporter:
                    feat_live.update(feature_reporter._build_table())
                if feature_reporter:
                    feature_reporter.complete(live=feat_live)
                    feature_errors = feature_reporter.errors
                set_attributes(
                    feature_span,
                    inserted=features_inserted,
                    errors=feature_errors,
                )
    except Exception as exc:
        if feat_live:
            feat_live.__exit__(type(exc), exc, exc.__traceback__)
        emit_error(f"Feature computation failed: {exc}", json_output=json_output)
    finally:
        if feat_live:
            feat_live.__exit__(None, None, None)

    # Add span attributes for tracing
    span = get_current_span()
    set_attributes(
        span,
        symbol_count=len(symbols),
        price_inserted=price_inserted,
        features_inserted=features_inserted,
        price_errors=price_reporter.errors,
        feature_errors=feature_errors,
        price_skipped=price_skipped,
        price_fetch_workers=price_fetch,
        price_writer_workers=price_writer,
        feature_fetch_workers=feature_fetch,
        feature_writer_workers=feature_writer,
    )

    emit(
        "Update complete",
        data={
            "symbols": symbols,
            "price_inserted": price_inserted,
            "features_inserted": features_inserted,
            "price_fetch_workers": price_fetch,
            "price_writer_workers": price_writer,
            "feature_fetch_workers": feature_fetch,
            "feature_writer_workers": feature_writer,
        },
        json_output=json_output,
    )


@backtest_app.command("run")
def backtest_run(
    symbols: Optional[str] = typer.Option(
        None,
        "--symbols",
        help="Comma-separated symbols to backtest (e.g., AAPL,MSFT,GOOGL)"
    ),
    exchange: Optional[str] = typer.Option(
        None,
        "--exchange",
        help="Exchange name (alternative to --symbols, e.g., NASDAQ)"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Limit number of symbols from exchange (for testing)"
    ),
    strategy: str = typer.Option(
        "momentum",
        "--strategy",
        help="Strategy name: 'momentum', 'mean_reversion', 'ma_crossover', 'breakout', 'pairs_trading', 'rsi_divergence', 'volatility_contraction', 'ml_signal', or 'ml_filter'"
    ),
    start_date: str = typer.Option(
        ...,
        "--start-date",
        help="Backtest start date (YYYY-MM-DD)"
    ),
    end_date: str = typer.Option(
        ...,
        "--end-date",
        help="Backtest end date (YYYY-MM-DD)"
    ),
    initial_cash: float = typer.Option(
        100000.0,
        "--initial-cash",
        help="Initial portfolio cash amount"
    ),
    lookback_days: int = typer.Option(
        20,
        "--lookback-days",
        help="Momentum lookback period in days"
    ),
    top_n: int = typer.Option(
        10,
        "--top-n",
        help="Number of top momentum stocks to hold"
    ),
    rebalance_days: int = typer.Option(
        5,
        "--rebalance-days",
        help="Days between rebalancing (momentum strategy)"
    ),
    rsi_oversold: float = typer.Option(
        30.0,
        "--rsi-oversold",
        help="RSI oversold threshold for buy signals (mean_reversion strategy)"
    ),
    rsi_overbought: float = typer.Option(
        70.0,
        "--rsi-overbought",
        help="RSI overbought threshold for sell signals (mean_reversion strategy)"
    ),
    rsi_period: int = typer.Option(
        14,
        "--rsi-period",
        help="RSI calculation period in days (mean_reversion strategy)"
    ),
    position_size: float = typer.Option(
        0.2,
        "--position-size",
        help="Fraction of portfolio per position (mean_reversion strategy)"
    ),
    max_positions: int = typer.Option(
        5,
        "--max-positions",
        help="Maximum concurrent positions (mean_reversion, ma_crossover strategies)"
    ),
    fast_period: int = typer.Option(
        50,
        "--fast-period",
        help="Fast moving average period in days (ma_crossover strategy)"
    ),
    slow_period: int = typer.Option(
        200,
        "--slow-period",
        help="Slow moving average period in days (ma_crossover strategy)"
    ),
    volume_threshold: float = typer.Option(
        1.5,
        "--volume-threshold",
        help="Volume multiplier for breakout confirmation (breakout strategy)"
    ),
    entry_zscore: float = typer.Option(
        2.0,
        "--entry-zscore",
        help="Z-score threshold for entering pairs trade (pairs_trading strategy)"
    ),
    exit_zscore: float = typer.Option(
        0.5,
        "--exit-zscore",
        help="Z-score threshold for exiting pairs trade (pairs_trading strategy)"
    ),
    divergence_lookback: int = typer.Option(
        10,
        "--divergence-lookback",
        help="Days to look back for divergence detection (rsi_divergence strategy)"
    ),
    bb_period: int = typer.Option(
        20,
        "--bb-period",
        help="Bollinger Band moving average period (volatility_contraction strategy)"
    ),
    bb_std_dev: float = typer.Option(
        2.0,
        "--bb-std-dev",
        help="Bollinger Band standard deviation multiplier (volatility_contraction strategy)"
    ),
    squeeze_threshold: float = typer.Option(
        0.05,
        "--squeeze-threshold",
        help="Band width threshold for squeeze detection (volatility_contraction strategy)"
    ),
    expansion_threshold: float = typer.Option(
        0.10,
        "--expansion-threshold",
        help="Band width threshold for expansion detection (volatility_contraction strategy)"
    ),
    # ML Signal Strategy parameters
    model_name: Optional[str] = typer.Option(
        None,
        "--model-name",
        help="ML model name for ml_signal strategy"
    ),
    model_version: Optional[str] = typer.Option(
        None,
        "--model-version",
        help="ML model version for ml_signal strategy"
    ),
    horizon_days: int = typer.Option(
        7,
        "--horizon-days",
        help="Prediction horizon in days: 7, 30, or 90 (ml_signal strategy)"
    ),
    prediction_type: str = typer.Option(
        "quantile",
        "--prediction-type",
        help="Prediction type: 'quantile' or 'classifier' (ml_signal strategy)"
    ),
    return_threshold: float = typer.Option(
        0.02,
        "--return-threshold",
        help="Min expected return (q50) to generate buy signal (ml_signal strategy)"
    ),
    downside_limit: float = typer.Option(
        -0.05,
        "--downside-limit",
        help="Max acceptable downside (q10) for buy signal (ml_signal strategy)"
    ),
    trend_classes: Optional[str] = typer.Option(
        None,
        "--trend-classes",
        help="Comma-separated trend classes that trigger buy: strong_up,weak_up (ml_signal classifier)"
    ),
    confidence_threshold: float = typer.Option(
        0.5,
        "--confidence-threshold",
        help="Min probability threshold for classifier signals (ml_signal strategy)"
    ),
    prediction_source: str = typer.Option(
        "database",
        "--prediction-source",
        help="(Deprecated) Only 'database' mode is supported",
        hidden=True,  # Hide deprecated option from help
    ),
    # ML Filter Strategy parameters
    base_strategy: Optional[str] = typer.Option(
        None,
        "--base-strategy",
        help="Base strategy to filter: momentum, mean_reversion, ma_crossover, breakout (ml_filter)"
    ),
    filter_mode: str = typer.Option(
        "confirm",
        "--filter-mode",
        help="Filter mode: 'confirm' (require positive ML) or 'veto' (block negative) (ml_filter)"
    ),
    filter_min_q50: float = typer.Option(
        0.0,
        "--filter-min-q50",
        help="Min q50 to pass filter (ml_filter strategy)"
    ),
    filter_max_q10: float = typer.Option(
        -0.10,
        "--filter-max-q10",
        help="Block if q10 below this (ml_filter strategy)"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON"
    ),
) -> None:
    """
    Run backtest for a trading strategy.

    Examples:
        # Backtest momentum strategy on tech stocks
        gefion backtest run --symbols AAPL,MSFT,GOOGL,NVDA,TSLA \\
          --start-date 2024-01-01 --end-date 2024-12-01 \\
          --initial-cash 100000 --strategy momentum --top-n 3

        # Backtest mean reversion strategy on NASDAQ
        gefion backtest run --exchange NASDAQ --limit 50 \\
          --start-date 2024-01-01 --end-date 2024-12-01 \\
          --strategy mean_reversion --rsi-oversold 25 --rsi-overbought 75

        # Backtest moving average crossover strategy
        gefion backtest run --symbols AAPL,MSFT,GOOGL \\
          --start-date 2024-01-01 --end-date 2024-12-01 \\
          --strategy ma_crossover --fast-period 50 --slow-period 200
    """
    from datetime import datetime
    from gefion.backtest.data_loader import load_price_data_for_backtest
    from gefion.backtest.engine import BacktestEngine
    from gefion.strategies.momentum import MomentumStrategy
    from gefion.strategies.mean_reversion import MeanReversionStrategy
    from gefion.strategies.ma_crossover import MovingAverageCrossoverStrategy
    from gefion.strategies.breakout import BreakoutStrategy
    from gefion.strategies.pairs_trading import PairsTradingStrategy
    from gefion.strategies.rsi_divergence import RSIDivergenceStrategy
    from gefion.strategies.volatility_contraction import VolatilityContractionStrategy
    from gefion.strategies.ml_signal import MLSignalStrategy

    url = os.getenv("DATABASE_URL", SETTINGS.database_url)

    # Validate inputs
    if not symbols and not exchange:
        emit_error(
            "Must specify either --symbols or --exchange",
            json_output=json_output
        )
        raise typer.Exit(1)

    # Parse dates
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as e:
        emit_error(
            f"Invalid date format: {e}. Use YYYY-MM-DD format.",
            json_output=json_output
        )
        raise typer.Exit(1)

    if start >= end:
        emit_error(
            "Start date must be before end date",
            json_output=json_output
        )
        raise typer.Exit(1)

    # Parse symbols
    symbol_list = None
    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",")]

    # Load price data
    emit("Loading price data from database...", json_output=json_output)

    try:
        price_data = load_price_data_for_backtest(
            db_url=url,
            symbols=symbol_list,
            exchange=exchange,
            start_date=start,
            end_date=end,
            limit=limit,
        )

        if not price_data:
            emit_error(
                "No price data found for specified parameters.\n"
                "Try: gefion data-update --exchange NASDAQ --limit 50",
                json_output=json_output
            )
            raise typer.Exit(1)

        # Count symbols
        symbols_found = set(row["symbol"] for row in price_data)
        emit(
            f"Loaded {len(price_data)} price records for {len(symbols_found)} symbols",
            json_output=json_output
        )

    except Exception as e:
        emit_error(
            f"Failed to load price data: {e}",
            json_output=json_output
        )
        raise typer.Exit(1)

    # Initialize strategy
    if strategy == "momentum":
        strat = MomentumStrategy(
            lookback_days=lookback_days,
            top_n=top_n,
            rebalance_days=rebalance_days,
        )
    elif strategy == "mean_reversion":
        strat = MeanReversionStrategy(
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            rsi_period=rsi_period,
            position_size=position_size,
            max_positions=max_positions,
        )
    elif strategy == "ma_crossover":
        strat = MovingAverageCrossoverStrategy(
            fast_period=fast_period,
            slow_period=slow_period,
            position_size=position_size,
            max_positions=max_positions,
        )
    elif strategy == "breakout":
        strat = BreakoutStrategy(
            lookback_days=lookback_days,
            volume_threshold=volume_threshold,
            position_size=position_size,
            max_positions=max_positions,
        )
    elif strategy == "pairs_trading":
        strat = PairsTradingStrategy(
            lookback_days=lookback_days,
            entry_zscore=entry_zscore,
            exit_zscore=exit_zscore,
            position_size=position_size,
            max_pairs=max_positions,  # Reuse max_positions as max_pairs
        )
    elif strategy == "rsi_divergence":
        strat = RSIDivergenceStrategy(
            rsi_period=rsi_period,
            divergence_lookback=divergence_lookback,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            position_size=position_size,
            max_positions=max_positions,
        )
    elif strategy == "volatility_contraction":
        strat = VolatilityContractionStrategy(
            bb_period=bb_period,
            bb_std_dev=bb_std_dev,
            squeeze_threshold=squeeze_threshold,
            expansion_threshold=expansion_threshold,
            position_size=position_size,
            max_positions=max_positions,
        )
    elif strategy == "ml_signal":
        # Validate ML strategy parameters
        if not model_name:
            emit_error(
                "ML Signal strategy requires --model-name parameter",
                json_output=json_output
            )
            raise typer.Exit(1)
        if not model_version:
            emit_error(
                "ML Signal strategy requires --model-version parameter",
                json_output=json_output
            )
            raise typer.Exit(1)

        # Parse trend classes if provided
        parsed_trend_classes = None
        if trend_classes:
            parsed_trend_classes = [c.strip() for c in trend_classes.split(",")]

        strat = MLSignalStrategy(
            model_name=model_name,
            model_version=model_version,
            horizon_days=horizon_days,
            prediction_type=prediction_type,
            prediction_source=prediction_source,
            return_threshold=return_threshold,
            downside_limit=downside_limit,
            trend_classes=parsed_trend_classes,
            confidence_threshold=confidence_threshold,
            position_size=position_size,
            max_positions=max_positions,
            rebalance_days=rebalance_days,
            db_url=url,
        )
    elif strategy == "ml_filter":
        from gefion.strategies.ml_filter import MLFilterStrategy

        # Validate required parameters
        if not base_strategy:
            emit_error(
                "ML Filter strategy requires --base-strategy parameter",
                json_output=json_output
            )
            raise typer.Exit(1)
        if not model_name:
            emit_error(
                "ML Filter strategy requires --model-name parameter",
                json_output=json_output
            )
            raise typer.Exit(1)
        if not model_version:
            emit_error(
                "ML Filter strategy requires --model-version parameter",
                json_output=json_output
            )
            raise typer.Exit(1)

        # Create base strategy
        if base_strategy == "momentum":
            base_strat = MomentumStrategy(
                lookback_days=lookback_days,
                top_n=top_n,
                rebalance_days=rebalance_days,
            )
        elif base_strategy == "mean_reversion":
            base_strat = MeanReversionStrategy(
                rsi_oversold=rsi_oversold,
                rsi_overbought=rsi_overbought,
                position_size=position_size,
                max_positions=max_positions,
            )
        elif base_strategy == "ma_crossover":
            base_strat = MovingAverageCrossoverStrategy(
                fast_period=fast_period,
                slow_period=slow_period,
                max_positions=max_positions,
            )
        elif base_strategy == "breakout":
            base_strat = BreakoutStrategy(
                lookback_days=lookback_days,
                volume_threshold=volume_threshold,
            )
        else:
            emit_error(
                f"Unknown base strategy: {base_strategy}. Supported: momentum, mean_reversion, ma_crossover, breakout",
                json_output=json_output
            )
            raise typer.Exit(1)

        # Create ML filter wrapper
        strat = MLFilterStrategy(
            base_strategy=base_strat,
            model_name=model_name,
            model_version=model_version,
            horizon_days=horizon_days,
            filter_mode=filter_mode,
            min_q50=filter_min_q50,
            max_q10=filter_max_q10,
            db_url=url,
        )
    else:
        emit_error(
            f"Unknown strategy: {strategy}. Supported: momentum, mean_reversion, ma_crossover, breakout, pairs_trading, rsi_divergence, volatility_contraction, ml_signal, ml_filter",
            json_output=json_output
        )
        raise typer.Exit(1)

    # Run backtest
    emit(f"Running {strategy} strategy backtest...", json_output=json_output)

    try:
        # Helper to convert dict prices to flat list format
        def _dict_to_flat_prices(prices_dict):
            """Convert {symbol: [records]} to flat list with symbol field."""
            flat = []
            for symbol, records in prices_dict.items():
                for record in records:
                    flat_record = {**record}
                    if "symbol" not in flat_record:
                        flat_record["symbol"] = symbol
                    flat.append(flat_record)
            return flat

        # Create wrapper function for strategy that matches BacktestEngine interface
        # Some strategies expect dict format, others expect flat list
        dict_format_strategies = {"momentum", "ml_signal", "ml_filter"}

        def strategy_fn(current_date, portfolio, prices):
            # Convert prices to format expected by strategy
            if strategy in dict_format_strategies:
                price_data_for_strat = prices
                portfolio_for_strat = portfolio  # Keep Portfolio object
            else:
                price_data_for_strat = _dict_to_flat_prices(prices)
                portfolio_for_strat = portfolio.positions  # Convert to dict

            return strat.generate_signals(
                current_date=current_date,
                portfolio=portfolio_for_strat,
                price_data=price_data_for_strat,
                initial_cash=initial_cash,
            )

        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy_fn,
            initial_cash=initial_cash,
            start_date=start,
            end_date=end,
        )

        results = engine.run()

        # Extract data from results
        equity_curve = results.get("equity_curve", [])
        trades = results.get("trades", [])
        base_metrics = results.get("metrics", {})

        final_equity = equity_curve[-1]["equity"] if equity_curve else initial_cash
        trade_count = len(trades)

        # Calculate extended metrics
        from gefion.backtest.metrics import (
            calculate_trade_metrics,
            calculate_monthly_returns,
            calculate_drawdown_series,
            calculate_sortino_ratio,
            calculate_calmar_ratio,
            calculate_benchmark,
        )

        # Convert trades to include PnL for trade metrics
        trades_with_pnl = []
        for t in trades:
            pnl = t.get("pnl", 0)
            if pnl == 0 and t.get("action") == "sell":
                # Estimate PnL from price difference if not provided
                pnl = (t.get("price", 0) - t.get("avg_cost", t.get("price", 0))) * t.get("shares", 0)
            trades_with_pnl.append({**t, "pnl": pnl})

        trade_metrics = calculate_trade_metrics(trades_with_pnl)

        # Calculate monthly returns from equity curve
        monthly_returns = calculate_monthly_returns(equity_curve) if equity_curve else []

        # Calculate drawdown series
        drawdown_series = calculate_drawdown_series(equity_curve) if equity_curve else []

        # Calculate risk-adjusted metrics
        sortino_ratio = calculate_sortino_ratio(equity_curve) if equity_curve else 0
        days_in_backtest = len(equity_curve) if equity_curve else 0
        calmar_ratio = calculate_calmar_ratio(equity_curve, days=days_in_backtest) if equity_curve else 0

        # Calculate buy-and-hold benchmark for comparison
        benchmark_result = calculate_benchmark(price_data, initial_cash, start, end)

        # Build comprehensive metrics
        metrics = {
            "total_return": base_metrics.get("total_return", 0),
            "total_return_pct": base_metrics.get("total_return", 0) * 100,
            "sharpe_ratio": base_metrics.get("sharpe_ratio", 0),
            "sortino_ratio": sortino_ratio,
            "calmar_ratio": calmar_ratio,
            "max_drawdown": base_metrics.get("max_drawdown", 0),
            "max_drawdown_pct": base_metrics.get("max_drawdown", 0) * 100,
            "win_rate": trade_metrics.get("win_rate", 0),
            "profit_factor": trade_metrics.get("profit_factor", 0),
            "avg_win_loss_ratio": trade_metrics.get("avg_win_loss_ratio", 0),
            "total_trades": trade_count,
        }

        # Format and output results
        emit(
            "Backtest complete",
            data={
                "strategy": strategy,
                "parameters": {
                    "initial_cash": initial_cash,
                    "lookback_days": lookback_days,
                    "top_n": top_n,
                    "rebalance_days": rebalance_days,
                },
                "date_range": {
                    "start": start_date,
                    "end": end_date,
                },
                "symbols_tested": len(symbols_found),
                "performance": {
                    "initial_value": initial_cash,
                    "final_value": final_equity,
                    "total_return": base_metrics.get("total_return", 0),
                    "sharpe_ratio": base_metrics.get("sharpe_ratio", 0),
                    "max_drawdown": base_metrics.get("max_drawdown", 0),
                },
                "metrics": metrics,
                "trades_count": trade_count,
                "trades": [
                    {
                        "date": str(t.get("date", "")),
                        "action": t.get("action", ""),
                        "symbol": t.get("symbol", ""),
                        "shares": t.get("shares", 0),
                        "price": round(t.get("price", 0), 2),
                        "value": round(t.get("shares", 0) * t.get("price", 0), 2),
                        "pnl": round(t.get("pnl", 0), 2),
                    }
                    for t in trades_with_pnl
                ],
                "equity_curve": [
                    {"date": str(e["date"]), "equity": round(e["equity"], 2)}
                    for e in equity_curve
                ],
                "drawdown_series": drawdown_series,
                "monthly_returns": monthly_returns,
                "benchmark": {
                    "name": "Buy & Hold (Equal Weight)",
                    "total_return": benchmark_result.get("total_return", 0),
                    "total_return_pct": benchmark_result.get("total_return_pct", 0),
                    "equity_curve": [
                        {"date": str(e["date"]), "equity": round(e["equity"], 2)}
                        for e in benchmark_result.get("equity_curve", [])
                    ],
                },
            },
            json_output=json_output,
        )

        # Print formatted summary if not JSON
        if not json_output:
            console = Console()
            console.print("\n[bold green]Backtest Results[/bold green]")
            console.print(f"Strategy: {strategy}")
            console.print(f"Period: {start_date} to {end_date}")
            console.print(f"Symbols: {len(symbols_found)}")
            console.print(f"\n[bold]Performance:[/bold]")
            console.print(f"  Initial Value: ${initial_cash:,.2f}")
            console.print(f"  Final Value:   ${final_equity:,.2f}")
            console.print(f"  Total Return:  {metrics['total_return']:.2%}")
            console.print(f"  Sharpe Ratio:  {metrics['sharpe_ratio']:.3f}")
            console.print(f"  Max Drawdown:  {metrics['max_drawdown']:.2%}")
            console.print(f"\n[bold]Activity:[/bold]")
            console.print(f"  Total Trades:  {trade_count}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        emit_error(
            f"Backtest failed: {e}",
            json_output=json_output
        )
        raise typer.Exit(1)


def _get_strategy_config(db_url: str, config_name: str) -> Optional[Dict[str, Any]]:
    """Look up a strategy config by name from the database."""
    import psycopg

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT strategy_name, params
                    FROM strategy_configs
                    WHERE name = %s AND active = true
                """, (config_name,))
                row = cur.fetchone()
                if row:
                    return {
                        "strategy_name": row[0],
                        "params": row[1] if row[1] else {},
                    }
    except Exception:
        pass
    return None


@backtest_app.command("compare")
def backtest_compare(
    strategies: Optional[str] = typer.Option(
        None,
        "--strategies",
        help="Comma-separated strategy names or config names to compare (e.g., momentum,ml_filter_h7,ml_filter_h30)"
    ),
    all_strategies: bool = typer.Option(
        False,
        "--all",
        help="Compare all available strategies"
    ),
    symbols: Optional[str] = typer.Option(
        None,
        "--symbols",
        help="Comma-separated symbols to backtest (e.g., AAPL,MSFT,GOOGL)"
    ),
    exchange: Optional[str] = typer.Option(
        None,
        "--exchange",
        help="Exchange name (alternative to --symbols, e.g., NASDAQ)"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Limit number of symbols from exchange (for testing)"
    ),
    start_date: str = typer.Option(
        ...,
        "--start-date",
        help="Backtest start date (YYYY-MM-DD)"
    ),
    end_date: str = typer.Option(
        ...,
        "--end-date",
        help="Backtest end date (YYYY-MM-DD)"
    ),
    initial_cash: float = typer.Option(
        100000.0,
        "--initial-cash",
        help="Initial portfolio cash amount"
    ),
    rank_by: str = typer.Option(
        "sharpe_ratio",
        "--rank-by",
        help="Metric to rank strategies by (sharpe_ratio, total_return, calmar_ratio, sortino_ratio)"
    ),
    model_name: Optional[str] = typer.Option(
        None,
        "--model-name",
        help="ML model name for ml_signal/ml_filter strategies"
    ),
    model_version: Optional[str] = typer.Option(
        None,
        "--model-version",
        help="ML model version for ml_signal/ml_filter strategies"
    ),
    horizon_days: int = typer.Option(
        7,
        "--horizon-days",
        help="Prediction horizon for ML strategies"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON"
    ),
) -> None:
    """
    Compare multiple trading strategies side-by-side.

    Supports both strategy names and config names. Use strategy configs to
    compare different parameterizations of the same strategy (e.g., ml_filter
    with different horizons).

    Examples:
        # Compare momentum vs mean reversion on tech stocks
        gefion backtest compare --strategies momentum,mean_reversion \\
          --symbols AAPL,MSFT,GOOGL,NVDA,TSLA \\
          --start-date 2024-01-01 --end-date 2024-12-01

        # Compare ML filter configs with different horizons
        gefion backtest compare --strategies ml_filter_h7,ml_filter_h30 \\
          --symbols AAPL,MSFT,GOOGL --start-date 2024-01-01 --end-date 2024-12-01

        # Compare all strategies on NASDAQ stocks
        gefion backtest compare --all --exchange NASDAQ --limit 50 \\
          --start-date 2024-01-01 --end-date 2024-12-01

        # Compare strategies and rank by Calmar ratio
        gefion backtest compare --strategies momentum,breakout,ma_crossover \\
          --symbols AAPL,MSFT,GOOGL --start-date 2024-01-01 --end-date 2024-12-01 \\
          --rank-by calmar_ratio
    """
    from gefion.backtest.data_loader import load_price_data_for_backtest
    from gefion.backtest.comparison import compare_strategies, rank_strategies, AVAILABLE_STRATEGIES

    try:
        # Get database URL for config lookups
        db_url = os.getenv("DATABASE_URL", SETTINGS.database_url)

        # Validate strategies (support both strategy names and config names)
        strategy_mapping = {}  # Maps display_name -> actual_strategy
        config_params = {}  # Params from resolved configs

        if all_strategies:
            strategy_list = list(AVAILABLE_STRATEGIES.keys())
        elif strategies:
            strategy_list = [s.strip() for s in strategies.split(",")]
            # Check if each name is a strategy or a config
            for s in strategy_list:
                if s in AVAILABLE_STRATEGIES:
                    # Direct strategy name
                    continue
                else:
                    # Try to resolve as a config name
                    config = _get_strategy_config(db_url, s)
                    if config:
                        strategy_mapping[s] = config["strategy_name"]
                        config_params[s] = config["params"]
                    else:
                        emit_error(
                            f"Unknown strategy or config: '{s}'. Available strategies: {list(AVAILABLE_STRATEGIES.keys())}",
                            json_output=json_output,
                        )
                        raise typer.Exit(1)
        else:
            emit_error(
                "Must specify --strategies or --all",
                json_output=json_output,
            )
            raise typer.Exit(1)

        # Validate symbols/exchange
        if not symbols and not exchange:
            emit_error(
                "Must specify --symbols or --exchange",
                json_output=json_output,
            )
            raise typer.Exit(1)

        # Parse symbols
        symbol_list = None
        if symbols:
            symbol_list = [s.strip() for s in symbols.split(",")]

        # Load price data
        emit(f"Loading price data...", json_output=json_output)

        price_data = load_price_data_for_backtest(
            db_url=db_url,
            symbols=symbol_list,
            exchange=exchange,
            limit=limit,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
        )

        if not price_data:
            emit_error(
                "No price data found for specified symbols/date range",
                json_output=json_output,
            )
            raise typer.Exit(1)

        symbols_found = list(set(row["symbol"] for row in price_data))
        emit(f"Loaded {len(price_data)} price records for {len(symbols_found)} symbols", json_output=json_output)

        # Compare strategies (include equity curves for charting)
        emit(f"Comparing {len(strategy_list)} strategies...", json_output=json_output)

        # Build strategy params - start with params from resolved configs
        strategy_params = dict(config_params)

        # For direct ML strategies (not from configs), require CLI params
        direct_ml_strategies = [
            s for s in strategy_list
            if s in ("ml_signal", "ml_filter") and s not in strategy_mapping
        ]
        if direct_ml_strategies:
            if not model_name or not model_version:
                if all_strategies:
                    # When using --all, skip ML strategies if no model params provided
                    emit(
                        f"Skipping ML strategies (no --model-name/--model-version provided): {direct_ml_strategies}",
                        json_output=json_output,
                    )
                    strategy_list = [s for s in strategy_list if s not in direct_ml_strategies]
                else:
                    emit_error(
                        "ML strategies require --model-name and --model-version",
                        json_output=json_output,
                    )
                    raise typer.Exit(1)
            else:
                for ml_strat in direct_ml_strategies:
                    strategy_params[ml_strat] = {
                        "model_name": model_name,
                        "model_version": model_version,
                        "horizon_days": horizon_days,
                    }

        comparison = compare_strategies(
            strategies=strategy_list,
            strategy_mapping=strategy_mapping,
            price_data=price_data,
            initial_capital=initial_cash,
            strategy_params=strategy_params,
            include_equity_curves=True,
        )

        # Calculate benchmark for comparison
        from gefion.backtest.metrics import calculate_benchmark
        benchmark = calculate_benchmark(
            price_data=price_data,
            initial_capital=initial_cash,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
        )

        # Rank strategies
        ranking = rank_strategies(comparison, metric=rank_by)

        # Prepare comparison data for output (separate metrics from equity curves)
        comparison_metrics = {}
        equity_curves = {}
        for strategy_name, data in comparison.items():
            # Extract equity curve if present
            if "equity_curve" in data:
                equity_curves[strategy_name] = [
                    {"date": str(e["date"]), "equity": round(e["equity"], 2)}
                    for e in data["equity_curve"]
                ]
            # Copy metrics without equity curve
            comparison_metrics[strategy_name] = {
                k: v for k, v in data.items()
                if k not in ("equity_curve", "trades")
            }

        # Output results
        if json_output:
            emit(
                "Comparison complete",
                data={
                    "comparison": comparison_metrics,
                    "equity_curves": equity_curves,
                    "benchmark": {
                        "name": "Buy & Hold (Equal Weight)",
                        "total_return": benchmark.get("total_return", 0),
                        "total_return_pct": benchmark.get("total_return_pct", 0),
                        "equity_curve": [
                            {"date": str(e["date"]), "equity": round(e["equity"], 2)}
                            for e in benchmark.get("equity_curve", [])
                        ],
                    },
                    "ranking": [
                        {"strategy": name, rank_by: value}
                        for name, value in ranking
                    ],
                    "date_range": {
                        "start": start_date,
                        "end": end_date,
                    },
                    "symbols_tested": len(symbols_found),
                    "initial_cash": initial_cash,
                },
                json_output=json_output,
            )
        else:
            # Print rich table
            from rich.table import Table

            console = Console()
            console.print("\n[bold green]Strategy Comparison Results[/bold green]")
            console.print(f"Period: {start_date} to {end_date}")
            console.print(f"Symbols: {len(symbols_found)}")
            console.print(f"Initial Capital: ${initial_cash:,.2f}\n")

            table = Table(title="Strategy Performance")
            table.add_column("Rank", justify="center")
            table.add_column("Strategy", justify="left")
            table.add_column("Return %", justify="right")
            table.add_column("Sharpe", justify="right")
            table.add_column("Sortino", justify="right")
            table.add_column("Calmar", justify="right")
            table.add_column("Max DD", justify="right")
            table.add_column("Win Rate", justify="right")
            table.add_column("Trades", justify="right")

            for idx, (strategy_name, _) in enumerate(ranking, 1):
                metrics = comparison[strategy_name]
                table.add_row(
                    str(idx),
                    strategy_name,
                    f"{metrics.get('total_return', 0) * 100:.1f}%",
                    f"{metrics.get('sharpe_ratio', 0):.2f}",
                    f"{metrics.get('sortino_ratio', 0):.2f}",
                    f"{metrics.get('calmar_ratio', 0):.2f}",
                    f"{metrics.get('max_drawdown', 0) * 100:.1f}%",
                    f"{metrics.get('win_rate', 0) * 100:.0f}%",
                    str(metrics.get('total_trades', 0)),
                )

            console.print(table)
            console.print(f"\n[dim]Ranked by: {rank_by}[/dim]")

    except Exception as e:
        import traceback
        traceback.print_exc()
        emit_error(
            f"Comparison failed: {e}",
            json_output=json_output
        )
        raise typer.Exit(1)


@app.command("mcp-setup")
def mcp_setup(
    db_url: Optional[str] = typer.Option(None, help="Database URL (default: from environment or postgresql://gefion:gefionpass@localhost:5432/gefion)"),
    api_key: Optional[str] = typer.Option(None, help="AlphaVantage API key (default: from environment)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing configuration"),
    targets: Optional[str] = typer.Option("all", help="Targets to configure: 'desktop', 'cli', or 'all' (default: all)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Configure MCP server for use with AI assistants.

    This command creates or updates the MCP server configuration for AI assistants.
    It determines the correct paths and settings automatically and can configure
    multiple targets at once.

    Configuration targets:
    - desktop: Claude Desktop App (GUI)
    - cli: Claude Code CLI and OpenAI-compatible tools
    - all: Both desktop and CLI (default)

    Configuration file locations:
    - Claude Desktop (macOS): ~/Library/Application Support/Claude/claude_desktop_config.json
    - Claude Code CLI (macOS): ~/.claude.json
    - Windows Desktop: %APPDATA%\\Claude\\claude_desktop_config.json
    - Windows CLI: %USERPROFILE%\\.claude.json
    - Linux Desktop: ~/.config/Claude/claude_desktop_config.json
    - Linux CLI: ~/.claude.json

    Example:
        gefion mcp-setup                    # Configure all targets
        gefion mcp-setup --targets cli      # Configure only CLI
        gefion mcp-setup --targets desktop  # Configure only desktop
        gefion mcp-setup --force            # Overwrite existing config
    """
    with create_span("cli.mcp-setup"):
        import platform
        import sys
        from pathlib import Path

        try:
            # Load environment variables from .env file if it exists
            from pathlib import Path
            env_file = Path.cwd() / '.env'
            if env_file.exists():
                from dotenv import load_dotenv
                load_dotenv(env_file)

            # Parse targets
            target_list = [t.strip().lower() for t in targets.split(',')]
            if 'all' in target_list:
                target_list = ['desktop', 'cli']

            # Determine config file locations based on platform
            system = platform.system()
            config_files = []

            if 'desktop' in target_list:
                if system == "Darwin":  # macOS
                    desktop_config = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
                elif system == "Windows":
                    desktop_config = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
                else:  # Linux
                    desktop_config = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
                config_files.append(('desktop', desktop_config))

            if 'cli' in target_list:
                # Claude Code CLI uses ~/.claude.json on all platforms
                cli_config = Path.home() / ".claude.json"
                config_files.append(('cli', cli_config))

            # Get absolute path to gefion project root
            cli_file = Path(__file__).resolve()
            project_root = cli_file.parent.parent.parent
            server_path = project_root / "mcp-server" / "server.py"

            if not server_path.exists():
                emit_error(
                    f"MCP server not found at {server_path}. "
                    "Are you running this from the Gefion project directory?",
                    json_output=json_output
                )
                raise typer.Exit(1)

            # Get database URL
            if not db_url:
                db_url = os.environ.get('DATABASE_URL', 'postgresql://gefion:gefionpass@localhost:6432/gefion')

            # Get API key
            if not api_key:
                api_key = os.environ.get('ALPHAVANTAGE_API_KEY', '')

            # Get Python interpreter path
            python_path = sys.executable

            # Prepare MCP server config
            expected_config = {
                "command": python_path,
                "args": [str(server_path)],
                "env": {"DATABASE_URL": db_url}
            }
            if api_key:
                expected_config["env"]["ALPHAVANTAGE_API_KEY"] = api_key

            # Process each config file
            results = []
            all_unchanged = True

            for target_name, config_file in config_files:
                config_unchanged = False
                existing_config = {}

                # Check if config file exists and load it
                if config_file.exists():
                    with open(config_file, 'r') as f:
                        existing_config = json.load(f)

                    # Check if gefion server already configured
                    if "mcpServers" in existing_config and "gefion" in existing_config.get("mcpServers", {}):
                        existing_gefion_config = existing_config["mcpServers"]["gefion"]

                        # Compare configurations (ignoring key order)
                        if (existing_gefion_config.get("command") == expected_config["command"] and
                            existing_gefion_config.get("args") == expected_config["args"] and
                            existing_gefion_config.get("env", {}) == expected_config["env"]):
                            # Configuration is already correct - idempotent success
                            config_unchanged = True
                        elif not force:
                            emit_error(
                                f"MCP server already configured in {config_file} with different settings. "
                                "Use --force to overwrite.",
                                json_output=json_output
                            )
                            raise typer.Exit(1)

                # Only write if config changed or doesn't exist
                if not config_unchanged:
                    all_unchanged = False
                    # Merge configurations
                    if not existing_config.get("mcpServers"):
                        existing_config["mcpServers"] = {}
                    existing_config["mcpServers"]["gefion"] = expected_config

                    # Create config directory if it doesn't exist
                    config_file.parent.mkdir(parents=True, exist_ok=True)

                    # Write configuration
                    with open(config_file, 'w') as f:
                        json.dump(existing_config, f, indent=2)

                results.append({
                    "target": target_name,
                    "config_file": str(config_file),
                    "config_unchanged": config_unchanged,
                })

            result = {
                "targets": results,
                "server_path": str(server_path),
                "python_path": python_path,
                "database_url": db_url,
                "api_key_set": bool(api_key),
                "all_unchanged": all_unchanged,
            }

            if json_output:
                emit("MCP Setup Complete", data=result, json_output=True)
            else:
                console = Console()
                if all_unchanged:
                    console.print("\n[bold green]✓ MCP Server Configuration Already Up-to-Date[/bold green]\n")
                else:
                    console.print("\n[bold green]✓ MCP Server Configuration Complete[/bold green]\n")

                # Show results for each target
                for target_result in results:
                    target_name = target_result['target']
                    target_file = target_result['config_file']
                    unchanged = target_result['config_unchanged']

                    status = "[dim]unchanged[/dim]" if unchanged else "[green]updated[/green]"
                    console.print(f"{target_name.capitalize()}: {status}")
                    console.print(f"  [dim]{target_file}[/dim]")

                console.print(f"\nServer path: [cyan]{server_path}[/cyan]")
                console.print(f"Python: [cyan]{python_path}[/cyan]")
                console.print(f"Database: [cyan]{db_url}[/cyan]")
                if api_key:
                    console.print(f"API Key: [green]✓ Set[/green]")
                else:
                    console.print(f"API Key: [yellow]⚠ Not set (optional)[/yellow]")

                if not all_unchanged:
                    console.print("\n[bold]Next steps:[/bold]")
                    if any(r['target'] == 'desktop' and not r['config_unchanged'] for r in results):
                        console.print("• Restart Claude Desktop App")
                    if any(r['target'] == 'cli' and not r['config_unchanged'] for r in results):
                        console.print("• Restart Claude Code CLI or OpenAI-compatible tools")
                    console.print("\nThe 'gefion' MCP server should now be available")
                else:
                    console.print("\n[dim]All configurations are already correct. No changes needed.[/dim]")
                console.print("\n[dim]To update configs, run: gefion mcp-setup --force[/dim]")

        except typer.Exit:
            raise
        except Exception as exc:
            import traceback
            traceback.print_exc()
            emit_error(f"Setup failed: {exc}", json_output=json_output)
            raise typer.Exit(1)


# =============================================================================
# Strategy Commands
# =============================================================================


@strategy_app.command("list")
def strategy_list(
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all registered strategies."""
    from gefion.output import Column, get_output
    from gefion.strategies.dispatcher import get_strategy_registry

    url = _db_url(db_url)
    with psycopg.connect(url) as conn:
        strategies = get_strategy_registry(conn)

    out = get_output(json_output)
    out.table(
        columns=[
            Column("Name", style="cyan"),
            Column("Description"),
            Column("Tags", style="dim"),
            Column("Default Params", style="dim"),
        ],
        rows=[
            [
                s["name"],
                s.get("description", ""),
                ", ".join(s.get("tags", [])),
                json.dumps(s.get("default_params", {})),
            ]
            for s in strategies
        ],
        title="Registered Strategies",
        data_key="strategies",
        json_data=strategies,  # Pass raw data for JSON output
    )


@strategy_app.command("configs")
def strategy_configs(
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all active strategy configurations."""
    from gefion.output import Column, get_output
    from gefion.strategies.dispatcher import get_strategy_configs

    url = _db_url(db_url)
    with psycopg.connect(url) as conn:
        configs = get_strategy_configs(conn)

    out = get_output(json_output)
    out.table(
        columns=[
            Column("Name", style="cyan"),
            Column("Strategy", style="green"),
            Column("Params", style="dim"),
            Column("Description"),
        ],
        rows=[
            [
                c["name"],
                c["strategy_name"],
                json.dumps(c.get("params", {})),
                c.get("description", ""),
            ]
            for c in configs
        ],
        title="Strategy Configurations",
        data_key="configs",
        json_data=configs,  # Pass raw data for JSON output
    )


@strategy_app.command("create-config")
def strategy_create_config(
    name: str = typer.Option(..., "--name", help="Unique name for the config"),
    strategy: str = typer.Option(..., "--strategy", help="Strategy name from registry"),
    params: Optional[str] = typer.Option(None, "--params", help="JSON params to override defaults"),
    description: Optional[str] = typer.Option(None, "--description", help="Config description"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Create a new strategy configuration."""
    from gefion.output import get_output
    from gefion.strategies.dispatcher import create_strategy_config

    out = get_output(json_output)

    # Parse params JSON
    parsed_params = {}
    if params:
        try:
            parsed_params = json.loads(params)
        except json.JSONDecodeError as e:
            out.error(f"Invalid JSON in --params: {e}")
            raise typer.Exit(code=1)

    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            config_id = create_strategy_config(
                conn,
                name=name,
                strategy_name=strategy,
                params=parsed_params,
                description=description,
            )
    except ValueError as e:
        out.error(str(e))
        raise typer.Exit(code=1)

    out.success(f"Created config '{name}'", {"id": config_id, "name": name, "strategy": strategy})


@volatility_app.command("compute")
def volatility_compute(
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbols"),
    exchange: Optional[str] = typer.Option(None, "--exchange", help="Exchange name"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Limit symbols from exchange"),
    horizons: str = typer.Option("7,30,90", "--horizons", help="Comma-separated horizons in days"),
    date: Optional[str] = typer.Option(None, "--date", help="Calculation date (YYYY-MM-DD)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Compute volatility thresholds for stocks."""
    from datetime import datetime

    from gefion.ml.volatility import (
        calculate_historical_volatility,
        compute_adaptive_thresholds,
        compute_volatility_percentile,
    )

    # Validate input
    if not symbols and not exchange and not limit:
        emit_error("Must specify --symbols, --exchange, or --limit", json_output=json_output)

    # Parse horizons
    try:
        horizon_list = [int(h.strip()) for h in horizons.split(",")]
    except ValueError:
        emit_error("Invalid horizons format", json_output=json_output)

    # Parse date
    calc_date = datetime.now().date() if date is None else datetime.strptime(date, "%Y-%m-%d").date()

    url = _db_url(db_url)
    try:
        with psycopg.connect(url) as conn:
            # Get symbols list
            if symbols:
                symbol_list = [s.strip().upper() for s in symbols.split(",")]
            else:
                with conn.cursor() as cur:
                    # Query stocks with sufficient price history (at least 60 days)
                    query = """
                        SELECT s.symbol
                        FROM stocks s
                        JOIN stock_ohlcv o ON o.data_id = s.id
                        GROUP BY s.symbol
                        HAVING COUNT(*) >= 60
                    """
                    if limit:
                        query += f" LIMIT {limit}"
                    cur.execute(query)
                    symbol_list = [row[0] for row in cur.fetchall()]

            if not symbol_list:
                emit_error("No symbols found", json_output=json_output)

            # Get price data and compute volatility for each symbol
            import pandas as pd

            results = []
            all_volatilities = []

            # First pass: compute volatilities
            for sym in symbol_list:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT s.id, o.date, o.close
                        FROM stock_ohlcv o
                        JOIN stocks s ON o.data_id = s.id
                        WHERE s.symbol = %s
                        ORDER BY o.date DESC
                        LIMIT 252
                        """,
                        (sym,),
                    )
                    rows = cur.fetchall()

                if len(rows) < 60:
                    continue

                data_id = rows[0][0]
                df = pd.DataFrame(rows, columns=["data_id", "date", "close"])
                df = df.sort_values("date")
                returns = df["close"].pct_change().dropna()

                vol = calculate_historical_volatility(returns, window=60, annualize=True)
                if vol is not None:
                    all_volatilities.append((sym, data_id, vol, returns))

            if not all_volatilities:
                emit_error("No volatility data computed", json_output=json_output)

            # Compute percentiles
            vol_series = pd.Series([v[2] for v in all_volatilities])

            # Second pass: compute thresholds and store
            for sym, data_id, vol, returns in all_volatilities:
                percentile = compute_volatility_percentile(vol, vol_series)

                for horizon in horizon_list:
                    weak, strong = compute_adaptive_thresholds(vol, horizon, percentile)

                    # Upsert threshold
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO volatility_thresholds
                                (data_id, horizon_days, calculation_date,
                                 historical_volatility, weak_threshold, strong_threshold,
                                 volatility_percentile)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (data_id, horizon_days, calculation_date)
                            DO UPDATE SET
                                historical_volatility = EXCLUDED.historical_volatility,
                                weak_threshold = EXCLUDED.weak_threshold,
                                strong_threshold = EXCLUDED.strong_threshold,
                                volatility_percentile = EXCLUDED.volatility_percentile
                            """,
                            (data_id, horizon, calc_date, vol, weak, strong, percentile),
                        )

                    results.append({
                        "symbol": sym,
                        "horizon": horizon,
                        "volatility": round(vol, 4),
                        "weak_threshold": round(weak, 4),
                        "strong_threshold": round(strong, 4),
                        "percentile": round(percentile, 2),
                    })

            conn.commit()

    except psycopg.Error as e:
        emit_error(f"Database error: {e}", json_output=json_output)

    if json_output:
        emit_json({"count": len(results), "results": results})
    else:
        console = Console()
        console.print(f"Computed {len(results)} volatility thresholds")
        if results:
            table = Table(title="Sample Thresholds")
            table.add_column("Symbol")
            table.add_column("Horizon")
            table.add_column("Vol")
            table.add_column("Weak")
            table.add_column("Strong")
            for r in results[:10]:
                table.add_row(
                    r["symbol"],
                    str(r["horizon"]),
                    f"{r['volatility']:.1%}",
                    f"{r['weak_threshold']:.2%}",
                    f"{r['strong_threshold']:.2%}",
                )
            console.print(table)


# =============================================================================
# EXPERIMENT COMMANDS
# =============================================================================


@experiment_app.command("propose")
def experiment_propose(
    name: str = typer.Option(..., "--name", "-n", help="Experiment name"),
    experiment_type: str = typer.Option(
        "strategy_params", "--type", "-t",
        help="Experiment type (strategy_params, feature_selection, hyperparameter)"
    ),
    strategy: Optional[str] = typer.Option(
        None, "--strategy", help="Strategy name (for strategy_params type)"
    ),
    search_space: str = typer.Option(
        ..., "--search-space", "-s",
        help='JSON search space, e.g. \'{"lookback_days": {"type": "int", "low": 5, "high": 20}}\''
    ),
    symbols: Optional[str] = typer.Option(
        None, "--symbols", help="Comma-separated symbols (e.g., AAPL,MSFT,GOOGL)"
    ),
    exchange: Optional[str] = typer.Option(None, "--exchange", help="Exchange name"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date (YYYY-MM-DD)"),
    objective: str = typer.Option("sharpe_ratio", "--objective", "-o", help="Metric to optimize"),
    max_trials: int = typer.Option(50, "--max-trials", help="Maximum number of trials"),
    search_method: str = typer.Option(
        "grid", "--search-method", "-m",
        help="Search method: grid, random, or bayesian"
    ),
    goal_type: Optional[str] = typer.Option(
        None, "--goal-type",
        help="Goal type: achieve (target value), improve (beat baseline)"
    ),
    goal_target: Optional[float] = typer.Option(None, "--goal-target", help="Target value for goal"),
    baseline: Optional[float] = typer.Option(None, "--baseline", help="Baseline value for improvement goals"),
    early_stop: bool = typer.Option(False, "--early-stop", help="Stop when goal achieved"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Propose a new experiment for approval."""
    from gefion.experiments.core import ExperimentConfig, ExperimentRunner

    try:
        search_space_dict = json.loads(search_space)
    except json.JSONDecodeError as e:
        emit_error(f"Invalid JSON in search-space: {e}", json_output=json_output)

    # Build extra config
    extra_config = {}
    if strategy:
        extra_config["strategy"] = strategy

    config = ExperimentConfig(
        name=name,
        experiment_type=experiment_type,
        search_space=search_space_dict,
        objective_metric=objective,
        max_trials=max_trials,
        search_method=search_method,
        goal_type=goal_type,
        goal_target=goal_target,
        baseline_value=baseline,
        early_stop_on_goal=early_stop,
        symbols=parse_comma_separated(symbols) if symbols else None,
        exchange=exchange,
        start_date=start_date,
        end_date=end_date,
        extra_config=extra_config,
    )

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        experiment_id = runner.propose(config, proposed_by="user")
        experiment = runner.get(experiment_id)

        if json_output:
            emit_json({
                "experiment_id": experiment_id,
                "name": name,
                "status": "proposed",
                "message": f"Experiment #{experiment_id} proposed. Use 'gefion experiment approve --id {experiment_id}' to approve."
            })
        else:
            console = Console()
            console.print(f"[bold green]Experiment #{experiment_id} proposed[/bold green]")
            console.print(f"  Name: {name}")
            console.print(f"  Type: {experiment_type}")
            console.print(f"  Objective: {objective}")
            console.print(f"  Max Trials: {max_trials}")
            if goal_type:
                console.print(f"  Goal: {goal_type} {goal_target}")
            console.print()
            console.print(f"[dim]To approve: gefion experiment approve --id {experiment_id}[/dim]")

    except Exception as e:
        emit_error(f"Failed to propose experiment: {e}", json_output=json_output)


@experiment_app.command("list")
def experiment_list(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    experiment_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by type"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """List experiments."""
    from gefion.experiments.core import ExperimentRunner
    from gefion.output import Column, get_output

    out = get_output(json_output)
    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        experiments = runner.list(status=status, experiment_type=experiment_type, limit=limit)

        if not experiments:
            out.info("No experiments found")
            if out.json_mode:
                out.json({"experiments": [], "count": 0})
            return

        rows = []
        for exp in experiments:
            trials = f"{exp.get('completed_trials', 0) or 0}/{exp.get('total_trials', 0) or 0}"
            best = f"{exp['best_score']:.4f}" if exp.get('best_score') else "-"
            rows.append([
                str(exp["id"]),
                exp["name"][:30],
                exp["experiment_type"],
                exp["status"],
                trials,
                best,
            ])

        out.table(
            columns=[
                Column("ID", style="cyan", json_key="id"),
                Column("Name", json_key="name"),
                Column("Type", json_key="experiment_type"),
                Column("Status", json_key="status"),
                Column("Trials", json_key="trials"),
                Column("Best Score", json_key="best_score"),
            ],
            rows=rows,
            title="Experiments",
            data_key="experiments",
            json_data=experiments,
        )

    except Exception as e:
        out.error(f"Failed to list experiments: {e}")
        raise typer.Exit(code=1)


@experiment_app.command("pending")
def experiment_pending(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """List experiments awaiting approval."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        pending = runner.get_pending_approvals()

        if json_output:
            emit_json({"count": len(pending), "pending": pending})
        else:
            console = Console()
            if not pending:
                console.print("[dim]No experiments awaiting approval[/dim]")
                return

            console.print(f"[bold]{len(pending)} experiment(s) awaiting approval:[/bold]\n")

            for exp in pending:
                console.print(f"[cyan]#{exp['id']}[/cyan] {exp['name']}")
                console.print(f"  Type: {exp['experiment_type']}")
                console.print(f"  Objective: {exp['objective_metric']}")
                console.print(f"  Trials: {exp.get('total_trials', 0)}")
                if exp.get('goal_type'):
                    console.print(f"  Goal: {exp['goal_type']} {exp.get('goal_target')}")
                console.print(f"  [dim]Approve: gefion experiment approve --id {exp['id']}[/dim]")
                console.print()

    except Exception as e:
        emit_error(f"Failed to get pending experiments: {e}", json_output=json_output)


@experiment_app.command("approve")
def experiment_approve(
    experiment_id: int = typer.Option(..., "--id", "-i", help="Experiment ID to approve"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Approve a proposed experiment."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        runner.approve(experiment_id, approver="user")
        experiment = runner.get(experiment_id)

        if json_output:
            emit_json({
                "experiment_id": experiment_id,
                "status": "approved",
                "message": f"Experiment #{experiment_id} approved. Use 'gefion experiment run --id {experiment_id}' to run."
            })
        else:
            console = Console()
            console.print(f"[bold green]Experiment #{experiment_id} approved[/bold green]")
            console.print(f"[dim]To run: gefion experiment run --id {experiment_id}[/dim]")

    except ValueError as e:
        emit_error(str(e), json_output=json_output)
    except Exception as e:
        emit_error(f"Failed to approve experiment: {e}", json_output=json_output)


@experiment_app.command("reject")
def experiment_reject(
    experiment_id: int = typer.Option(..., "--id", "-i", help="Experiment ID to reject"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r", help="Rejection reason"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Reject a proposed experiment."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        runner.reject(experiment_id, reason=reason)

        if json_output:
            emit_json({
                "experiment_id": experiment_id,
                "status": "rejected",
                "reason": reason,
            })
        else:
            console = Console()
            console.print(f"[bold yellow]Experiment #{experiment_id} rejected[/bold yellow]")
            if reason:
                console.print(f"  Reason: {reason}")

    except ValueError as e:
        emit_error(str(e), json_output=json_output)
    except Exception as e:
        emit_error(f"Failed to reject experiment: {e}", json_output=json_output)


@experiment_app.command("status")
def experiment_status(
    experiment_id: int = typer.Option(..., "--id", "-i", help="Experiment ID"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Get detailed status of an experiment."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        experiment = runner.get(experiment_id)

        if json_output:
            # Convert datetime objects to strings for JSON
            for key in ["created_at", "started_at", "completed_at"]:
                if experiment.get(key):
                    experiment[key] = str(experiment[key])
            emit_json(experiment)
        else:
            console = Console()
            console.print(f"[bold]Experiment #{experiment_id}[/bold]\n")

            status_color = {
                "proposed": "yellow",
                "approved": "blue",
                "running": "cyan",
                "completed": "green",
                "failed": "red",
                "rejected": "dim",
            }.get(experiment["status"], "white")

            console.print(f"  Name: {experiment['name']}")
            console.print(f"  Type: {experiment['experiment_type']}")
            console.print(f"  Status: [{status_color}]{experiment['status']}[/{status_color}]")
            console.print(f"  Objective: {experiment['objective_metric']} ({experiment['objective_direction']})")

            if experiment.get("goal_type"):
                console.print(f"  Goal: {experiment['goal_type']} {experiment.get('goal_target')}")
                if experiment.get("baseline_value"):
                    console.print(f"  Baseline: {experiment['baseline_value']}")

            console.print()
            console.print(f"  Trials: {experiment.get('completed_trials', 0) or 0}/{experiment.get('total_trials', 0) or 0}")
            if experiment.get("best_score"):
                console.print(f"  Best Score: {experiment['best_score']:.6f}")
            if experiment.get("goal_achieved") is not None:
                ga = "[green]Yes[/green]" if experiment["goal_achieved"] else "[red]No[/red]"
                console.print(f"  Goal Achieved: {ga}")

            console.print()
            console.print(f"  Created: {experiment.get('created_at')}")
            if experiment.get("started_at"):
                console.print(f"  Started: {experiment['started_at']}")
            if experiment.get("completed_at"):
                console.print(f"  Completed: {experiment['completed_at']}")

    except ValueError as e:
        emit_error(str(e), json_output=json_output)
    except Exception as e:
        emit_error(f"Failed to get experiment status: {e}", json_output=json_output)


@experiment_app.command("run")
def experiment_run(
    experiment_id: int = typer.Option(..., "--id", "-i", help="Experiment ID to run"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Run an approved experiment."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        # Check experiment is approved before running
        experiment = runner.get(experiment_id)
        if experiment["status"] != "approved":
            emit_error(
                f"Experiment {experiment_id} has status '{experiment['status']}'. "
                "Only 'approved' experiments can be run.",
                json_output=json_output,
            )
            raise typer.Exit(1)

        if not json_output:
            console = Console()
            console.print(f"[bold]Running experiment #{experiment_id}:[/bold] {experiment['name']}")
            console.print()

        # Run the experiment
        results = runner.run(experiment_id)

        if json_output:
            emit_json(results)
        else:
            console.print("[green]Experiment completed![/green]\n")
            console.print(f"  Trials completed: {results['completed_trials']}")
            if results.get("best_score") is not None:
                console.print(f"  Best score: {results['best_score']:.6f}")
            if results.get("best_params"):
                console.print(f"  Best params: {results['best_params']}")
            if results.get("goal_achieved") is not None:
                ga = "[green]Yes[/green]" if results["goal_achieved"] else "[red]No[/red]"
                console.print(f"  Goal achieved: {ga}")

    except ValueError as e:
        emit_error(str(e), json_output=json_output)
        raise typer.Exit(1)
    except Exception as e:
        emit_error(f"Experiment failed: {e}", json_output=json_output)
        raise typer.Exit(1)


@experiment_app.command("results")
def experiment_results(
    experiment_id: int = typer.Option(..., "--id", "-i", help="Experiment ID"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    show_trials: bool = typer.Option(False, "--trials", "-t", help="Show all trial details"),
) -> None:
    """Get results for a completed experiment."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        results = runner.get_results(experiment_id)

        if results["status"] != "completed":
            emit_error(
                f"Experiment {experiment_id} has status '{results['status']}'. "
                "Results are only available for 'completed' experiments.",
                json_output=json_output,
            )
            raise typer.Exit(1)

        if json_output:
            emit_json(results)
        else:
            console = Console()
            console.print(f"[bold]Results for Experiment #{experiment_id}[/bold]\n")

            # Extract best params and score from results
            result_data = results.get("results") or {}
            best_params = result_data.get("best_params")
            best_score = results.get("best_score")

            console.print(f"  Status: [green]{results['status']}[/green]")
            console.print(f"  Trials: {results['completed_trials']}/{results.get('total_trials', 'N/A')}")

            if best_score is not None:
                console.print(f"  Best Score: [bold cyan]{best_score:.6f}[/bold cyan]")

            if best_params:
                console.print(f"  Best Params: {best_params}")

            if results.get("goal_achieved") is not None:
                ga = "[green]Yes[/green]" if results["goal_achieved"] else "[red]No[/red]"
                console.print(f"  Goal Achieved: {ga}")

            if show_trials:
                console.print("\n[bold]Trial Details:[/bold]")
                # Query trials from database
                import psycopg
                with psycopg.connect(db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT trial_number, params, metrics, score, duration_seconds
                            FROM experiment_trials
                            WHERE experiment_id = %s
                            ORDER BY trial_number
                        """, (experiment_id,))
                        for row in cur.fetchall():
                            trial_num, params, metrics, score, duration = row
                            score_str = f"{float(score):.6f}" if score is not None else "N/A"
                            console.print(f"  Trial {trial_num}: score={score_str} params={params}")

    except ValueError as e:
        emit_error(str(e), json_output=json_output)
        raise typer.Exit(1)
    except Exception as e:
        emit_error(f"Failed to get experiment results: {e}", json_output=json_output)
        raise typer.Exit(1)


@experiment_app.command("chain")
def experiment_chain(
    parent_id: int = typer.Option(..., "--parent", "-p", help="Parent experiment ID"),
    name: str = typer.Option(..., "--name", "-n", help="Name for child experiment"),
    search_space: str = typer.Option(
        ..., "--search-space", "-s",
        help='JSON search space for child experiment'
    ),
    depends_on: str = typer.Option(
        "best_params", "--depends-on", "-d",
        help="Parent output to use (best_params, best_score)"
    ),
    strategy: Optional[str] = typer.Option(None, "--strategy", help="Strategy name"),
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbols"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="Start date"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date"),
    max_trials: int = typer.Option(50, "--max-trials", help="Maximum trials"),
    search_method: str = typer.Option("grid", "--search-method", "-m", help="Search method"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Create a child experiment chained to a parent."""
    from gefion.experiments.core import ExperimentConfig, ExperimentRunner

    try:
        search_space_dict = json.loads(search_space)
    except json.JSONDecodeError as e:
        emit_error(f"Invalid JSON in search-space: {e}", json_output=json_output)
        raise typer.Exit(1)

    extra_config = {}
    if strategy:
        extra_config["strategy"] = strategy

    child_config = ExperimentConfig(
        name=name,
        experiment_type="strategy_params",
        search_space=search_space_dict,
        max_trials=max_trials,
        search_method=search_method,
        symbols=parse_comma_separated(symbols) if symbols else None,
        start_date=start_date,
        end_date=end_date,
        extra_config=extra_config,
    )

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        child_id = runner.chain(parent_id, child_config, depends_on=depends_on)

        if json_output:
            emit_json({
                "status": "proposed",
                "child_id": child_id,
                "parent_id": parent_id,
                "depends_on": depends_on,
            })
        else:
            console = Console()
            console.print(f"[green]Child experiment #{child_id} created![/green]")
            console.print(f"  Parent: #{parent_id}")
            console.print(f"  Depends on: {depends_on}")
            console.print(f"  Status: proposed")
            console.print("\nApprove with: gefion experiment approve --id", child_id)

    except ValueError as e:
        emit_error(str(e), json_output=json_output)
        raise typer.Exit(1)
    except Exception as e:
        emit_error(f"Failed to chain experiment: {e}", json_output=json_output)
        raise typer.Exit(1)


@experiment_app.command("children")
def experiment_children(
    parent_id: int = typer.Option(..., "--parent", "-p", help="Parent experiment ID"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """List child experiments of a parent."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        children = runner.list_children(parent_id)

        if json_output:
            # Convert datetime objects to strings
            for child in children:
                for key in ["created_at", "completed_at"]:
                    if child.get(key):
                        child[key] = str(child[key])
            emit_json(children)
        else:
            console = Console()
            if not children:
                console.print(f"No child experiments for parent #{parent_id}")
                return

            console.print(f"[bold]Children of Experiment #{parent_id}[/bold]\n")
            for child in children:
                status_color = {
                    "proposed": "yellow",
                    "approved": "blue",
                    "running": "cyan",
                    "completed": "green",
                    "failed": "red",
                }.get(child["status"], "white")

                score_str = f" (score: {child['best_score']:.4f})" if child.get("best_score") else ""
                console.print(
                    f"  #{child['id']} {child['name']} "
                    f"[{status_color}]{child['status']}[/{status_color}]{score_str}"
                )
                console.print(f"      Depends on: {child['depends_on']}")

    except Exception as e:
        emit_error(f"Failed to list children: {e}", json_output=json_output)
        raise typer.Exit(1)


@experiment_app.command("parent")
def experiment_parent(
    experiment_id: int = typer.Option(..., "--id", "-i", help="Experiment ID"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Show parent experiment and its results for a chained experiment."""
    from gefion.experiments.core import ExperimentRunner

    db_url = str(SETTINGS.database_url)
    runner = ExperimentRunner(db_url)

    try:
        parent_results = runner.get_parent_results(experiment_id)

        if parent_results is None:
            if json_output:
                emit_json({"parent": None})
            else:
                console = Console()
                console.print(f"Experiment #{experiment_id} has no parent.")
            return

        if json_output:
            emit_json(parent_results)
        else:
            console = Console()
            console.print(f"[bold]Parent of Experiment #{experiment_id}[/bold]\n")
            console.print(f"  Parent ID: #{parent_results['experiment_id']}")
            console.print(f"  Name: {parent_results['name']}")
            console.print(f"  Status: {parent_results['status']}")
            console.print(f"  Depends on: {parent_results['depends_on']}")

            if parent_results.get("best_score") is not None:
                console.print(f"  Best Score: {parent_results['best_score']:.6f}")
            if parent_results.get("best_params"):
                console.print(f"  Best Params: {parent_results['best_params']}")

    except Exception as e:
        emit_error(f"Failed to get parent: {e}", json_output=json_output)
        raise typer.Exit(1)


# ==============================================================================
# Chart Commands
# ==============================================================================


@chart_app.command("price")
def chart_price(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g., AAPL)"),
    start_date: Optional[str] = typer.Option(None, "--start-date", "-s", help="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", "-e", help="End date (YYYY-MM-DD)"),
    indicators: Optional[str] = typer.Option(None, "--indicators", "-i", help="Comma-separated indicators to overlay"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate candlestick price chart for a symbol."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart, fetch_features_for_chart
        from gefion.charts.renderers import create_candlestick_chart
        from gefion.charts.analysis import compute_price_insights
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        emit("Install with: pip install 'gefion[charts]'", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime

    start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    with db_connection(None) as conn:
        ohlcv = fetch_ohlcv_for_chart(conn, symbol.upper(), start, end)

        if not ohlcv:
            emit(f"No data found for {symbol}", json_output=json_output, error=True)
            raise typer.Exit(1)

        # Fetch indicators if requested
        indicator_data = None
        if indicators:
            feature_names = [f.strip() for f in indicators.split(",")]
            indicator_data = fetch_features_for_chart(conn, symbol.upper(), feature_names, start, end)

        # Compute insights for rich context (before chart so we can display on chart)
        insights = compute_price_insights(ohlcv, indicator_data)

        # Create chart with insights panel
        fig = create_candlestick_chart(ohlcv, symbol.upper(), indicators=indicator_data, insights=insights)

        # Save chart
        filename = generate_chart_filename(symbol.upper(), "price")
        chart_path = save_chart_html(fig, filename)

        if json_output:
            emit_json({
                "status": "ok",
                "chart_path": str(chart_path),
                "chart_type": "price",
                "symbol": symbol.upper(),
                "date_range": {
                    "start": str(ohlcv[0]["date"]) if ohlcv else None,
                    "end": str(ohlcv[-1]["date"]) if ohlcv else None,
                },
                "summary": insights,
                "data_points": len(ohlcv),
            })
        else:
            emit(f"✓ Chart saved: {chart_path}")
            for insight in insights.get("insights", []):
                emit(f"  - {insight}")

        if not no_open and not json_output:
            open_in_browser(chart_path)


@chart_app.command("predictions")
def chart_predictions(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g., AAPL)"),
    model: str = typer.Option(..., "--model", "-m", help="Model name for predictions"),
    horizon: int = typer.Option(7, "--horizon", "-h", help="Prediction horizon in days"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate price chart with prediction bands (q10/q50/q90)."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart, fetch_predictions_for_chart
        from gefion.charts.renderers import create_prediction_chart
        from gefion.charts.analysis import compute_prediction_insights
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        emit("Install with: pip install 'gefion[charts]'", json_output=json_output, error=True)
        raise typer.Exit(1)

    with db_connection(None) as conn:
        ohlcv = fetch_ohlcv_for_chart(conn, symbol.upper())
        predictions = fetch_predictions_for_chart(conn, symbol.upper(), model, horizon)

        if not ohlcv:
            emit(f"No price data found for {symbol}", json_output=json_output, error=True)
            raise typer.Exit(1)

        if not predictions:
            emit(f"No predictions found for {symbol} with model {model}", json_output=json_output, error=True)
            raise typer.Exit(1)

        current_price = ohlcv[-1]["close"]

        # Create chart
        fig = create_prediction_chart(ohlcv, predictions, symbol.upper())

        # Compute insights for rich context
        insights = compute_prediction_insights(predictions, current_price)

        # Save chart
        filename = generate_chart_filename(symbol.upper(), "predictions")
        chart_path = save_chart_html(fig, filename)

        if json_output:
            emit_json({
                "status": "ok",
                "chart_path": str(chart_path),
                "chart_type": "predictions",
                "symbol": symbol.upper(),
                "model": model,
                "horizon_days": horizon,
                "summary": {
                    "description": insights["description"],
                    "current_price": current_price,
                    "predicted_median": insights["predicted_median"],
                    "prediction_range": insights["prediction_range"],
                    "confidence_width": insights["confidence_width"],
                },
                "insights": insights["insights"],
            })
        else:
            emit(f"✓ Chart saved: {chart_path}")
            for insight in insights.get("insights", []):
                emit(f"  - {insight}")

        if not no_open and not json_output:
            open_in_browser(chart_path)


@chart_app.command("features")
def chart_features(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g., AAPL)"),
    features: str = typer.Option(..., "--features", "-f", help="Comma-separated feature names"),
    start_date: Optional[str] = typer.Option(None, "--start-date", "-s", help="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", "-e", help="End date (YYYY-MM-DD)"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate price chart with feature overlays."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart, fetch_features_for_chart
        from gefion.charts.renderers import create_feature_chart
        from gefion.charts.analysis import compute_price_insights, detect_technical_signals
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        emit("Install with: pip install 'gefion[charts]'", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime

    start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    feature_names = [f.strip() for f in features.split(",")]

    with db_connection(None) as conn:
        ohlcv = fetch_ohlcv_for_chart(conn, symbol.upper(), start, end)
        feature_data = fetch_features_for_chart(conn, symbol.upper(), feature_names, start, end)

        if not ohlcv:
            emit(f"No price data found for {symbol}", json_output=json_output, error=True)
            raise typer.Exit(1)

        # Check if any features have data
        features_with_data = {k: v for k, v in feature_data.items() if v}
        if not features_with_data:
            emit(f"No feature data found for {symbol}", json_output=json_output, error=True)
            raise typer.Exit(1)

        # Create chart
        fig = create_feature_chart(ohlcv, features_with_data, symbol.upper())

        # Compute insights
        price_insights = compute_price_insights(ohlcv, features_with_data)
        tech_signals = detect_technical_signals(ohlcv, features_with_data)

        # Save chart
        filename = generate_chart_filename(symbol.upper(), "features")
        chart_path = save_chart_html(fig, filename)

        if json_output:
            emit_json({
                "status": "ok",
                "chart_path": str(chart_path),
                "chart_type": "features",
                "symbol": symbol.upper(),
                "features_shown": list(features_with_data.keys()),
                "date_range": {
                    "start": str(ohlcv[0]["date"]) if ohlcv else None,
                    "end": str(ohlcv[-1]["date"]) if ohlcv else None,
                },
                "summary": price_insights,
                "technical_signals": tech_signals,
                "data_points": len(ohlcv),
            })
        else:
            emit(f"✓ Chart saved: {chart_path}")
            emit(f"  Features: {', '.join(features_with_data.keys())}")
            for signal in tech_signals:
                emit(f"  - {signal}")

        if not no_open and not json_output:
            open_in_browser(chart_path)


@chart_app.command("compare")
def chart_compare(
    symbols: str = typer.Argument(..., help="Comma-separated symbols (e.g., NVDA,AMD)"),
    start_date: Optional[str] = typer.Option(None, "--start-date", "-s", help="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", "-e", help="End date (YYYY-MM-DD)"),
    period: Optional[str] = typer.Option("1y", "--period", "-p", help="Period: 1m, 3m, 6m, 1y, 2y, 5y, max"),
    no_normalize: bool = typer.Option(False, "--no-normalize", help="Show actual prices instead of normalized"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Compare price performance of multiple symbols."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart
        from gefion.charts.renderers import create_comparison_chart
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        emit("Install with: pip install 'gefion[charts]'", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime, timedelta

    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    if len(symbol_list) < 2:
        emit("Please provide at least 2 symbols to compare", json_output=json_output, error=True)
        raise typer.Exit(1)

    # Calculate date range from period if not specified
    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else datetime.now().date()
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "2y": 730, "5y": 1825, "max": 36500}
        days = period_days.get(period, 365)
        start = end - timedelta(days=days)

    symbol_data = {}
    with db_connection(None) as conn:
        for symbol in symbol_list:
            ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
            if ohlcv:
                symbol_data[symbol] = ohlcv
            else:
                emit(f"Warning: No data found for {symbol}", json_output=json_output)

    if len(symbol_data) < 2:
        emit("Need at least 2 symbols with data to compare", json_output=json_output, error=True)
        raise typer.Exit(1)

    # Create comparison chart
    fig = create_comparison_chart(symbol_data, normalize=not no_normalize)

    # Calculate performance metrics
    performance = {}
    for sym, data in symbol_data.items():
        if data:
            sorted_data = sorted(data, key=lambda x: x["date"])
            start_price = sorted_data[0]["close"]
            end_price = sorted_data[-1]["close"]
            total_return = ((end_price / start_price) - 1) * 100 if start_price > 0 else 0
            performance[sym] = {
                "start_price": start_price,
                "end_price": end_price,
                "total_return": total_return,
                "data_points": len(data),
            }

    # Save chart
    filename = generate_chart_filename("_".join(symbol_list[:3]), "compare")
    chart_path = save_chart_html(fig, filename)

    if json_output:
        emit_json({
            "status": "ok",
            "chart_path": str(chart_path),
            "chart_type": "comparison",
            "symbols": list(symbol_data.keys()),
            "period": period,
            "date_range": {
                "start": str(start),
                "end": str(end),
            },
            "performance": performance,
        })
    else:
        emit(f"✓ Comparison chart saved: {chart_path}")
        emit(f"  Symbols: {', '.join(symbol_data.keys())}")
        emit(f"  Period: {start} to {end}")
        for sym, perf in performance.items():
            emit(f"  {sym}: {perf['total_return']:+.1f}% (${perf['start_price']:.2f} → ${perf['end_price']:.2f})")

    if not no_open and not json_output:
        open_in_browser(chart_path)


@chart_app.command("correlation")
def chart_correlation(
    symbols: str = typer.Argument(..., help="Comma-separated symbols (e.g., AAPL,MSFT,GOOGL,AMZN)"),
    period: Optional[str] = typer.Option("1y", "--period", "-p", help="Period: 1m, 3m, 6m, 1y, 2y, 5y"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate correlation matrix heatmap for multiple symbols."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart
        from gefion.charts.renderers import create_correlation_matrix
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime, timedelta

    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    if len(symbol_list) < 2:
        emit("Please provide at least 2 symbols", json_output=json_output, error=True)
        raise typer.Exit(1)

    end = datetime.now().date()
    period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "2y": 730, "5y": 1825}
    start = end - timedelta(days=period_days.get(period, 365))

    symbol_data = {}
    with db_connection(None) as conn:
        for symbol in symbol_list:
            ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
            if ohlcv:
                symbol_data[symbol] = ohlcv

    if len(symbol_data) < 2:
        emit("Need at least 2 symbols with data", json_output=json_output, error=True)
        raise typer.Exit(1)

    fig = create_correlation_matrix(symbol_data)
    filename = generate_chart_filename("correlation", "matrix")
    chart_path = save_chart_html(fig, filename)

    if json_output:
        emit_json({"status": "ok", "chart_path": str(chart_path), "symbols": list(symbol_data.keys())})
    else:
        emit(f"✓ Correlation matrix saved: {chart_path}")

    if not no_open and not json_output:
        open_in_browser(chart_path)


@chart_app.command("sector")
def chart_sector(
    exchange: Optional[str] = typer.Option("NASDAQ", "--exchange", "-x", help="Exchange to analyze"),
    limit: Optional[int] = typer.Option(50, "--limit", "-l", help="Max symbols per sector"),
    period: Optional[str] = typer.Option("1m", "--period", "-p", help="Period: 1w, 1m, 3m, 6m, 1y"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate sector performance heatmap."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart
        from gefion.charts.renderers import create_sector_heatmap
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime, timedelta

    end = datetime.now().date()
    period_days = {"1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365}
    start = end - timedelta(days=period_days.get(period, 30))

    sector_data: Dict[str, Dict[str, float]] = {}

    with db_connection(None) as conn:
        # Get symbols with sector info
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, COALESCE(sector, 'Unknown') as sector
                FROM stocks
                WHERE status = 'Active' AND sector IS NOT NULL
                ORDER BY sector, symbol
            """)
            rows = cur.fetchall()

        sector_symbols: Dict[str, List[str]] = {}
        for symbol, sector in rows:
            if sector not in sector_symbols:
                sector_symbols[sector] = []
            if len(sector_symbols[sector]) < limit:
                sector_symbols[sector].append(symbol)

        # Calculate returns for each symbol
        for sector, symbols in sector_symbols.items():
            sector_data[sector] = {}
            for symbol in symbols:
                ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                if ohlcv and len(ohlcv) >= 2:
                    start_price = ohlcv[0]["close"]
                    end_price = ohlcv[-1]["close"]
                    if start_price > 0:
                        ret = ((end_price / start_price) - 1) * 100
                        sector_data[sector][symbol] = ret

        # Remove empty sectors
        sector_data = {k: v for k, v in sector_data.items() if v}

    if not sector_data:
        emit("No sector data found", json_output=json_output, error=True)
        raise typer.Exit(1)

    fig = create_sector_heatmap(sector_data)
    filename = generate_chart_filename("sector", "heatmap")
    chart_path = save_chart_html(fig, filename)

    if json_output:
        emit_json({"status": "ok", "chart_path": str(chart_path), "sectors": list(sector_data.keys())})
    else:
        emit(f"✓ Sector heatmap saved: {chart_path}")
        emit(f"  Sectors: {len(sector_data)}, Symbols: {sum(len(v) for v in sector_data.values())}")

    if not no_open and not json_output:
        open_in_browser(chart_path)


@chart_app.command("volatility")
def chart_volatility(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g., AAPL)"),
    period: Optional[str] = typer.Option("1y", "--period", "-p", help="Period: 3m, 6m, 1y, 2y"),
    window: int = typer.Option(20, "--window", "-w", help="Lookback window for calculations"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate volatility analysis chart (Bollinger Bands, ATR, Historical Vol)."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart
        from gefion.charts.renderers import create_volatility_chart
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime, timedelta

    end = datetime.now().date()
    period_days = {"3m": 90, "6m": 180, "1y": 365, "2y": 730}
    start = end - timedelta(days=period_days.get(period, 365))

    with db_connection(None) as conn:
        ohlcv = fetch_ohlcv_for_chart(conn, symbol.upper(), start, end)

    if not ohlcv:
        emit(f"No data found for {symbol}", json_output=json_output, error=True)
        raise typer.Exit(1)

    fig = create_volatility_chart(ohlcv, symbol.upper(), window=window)
    filename = generate_chart_filename(symbol.upper(), "volatility")
    chart_path = save_chart_html(fig, filename)

    if json_output:
        emit_json({"status": "ok", "chart_path": str(chart_path), "symbol": symbol.upper()})
    else:
        emit(f"✓ Volatility chart saved: {chart_path}")

    if not no_open and not json_output:
        open_in_browser(chart_path)


@chart_app.command("drawdown")
def chart_drawdown(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g., AAPL)"),
    period: Optional[str] = typer.Option("2y", "--period", "-p", help="Period: 1y, 2y, 5y, max"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate drawdown analysis chart."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart
        from gefion.charts.renderers import create_drawdown_chart
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime, timedelta

    end = datetime.now().date()
    period_days = {"1y": 365, "2y": 730, "5y": 1825, "max": 36500}
    start = end - timedelta(days=period_days.get(period, 730))

    with db_connection(None) as conn:
        ohlcv = fetch_ohlcv_for_chart(conn, symbol.upper(), start, end)

    if not ohlcv:
        emit(f"No data found for {symbol}", json_output=json_output, error=True)
        raise typer.Exit(1)

    fig = create_drawdown_chart(ohlcv, symbol.upper())
    filename = generate_chart_filename(symbol.upper(), "drawdown")
    chart_path = save_chart_html(fig, filename)

    if json_output:
        emit_json({"status": "ok", "chart_path": str(chart_path), "symbol": symbol.upper()})
    else:
        emit(f"✓ Drawdown chart saved: {chart_path}")

    if not no_open and not json_output:
        open_in_browser(chart_path)


@chart_app.command("rolling")
def chart_rolling(
    symbols: str = typer.Argument(..., help="Comma-separated symbols (e.g., NVDA,AMD)"),
    period: Optional[str] = typer.Option("1y", "--period", "-p", help="Period: 6m, 1y, 2y"),
    windows: Optional[str] = typer.Option("30,60,90", "--windows", "-w", help="Rolling windows in days"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open in browser"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output JSON instead of chart"),
) -> None:
    """Generate rolling returns comparison chart."""
    try:
        from gefion.charts.queries import fetch_ohlcv_for_chart
        from gefion.charts.renderers import create_rolling_returns_chart
        from gefion.charts.output import save_chart_html, open_in_browser, generate_chart_filename
    except ImportError as e:
        emit(f"Charts not available: {e}", json_output=json_output, error=True)
        raise typer.Exit(1)

    from datetime import datetime, timedelta

    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    window_list = [int(w.strip()) for w in windows.split(",")]

    end = datetime.now().date()
    period_days = {"6m": 180, "1y": 365, "2y": 730}
    start = end - timedelta(days=period_days.get(period, 365))

    symbol_data = {}
    with db_connection(None) as conn:
        for symbol in symbol_list:
            ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
            if ohlcv:
                symbol_data[symbol] = ohlcv

    if not symbol_data:
        emit("No data found for any symbols", json_output=json_output, error=True)
        raise typer.Exit(1)

    fig = create_rolling_returns_chart(symbol_data, windows=window_list)
    filename = generate_chart_filename("_".join(symbol_list[:3]), "rolling")
    chart_path = save_chart_html(fig, filename)

    if json_output:
        emit_json({"status": "ok", "chart_path": str(chart_path), "symbols": list(symbol_data.keys())})
    else:
        emit(f"✓ Rolling returns chart saved: {chart_path}")

    if not no_open and not json_output:
        open_in_browser(chart_path)


@app.command("ui")
def launch_ui(
    port: int = typer.Option(8501, "--port", "-p", help="Port to run the UI on"),
    host: str = typer.Option("localhost", "--host", "-h", help="Host to bind to"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open browser"),
) -> None:
    """Launch the Streamlit web UI.

    Opens an interactive web interface for Gefion with:
    - Charts and visualizations
    - AI-powered analysis (Claude)
    - ML pipeline management
    - Backtesting tools

    Examples:
        gefion ui                    # Launch on default port 8501
        gefion ui --port 8080        # Use custom port
        gefion ui --no-browser       # Don't open browser automatically
    """
    with create_span("cli.ui", port=port):
        import subprocess
        import sys
        from pathlib import Path
        from datetime import datetime, timezone

        from gefion.ui.errors import clear_errors, read_session_errors

        # Find the app.py file
        ui_app = Path(__file__).parent / "ui" / "app.py"

        if not ui_app.exists():
            emit("UI app not found. Please reinstall gefion.", error=True)
            raise typer.Exit(1)

        emit(f"Starting Gefion UI on http://{host}:{port}")
        emit("Press Ctrl+C to stop")

        cmd = [
            sys.executable, "-m", "streamlit", "run",
            str(ui_app),
            "--server.port", str(port),
            "--server.address", host,
            "--theme.primaryColor", "#2962ff",
            "--theme.backgroundColor", "#ffffff",
            "--theme.secondaryBackgroundColor", "#f0f2f6",
        ]

        if no_browser:
            cmd.extend(["--server.headless", "true"])

        session_start = datetime.now(timezone.utc)
        clear_errors()

        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            emit("\nShutting down UI...")
        except subprocess.CalledProcessError as e:
            emit(f"UI failed to start: {e}", error=True)
            raise typer.Exit(1)
        except FileNotFoundError:
            emit("Streamlit not installed. Install with: pip install 'gefion[ui]'", error=True)
            raise typer.Exit(1)

        # Print error summary if any errors were logged during the session
        errors = read_session_errors(since=session_start)
        if errors:
            emit(f"\n--- UI Session Errors ({len(errors)}) ---")
            for err in errors:
                emit(f"  ({err['source']}) {err['message']}")
                if err.get("context"):
                    for k, v in err["context"].items():
                        emit(f"    {k}: {v}")


# =============================================================================
# DATA MANAGEMENT COMMANDS
# =============================================================================

@data_app.command("cull")
def data_cull(
    before: str = typer.Argument(..., help="Delete data before this date (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols to filter (default: all)"),
    dry_run: bool = typer.Option(True, "--dry-run/--confirm", help="Preview changes (default) or execute"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output as JSON"),
) -> None:
    """Delete old data in dependency order: predictions → features → OHLCV.

    By default, runs in dry-run mode showing what would be deleted.
    Pass --confirm to actually execute the deletion.
    """
    from datetime import date as date_type, datetime
    from gefion.db.cull import plan_cull, execute_cull
    from gefion.cli_helpers import db_connection

    try:
        before_date = datetime.strptime(before, "%Y-%m-%d").date()
    except ValueError:
        emit_error(f"Invalid date format: {before}. Expected YYYY-MM-DD.", json_output=json_output)
        raise typer.Exit(1)

    symbol_list = [s.strip() for s in symbols.split(",")] if symbols else None

    try:
        with db_connection(None) as conn:
            if dry_run:
                plan = plan_cull(conn, before_date=before_date, symbols=symbol_list)

                if json_output:
                    emit("Data Cull Plan (dry run)", data={
                        "before_date": str(before_date),
                        "symbols": symbol_list,
                        "tables": plan,
                        "total_rows": sum(plan.values()),
                        "dry_run": True,
                    }, json_output=True)
                else:
                    from rich.console import Console
                    from rich.table import Table
                    console = Console()

                    if not plan:
                        console.print(f"\n[green]No data found before {before_date}.[/green]")
                        return

                    table = Table(title=f"Data Cull Plan — before {before_date} (DRY RUN)")
                    table.add_column("Table", style="cyan")
                    table.add_column("Rows to Delete", justify="right", style="red")

                    for tbl, count in plan.items():
                        table.add_row(tbl, f"{count:,}")

                    table.add_row("[bold]Total[/bold]", f"[bold]{sum(plan.values()):,}[/bold]")
                    console.print(table)
                    console.print("\n[dim]Run with --confirm to execute.[/dim]")
            else:
                result = execute_cull(conn, before_date=before_date, symbols=symbol_list)

                if json_output:
                    emit("Data Cull Complete", data={
                        "before_date": str(before_date),
                        "symbols": symbol_list,
                        "deleted": result,
                        "total_rows": sum(result.values()),
                        "dry_run": False,
                    }, json_output=True)
                else:
                    from rich.console import Console
                    from rich.table import Table
                    console = Console()

                    if not result:
                        console.print(f"\n[green]No data found before {before_date}.[/green]")
                        return

                    table = Table(title=f"Data Cull Results — before {before_date}")
                    table.add_column("Table", style="cyan")
                    table.add_column("Rows Deleted", justify="right", style="red")

                    for tbl, count in result.items():
                        table.add_row(tbl, f"{count:,}")

                    table.add_row("[bold]Total[/bold]", f"[bold]{sum(result.values()):,}[/bold]")
                    console.print(table)

    except Exception as exc:
        import traceback
        emit_error(f"Data cull failed: {exc}", json_output=json_output)
        if os.environ.get("DEBUG"):
            traceback.print_exc()
        raise typer.Exit(1)


def entrypoint() -> None:  # pragma: no cover - thin wrapper
    import atexit
    # Register shutdown handler to flush traces on exit
    atexit.register(otel_shutdown)

    try:
        app()
    finally:
        # Ensure traces are flushed even on early exit
        otel_shutdown()


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
