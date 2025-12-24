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

from g2.alphavantage.catalog import parse_daily_adjusted
from g2.alphavantage.client import AlphaVantageClient
from g2.cli_helpers import (
    parse_comma_separated,
    upsert_feature_function as upsert_feature_function_helper,
    db_connection,
    init_schema_tables,
)
from g2.features.dispatcher import compute_features
from g2.config import load_settings
from g2.db import schema
from psycopg.types.json import Json
from g2.observability import create_span, set_attributes, add_event, get_current_span, shutdown as otel_shutdown
from g2.db import migrate
from g2.db.ingest import (
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

ml_app = typer.Typer(help="ML workflow commands (dataset/build/train/predict/eval)")
app.add_typer(ml_app, name="ml", cls=SortedGroup)

backtest_app = typer.Typer(help="Backtesting commands (run/compare/analyze)")
app.add_typer(backtest_app, name="backtest", cls=SortedGroup)


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
                    "quantile_predictions",
                    "trend_class_predictions",
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
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Create a dataset manifest and register it in ml_datasets.

    Examples:
        # Build dataset for specific symbols
        g2 ml dataset-build --name tech_stocks --version v1 --symbols AAPL,MSFT,GOOGL

        # Build dataset for NASDAQ exchange (limited to 50 stocks)
        g2 ml dataset-build --name nasdaq_50 --version 2025-01 --exchange NASDAQ --limit 50

        # Build with custom horizons and thresholds
        g2 ml dataset-build --name custom --version v1 --symbols AAPL,MSFT \\
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

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / f"{name}_{version}.json"
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

    from g2.ml.store import sha256_text, upsert_ml_dataset

    payload = dict(manifest)
    payload["checksum"] = sha256_text(manifest_text)

    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["ml_datasets"])
        dataset_id = upsert_ml_dataset(conn, payload)
        if export:
            from g2.ml.dataset import export_dataset_artifacts

            export_dataset_artifacts(conn, manifest=manifest, out_dir=out_dir)

    emit(
        f"Dataset registered: {name} {version}",
        data={"dataset_id": dataset_id, "artifact_uri": str(manifest_path)},
        json_output=json_output,
    )


@ml_app.command("train")
def ml_train(
    dataset_name: str = typer.Option(..., help="Dataset name to train on"),
    dataset_version: str = typer.Option(..., help="Dataset version"),
    model_name: str = typer.Option(..., help="Model name (identifier)"),
    model_version: str = typer.Option(..., help="Model version (e.g., date tag)"),
    algorithm: str = typer.Option("quantile_regression", help="Algorithm: quantile_regression, xgboost, lightgbm"),
    out_dir: Path = typer.Option(Path("models"), help="Output directory for model artifacts"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Train a quantile regression model for multi-horizon return prediction.

    Examples:
        # Train a quantile regression model on a dataset
        g2 ml train --dataset-name tech_stocks --dataset-version v1 \\
            --model-name tech_qr --model-version v1

        # Train using XGBoost algorithm
        g2 ml train --dataset-name nasdaq_50 --dataset-version 2025-01 \\
            --model-name nasdaq_xgb --model-version v1 --algorithm xgboost

        # Train with custom output directory
        g2 ml train --dataset-name custom --dataset-version v1 \\
            --model-name custom_model --model-version v1 --out-dir ./my_models
    """
    from g2.ml.store import get_ml_dataset
    from g2.ml.models import load_dataset_from_csv, train_quantile_model, save_model_artifact

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

        emit(f"Training {algorithm} models for horizons: {horizons}", json_output=json_output)

        for horizon in horizons:
            emit(f"Training model for {horizon}-day horizon...", json_output=json_output)

            # Load features and labels for this horizon
            X, y = load_dataset_from_csv(artifact_uri, horizon)
            emit(f"  Loaded {len(X)} samples with {X.shape[1]} features", json_output=json_output)

            # Train quantile models (q10, q50, q90)
            model_data = train_quantile_model(X, y, algorithm=algorithm)
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
    prediction_date: str = typer.Option(..., help="Date to generate predictions for (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbol list (optional)"),
    exchange: Optional[str] = typer.Option(None, help="Exchange name for universe selection (optional)"),
    limit: Optional[int] = typer.Option(None, help="Optional universe limit (exchange mode)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """
    Generate predictions using a trained model.

    Examples:
        # Generate predictions for specific symbols
        g2 ml predict --model-name tech_qr --model-version v1 \\
            --prediction-date 2025-01-15 --symbols AAPL,MSFT,GOOGL

        # Generate predictions for NASDAQ universe
        g2 ml predict --model-name nasdaq_xgb --model-version v1 \\
            --prediction-date 2025-01-15 --exchange NASDAQ --limit 50
    """
    import pandas as pd
    from g2.ml.models import load_model_artifact, predict_quantiles
    from g2.ml.store import get_ml_dataset

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
        if exchange:
            with conn.cursor() as cur:
                limit_clause = f"LIMIT {limit}" if limit else ""
                cur.execute(
                    f"""
                    SELECT DISTINCT s.id, s.symbol
                    FROM stocks s
                    WHERE s.exchange = %s
                    {limit_clause};
                    """,
                    (exchange,),
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

        emit(f"Generating predictions for {len(universe)} symbols on {prediction_date}", json_output=json_output)

        # Fetch features for all symbols on prediction_date
        data_ids = [u[0] for u in universe]
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
                (data_ids, prediction_date, feature_names),
            )
            features_data = cur.fetchall()

        if not features_data:
            emit_error(f"No features found for {prediction_date}", json_output=json_output)
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
                VALUES ('predict', 'running', %s, %s, NOW())
                RETURNING id;
                """,
                (
                    dataset_id,
                    Json(
                        {
                            "model_name": model_name,
                            "model_version": model_version,
                            "prediction_date": prediction_date,
                            "universe": {"symbols": sym_list} if sym_list else {"exchange": exchange},
                        }
                    ),
                ),
            )
            run_id = int(cur.fetchone()[0])

        total_predictions = 0
        for horizon in horizons:
            emit(f"Predicting for {horizon}-day horizon...", json_output=json_output)

            # Load model for this horizon
            horizon_model_path = Path(artifact_uri) / f"_h{horizon}"
            model_data = load_model_artifact(horizon_model_path)

            # Generate predictions
            predictions = predict_quantiles(model_data, features_wide)

            # Insert predictions into database
            with conn.cursor() as cur:
                for data_id in predictions.index:
                    q10 = Decimal(str(predictions.loc[data_id, "q10"]))
                    q50 = Decimal(str(predictions.loc[data_id, "q50"]))
                    q90 = Decimal(str(predictions.loc[data_id, "q90"]))

                    cur.execute(
                        """
                        INSERT INTO quantile_predictions
                          (model_id, data_id, prediction_date, horizon_days, q10, q50, q90,
                           model_version, run_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (model_id, data_id, prediction_date, horizon_days)
                        DO UPDATE SET
                          q10 = EXCLUDED.q10,
                          q50 = EXCLUDED.q50,
                          q90 = EXCLUDED.q90,
                          model_version = EXCLUDED.model_version,
                          run_id = EXCLUDED.run_id,
                          created_at = NOW();
                        """,
                        (model_id, int(data_id), prediction_date, horizon, q10, q50, q90, model_version, run_id),
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

    emit(
        f"Predictions generated: {model_name} {model_version} for {prediction_date}",
        data={"model_id": model_id, "run_id": run_id, "prediction_date": prediction_date, "total_predictions": total_predictions, "horizons": horizons},
        json_output=json_output,
    )


@ml_app.command("eval")
def ml_eval(
    model_name: str = typer.Option(..., help="Model name to evaluate"),
    model_version: str = typer.Option(..., help="Model version"),
    start_date: str = typer.Option(..., help="Evaluation start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., help="Evaluation end date (YYYY-MM-DD)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Evaluate model performance on historical predictions (MVP placeholder)."""
    import pandas as pd
    from datetime import datetime, timedelta
    from decimal import Decimal
    from g2.ml.evaluation import calculate_calibration_metrics, generate_evaluation_report

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

        # Fetch predictions from quantile_predictions
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT qp.data_id, qp.prediction_date, qp.horizon_days, qp.q10, qp.q50, qp.q90,
                       s.symbol
                FROM quantile_predictions qp
                JOIN stocks s ON qp.data_id = s.id
                WHERE qp.model_id = %s
                  AND qp.prediction_date >= %s
                  AND qp.prediction_date <= %s
                ORDER BY qp.prediction_date, qp.data_id, qp.horizon_days;
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

        # Store metrics in model_performance (one row per horizon)
        # Note: model_performance has model_id as PRIMARY KEY, so we can only store one horizon
        # For now, store the first horizon's metrics
        if all_metrics:
            first_horizon = sorted(all_metrics.keys())[0]
            first_metrics = all_metrics[first_horizon]

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO model_performance
                      (model_id, model_name, horizon_days, q10_calibration, q50_calibration, q90_calibration,
                       quantile_loss, avg_iqr, eval_start_date, eval_end_date, num_predictions, eval_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (model_id) DO UPDATE SET
                      horizon_days = EXCLUDED.horizon_days,
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
                        first_horizon,
                        Decimal(str(first_metrics.get("q10_calibration", 0))),
                        Decimal(str(first_metrics.get("q50_calibration", 0))),
                        Decimal(str(first_metrics.get("q90_calibration", 0))),
                        Decimal(str(first_metrics.get("quantile_loss", 0))) if "quantile_loss" in first_metrics else None,
                        Decimal(str(first_metrics.get("avg_iqr", 0))) if "avg_iqr" in first_metrics else None,
                        start_date,
                        end_date,
                        first_metrics.get("num_samples", 0),
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


@ml_app.command("train-classifier")
def ml_train_classifier(
    dataset_name: str = typer.Option(..., help="Dataset name to train on"),
    dataset_version: str = typer.Option(..., help="Dataset version"),
    model_name: str = typer.Option(..., help="Model name (identifier)"),
    model_version: str = typer.Option(..., help="Model version (e.g., date tag)"),
    algorithm: str = typer.Option("sklearn", help="Algorithm: sklearn, xgboost, lightgbm"),
    horizon: int = typer.Option(..., help="Horizon in days for classification"),
    out_dir: Path = typer.Option(Path("models"), help="Output directory for model artifacts"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Train a multi-class classifier for trend prediction (5-class labels)."""
    import joblib
    from g2.ml.store import get_ml_dataset
    from g2.ml.classifier import load_dataset_for_classifier, train_classifier, evaluate_classifier

    with db_connection(db_url) as conn:
        # Fetch dataset manifest
        dataset = get_ml_dataset(conn, name=dataset_name, version=dataset_version)
        if not dataset:
            emit_error(f"Dataset not found: {dataset_name} {dataset_version}", json_output=json_output)
            return

        emit(f"Training {algorithm} classifier for {horizon}-day horizon...", json_output=json_output)

        # Load features and labels for this horizon
        artifact_uri = Path(dataset["artifact_uri"])
        X, y = load_dataset_for_classifier(artifact_uri, horizon)
        emit(f"  Loaded {len(X)} samples with {X.shape[1]} features", json_output=json_output)
        emit(f"  Label distribution: {y.value_counts().to_dict()}", json_output=json_output)

        # Train classifier
        model_artifacts = train_classifier(X, y, algorithm=algorithm)
        emit(f"  Training accuracy: {model_artifacts['train_metrics']['train_accuracy']:.4f}", json_output=json_output)

        # Evaluate
        eval_metrics = evaluate_classifier(model_artifacts, X, y)
        emit(f"  Accuracy: {eval_metrics['accuracy']:.4f}", json_output=json_output)

        # Save model artifact
        out_dir.mkdir(parents=True, exist_ok=True)
        model_path = out_dir / f"{model_name}_{model_version}_h{horizon}_classifier"
        joblib.dump(model_artifacts, model_path / "classifier.pkl")
        (model_path / "metadata.json").write_text(
            json.dumps({
                "model_name": model_name,
                "model_version": model_version,
                "horizon_days": horizon,
                "dataset_name": dataset_name,
                "dataset_version": dataset_version,
                "algorithm": algorithm,
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
    start_date: str = typer.Option(..., help="Start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., help="End date (YYYY-MM-DD)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (optional, predicts all if not specified)"),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL (defaults to env DATABASE_URL)"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result/error as JSON"),
) -> None:
    """Generate trend class predictions using a trained classifier."""
    import joblib
    from g2.ml.classifier import predict_classifier

    # Load model
    model_artifacts = joblib.load(model_path / "classifier.pkl")
    metadata_path = model_path / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        emit(f"Loaded classifier: {metadata['model_name']} {metadata['model_version']}", json_output=json_output)
        emit(f"  Horizon: {metadata['horizon_days']} days", json_output=json_output)
        emit(f"  Algorithm: {metadata['algorithm']}", json_output=json_output)

    with db_connection(db_url) as conn:
        init_schema_tables(conn, ["trend_class_predictions"])

        # Parse symbols
        symbol_list = parse_comma_separated(symbols) if symbols else None

        # TODO: Load features for the specified date range and symbols
        # For now, emit a message that this needs feature loading implementation
        emit(
            "Prediction implementation: Load features from database for date range and symbols, "
            "then call predict_classifier() and store results in trend_class_predictions table",
            json_output=json_output
        )
        emit("Note: Full prediction workflow requires feature loading - to be implemented", json_output=json_output)


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
        g2 prices-ingest --symbol AAPL

        # Ingest from a local JSON file
        g2 prices-ingest --symbol AAPL --input prices.json

        # Fetch full history and refresh existing data
        g2 prices-ingest --symbol MSFT --timeframe full --refresh-existing
    """
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
        from g2.ingest.universe import _expected_market_date
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
        g2 universe-ingest --exchange NASDAQ --limit 10

        # Full refresh of NYSE universe with custom rate limit
        g2 universe-ingest --exchange NYSE --refresh --calls-per-minute 75

        # Ingest from a saved listings file
        g2 universe-ingest --exchange NASDAQ --listings-file listings.csv
    """
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
    from g2.ingest.universe import _expected_market_date, filter_symbols_needing_update
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
    from g2.db.migrate import check_pending_migrations, get_applied_migrations
    from pathlib import Path as PathLib
    import g2

    url = _db_url(db_url)

    # Find migrations directory
    if migrations_dir is None:
        package_dir = PathLib(g2.__file__).parent.parent.parent
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
                        from g2.db.migrate import scan_migration_files
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
                            from g2.db.migrate import get_applied_migrations
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
                emit("  Run 'g2 db-migrate' to apply pending migrations")
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
    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "g2")

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

    app_span_count = sum(1 for s in spans if s["scope"] == "g2.observability")
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
    console.print(f"Application spans (g2.observability): {app_span_count}", style="dim")
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
    console.print('Query: service.name = "g2"', style="dim")


@app.command("span-check")
def span_check(
    backend: str = typer.Option("tempo", help="Trace backend (default: tempo)"),
    tempo_url: Optional[str] = typer.Option(None, help="Tempo base URL (default: $TEMPO_URL or http://localhost:3200)"),
    service_name: Optional[str] = typer.Option(None, help="Service name tag (default: $OTEL_SERVICE_NAME or g2)"),
    limit: int = typer.Option(10, min=1, max=100, help="Number of recent traces to inspect"),
    trace_id: Optional[str] = typer.Option(None, help="Specific trace ID to inspect (default: most recent)"),
    show_spans: bool = typer.Option(True, "--show-spans/--no-show-spans", help="Print a span list"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Check recent traces in the configured backend (Tempo by default)."""
    otel_enabled = os.getenv("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
    if not otel_enabled:
        emit(
            "OTEL_ENABLED is not true; traces may be missing.",
            data={"hint": "export $(cat .env.example | xargs)"},
            json_output=json_output,
        )
    _span_check_impl(backend, tempo_url, service_name, limit, trace_id, show_spans, json_output)





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
        g2 db-init

        # Initialize with custom database URL
        g2 db-init --db-url postgresql://user:pass@localhost:5432/mydb

        # Check results in JSON format
        g2 db-init --json
    """
    with create_span("cli.db-init"):
        _db_init_impl(db_url, json_output)


def _db_init_impl(db_url, json_output):
    """Implementation of db-init (separated for tracing)."""
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

    except Exception as exc:
        emit_error(f"Initialization failed: {exc}", json_output=json_output)


@app.command("db-migrate")
def db_migrate(
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    migrations_dir: Optional[Path] = typer.Option(None, help="Migrations directory (default: sql/migrations)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show pending migrations without applying"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """
    Run database migrations from sql/migrations/ directory.

    Migrations are applied in order (001, 002, 003, etc.) and tracked
    in the schema_migrations table. Already-applied migrations are
    automatically skipped. Safe to run multiple times (idempotent).

    Examples:
        # Run all pending migrations
        g2 db-migrate

        # Show pending migrations without applying
        g2 db-migrate --dry-run

        # Run migrations on specific database
        g2 db-migrate --db-url postgresql://user:pass@host:5432/db

        # Use custom migrations directory
        g2 db-migrate --migrations-dir /path/to/migrations
    """
    with create_span("cli.db-migrate", dry_run=dry_run):
        _db_migrate_impl(db_url, migrations_dir, dry_run, json_output)


def _db_migrate_impl(db_url, migrations_dir, dry_run, json_output):
    """Implementation of db-migrate (separated for tracing)."""
    from g2.db.migrate import run_migrations
    from pathlib import Path as PathLib
    import g2

    url = _db_url(db_url)

    # Find migrations directory
    if migrations_dir is None:
        package_dir = PathLib(g2.__file__).parent.parent.parent
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


@app.command("feat-fx-export")
def features_fx_export(
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
    fx_filter = parse_comma_separated(functions)

    try:
        with db_connection(db_url) as conn:
            init_schema_tables(conn, ["feature_functions"])
            exported_count = export_functions_to_directory(conn, target_dir, fx_filter)

        emit(f"Exported {exported_count} function(s) to {target_dir}")
    except Exception as exc:
        emit_error(f"Export failed: {exc}")


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
) -> None:
    """
    Import feature_functions from individual JSON files.

    By default, imports all JSON files from the 'feature-functions/' directory.
    Idempotent: re-running will upsert by (name, version).
    """
    src_dir = Path(dir) if dir else Path("feature-functions")
    fx_filter = parse_comma_separated(functions)

    try:
        with db_connection(db_url) as conn:
            init_schema_tables(conn, ["feature_functions"])
            imported_count = import_functions_from_directory(conn, src_dir, fx_filter)

        if imported_count == 0:
            emit(f"No functions found in {src_dir}")
        else:
            emit(f"Imported {imported_count} function(s) from {src_dir}")
    except Exception as exc:
        emit_error(f"Import failed: {exc}")


@app.command("feat-def-export")
def feat_def_export(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory to write feature definition files (default: feature-definitions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    features: Optional[str] = typer.Option(None, "--features", help="Comma-separated list of feature names to export"),
) -> None:
    """
    Export feature_definitions to individual JSON files (one per feature).

    By default, exports all definitions to the 'feature-definitions/' directory.
    Each definition is saved as <name>.json.
    """
    target_dir = Path(dir) if dir else Path("feature-definitions")
    feat_filter = parse_comma_separated(features)

    try:
        with db_connection(db_url) as conn:
            init_schema_tables(conn, ["feature_definitions"])
            exported_count = export_definitions_to_directory(conn, target_dir, feat_filter)

        emit(f"Exported {exported_count} definition(s) to {target_dir}")
    except Exception as exc:
        emit_error(f"Export failed: {exc}")


@app.command("feat-def-import")
def feat_def_import(
    dir: Optional[Path] = typer.Option(None, "--dir", help="Directory containing feature definition JSON files (default: feature-definitions)"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    features: Optional[str] = typer.Option(None, "--features", help="Comma-separated list of feature names to import"),
) -> None:
    """
    Import feature_definitions from individual JSON files.

    By default, imports all JSON files from the 'feature-definitions/' directory.
    Idempotent: re-running will upsert by name.
    """
    src_dir = Path(dir) if dir else Path("feature-definitions")
    feat_filter = parse_comma_separated(features)

    try:
        with db_connection(db_url) as conn:
            init_schema_tables(conn, ["feature_definitions", "computed_features"])
            imported_count = import_definitions_from_directory(conn, src_dir, feat_filter)

        if imported_count == 0:
            emit(f"No definitions found in {src_dir}")
        else:
            emit(f"Imported {imported_count} definition(s) from {src_dir}")
    except Exception as exc:
        emit_error(f"Import failed: {exc}")


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
        g2 features-drop --feature indicator_rsi_14,indicator_macd

        # Drop all feature data but keep definitions
        g2 features-drop --all --data-only

        # Drop all features completely (DANGEROUS!)
        g2 features-drop --all
    """
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


@app.command("feat-def-show")
def features_show(
    feature: str = typer.Option(..., "--feature", help="Feature name"),
    db_url: Optional[str] = typer.Option(None, help="Database URL"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output result as JSON"),
) -> None:
    """Show a single feature definition."""
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
        g2 features-compute --symbols AAPL --function-names indicator

        # Compute specific features for multiple stocks
        g2 features-compute --symbols AAPL,MSFT --features indicator_rsi_14,derivative_rsi_14_slope_5

        # Full refresh of all features for all stocks
        g2 features-compute --all-features --full
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
    from g2.features.dispatcher import compute_features

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
                    "  → Run: g2 feat-def-list\n"
                    "  → Or import definitions: g2 feat-def-import --dir feature-definitions",
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
                    "  → Ingest a single stock: g2 prices-ingest --symbol AAPL\n"
                    "  → Or ingest a universe: g2 universe-ingest --exchange NASDAQ --limit 10\n"
                    "  → Or run full workflow: g2 data-update --exchange NASDAQ --limit 10",
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
                        from g2.observability import is_enabled
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
        from g2.observability import flush_telemetry
        flush_telemetry()

        # Only close pool if we initialized it (don't close pools managed by caller)
        if pool_needed:
            db_pool.close_pool()


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
    Use 'g2 feat-def-import' to import feature definitions from JSON files.

    Examples:
        # First time setup: import feature definitions
        g2 feat-def-import --dir feature-definitions

        # Update data for existing stocks in database (inferred from stocks table)
        g2 data-update

        # Update NASDAQ stocks (limited to 20 for testing)
        g2 data-update --exchange NASDAQ --limit 20

        # Full refresh of all features
        g2 data-update --exchange NYSE --refresh

        # Incremental update for all stocks
        g2 data-update
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
    from g2.db.migrate import check_pending_migrations
    from pathlib import Path as PathLib
    import g2
    try:
        package_dir = PathLib(g2.__file__).parent.parent.parent
        migrations_dir = package_dir / "sql" / "migrations"

        if migrations_dir.exists():
            with db_connection(url) as conn:
                pending = check_pending_migrations(conn, migrations_dir)
                if pending:
                    warning_msg = f"⚠️  Warning: {len(pending)} pending migration(s) detected. Database schema may be out of sync."
                    if not json_output:
                        emit(warning_msg)
                        for m in pending:
                            emit(f"  - {m['version']}_{m['name']}")
                        emit("  Run 'g2 db-migrate' to apply migrations before proceeding.")
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
    from g2.ingest.universe import _expected_market_date, filter_symbols_needing_update
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
                init_schema_tables(conn, ["stocks", "stock_ohlcv"])
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
    # Feature definitions must already exist (imported via g2 feat-def-import)
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
                                from g2.db.ingest import upsert_stock
                                data_id = upsert_stock(conn, symbol)
                                set_attributes(symbol_span, data_id=data_id)

                                # Compute ALL active features (indicators, derivatives, etc.)
                                # TODO: Add dependency ordering via feature_definitions.depends_on field
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
        help="Strategy name: 'momentum', 'mean_reversion', 'ma_crossover', 'breakout', 'pairs_trading', 'rsi_divergence', or 'volatility_contraction'"
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
        g2 backtest run --symbols AAPL,MSFT,GOOGL,NVDA,TSLA \\
          --start-date 2024-01-01 --end-date 2024-12-01 \\
          --initial-cash 100000 --strategy momentum --top-n 3

        # Backtest mean reversion strategy on NASDAQ
        g2 backtest run --exchange NASDAQ --limit 50 \\
          --start-date 2024-01-01 --end-date 2024-12-01 \\
          --strategy mean_reversion --rsi-oversold 25 --rsi-overbought 75

        # Backtest moving average crossover strategy
        g2 backtest run --symbols AAPL,MSFT,GOOGL \\
          --start-date 2024-01-01 --end-date 2024-12-01 \\
          --strategy ma_crossover --fast-period 50 --slow-period 200
    """
    from datetime import datetime
    from g2.backtest.data_loader import load_price_data_for_backtest
    from g2.backtest.engine import BacktestEngine
    from g2.strategies.momentum import MomentumStrategy
    from g2.strategies.mean_reversion import MeanReversionStrategy
    from g2.strategies.ma_crossover import MovingAverageCrossoverStrategy
    from g2.strategies.breakout import BreakoutStrategy
    from g2.strategies.pairs_trading import PairsTradingStrategy
    from g2.strategies.rsi_divergence import RSIDivergenceStrategy
    from g2.strategies.volatility_contraction import VolatilityContractionStrategy

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
                "Try: g2 data-update --exchange NASDAQ --limit 50",
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
    else:
        emit_error(
            f"Unknown strategy: {strategy}. Supported strategies: 'momentum', 'mean_reversion', 'ma_crossover', 'breakout', 'pairs_trading', 'rsi_divergence', 'volatility_contraction'",
            json_output=json_output
        )
        raise typer.Exit(1)

    # Run backtest
    emit(f"Running {strategy} strategy backtest...", json_output=json_output)

    try:
        # Create wrapper function for strategy that matches BacktestEngine interface
        def strategy_fn(current_date, portfolio, prices):
            return strat.generate_signals(
                current_date=current_date,
                portfolio=portfolio,
                price_data=prices,
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

        # Extract metrics (already calculated by engine)
        metrics = results["metrics"]
        final_equity = results["equity_curve"][-1]["equity"] if results["equity_curve"] else initial_cash
        trade_count = len(results["trades"])

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
                    "final_value": final_equity,
                    "total_return": metrics["total_return"],
                    "sharpe_ratio": metrics["sharpe_ratio"],
                    "max_drawdown": metrics["max_drawdown"],
                },
                "trades": trade_count,
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


@app.command("mcp-setup")
def mcp_setup(
    db_url: Optional[str] = typer.Option(None, help="Database URL (default: from environment or postgresql://g2:g2pass@localhost:5432/g2)"),
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
        g2 mcp-setup                    # Configure all targets
        g2 mcp-setup --targets cli      # Configure only CLI
        g2 mcp-setup --targets desktop  # Configure only desktop
        g2 mcp-setup --force            # Overwrite existing config
    """
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

        # Get absolute path to g2 project root
        cli_file = Path(__file__).resolve()
        g2_root = cli_file.parent.parent.parent
        server_path = g2_root / "mcp-server" / "server.py"

        if not server_path.exists():
            emit_error(
                f"MCP server not found at {server_path}. "
                "Are you running this from the g2 project directory?",
                json_output=json_output
            )
            raise typer.Exit(1)

        # Get database URL
        if not db_url:
            db_url = os.environ.get('DATABASE_URL', 'postgresql://g2:g2pass@localhost:6432/g2')

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

                # Check if g2 server already configured
                if "mcpServers" in existing_config and "g2" in existing_config.get("mcpServers", {}):
                    existing_g2_config = existing_config["mcpServers"]["g2"]

                    # Compare configurations (ignoring key order)
                    if (existing_g2_config.get("command") == expected_config["command"] and
                        existing_g2_config.get("args") == expected_config["args"] and
                        existing_g2_config.get("env", {}) == expected_config["env"]):
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
                existing_config["mcpServers"]["g2"] = expected_config

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
                console.print("\nThe 'g2' MCP server should now be available")
            else:
                console.print("\n[dim]All configurations are already correct. No changes needed.[/dim]")
            console.print("\n[dim]To update configs, run: g2 mcp-setup --force[/dim]")

    except typer.Exit:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        emit_error(f"Setup failed: {exc}", json_output=json_output)
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
