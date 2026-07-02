"""Apply an experiment winner to production.

Takes a promoted (or standalone completed) experiment through the full
pipeline: dataset rebuild -> retrain -> predict -> backtest, recording
the produced artifacts on the experiment and opening its probation
window. Each stage reports progress via the same on_progress callback
contract as CycleRunner.

Stages shell out to the gefion CLI with --json (the same pattern
CycleRunner._rebuild_dataset uses) so this module reuses the pipeline's
stable contracts instead of duplicating its logic.
"""

import json
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

# Experiment types whose winners retrain the prediction model.
SUPPORTED_TYPES = {"feature_engineering", "hyperparameter"}

# Days of history to predict over and backtest against.
DEFAULT_BACKTEST_DAYS = 90

# Probation window opened when an applied experiment reaches production.
PROBATION_DAYS = 7

_STAGE_TIMEOUTS = {
    "dataset": 600,
    "train": 3600,
    "predict": 3600,
    "backtest": 1800,
}

# best_params keys that `gefion ml train` accepts as flags
_TRAIN_PARAM_FLAGS = {
    "learning_rate": "--learning-rate",
    "n_estimators": "--n-estimators",
    "max_depth": "--max-depth",
    "min_child_weight": "--min-child-weight",
    "subsample": "--subsample",
    "colsample_bytree": "--colsample-bytree",
    "reg_alpha": "--reg-alpha",
    "reg_lambda": "--reg-lambda",
}


class ApplyError(Exception):
    """A stage of the apply flow failed or the experiment is not eligible."""


@contextmanager
def _db_conn(db_url: Optional[str] = None):
    """Connection from the shared pool, or direct when a URL is given."""
    from gefion.db import pool as db_pool

    if db_url:
        conn = db_pool.get_connection_direct(db_url)
        try:
            yield conn
        finally:
            conn.close()
        return

    p = db_pool.get_pool()
    if p is None:
        db_pool.init_pool(os.environ["DATABASE_URL"])
        p = db_pool.get_pool()
    with p.connection() as conn:
        yield conn


def _load_experiment(experiment_id: int, db_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load the fields the apply flow needs. Returns None if not found."""
    with _db_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, experiment_type, status, cycle_id, fdr_survived,
                       promoted_at, config, results, baseline_value, objective_metric
                FROM experiments WHERE id = %s
                """,
                (experiment_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "experiment_type": row[2],
        "status": row[3],
        "cycle_id": row[4],
        "fdr_survived": row[5],
        "promoted_at": row[6],
        "config": row[7] or {},
        "results": row[8] or {},
        "baseline_value": float(row[9]) if row[9] is not None else None,
        "objective_metric": row[10],
    }


def _load_manifest(dataset_uri: str) -> Dict[str, Any]:
    """Read a dataset manifest, raising ApplyError with a clear message."""
    path = Path(dataset_uri)
    if not path.exists():
        raise ApplyError(f"Dataset manifest not found: {dataset_uri}")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ApplyError(f"Cannot read dataset manifest {dataset_uri}: {e}")


def _run_cli(cmd: List[str], timeout: int = 600) -> Dict[str, Any]:
    """Run a gefion CLI command with --json and return the parsed output."""
    full_cmd = [sys.executable, "-m", "gefion.cli", *cmd]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ApplyError(f"Stage timed out after {timeout}s: {' '.join(cmd[:3])}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise ApplyError(f"Stage failed ({' '.join(cmd[:3])}): {detail}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # Some commands succeed without JSON output; treat as ok
        return {"status": "ok", "output": result.stdout[-500:]}


def _record_artifacts(experiment_id: int, artifacts: Dict[str, Any],
                      db_url: Optional[str] = None) -> None:
    """Store apply artifacts on the experiment and open its probation window."""
    from psycopg.types.json import Json

    with _db_conn(db_url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE experiments
                SET results = jsonb_set(COALESCE(results, '{}'::jsonb), '{applied}', %s::jsonb),
                    probation_until = NOW() + make_interval(days => %s::int)
                WHERE id = %s
                """,
                (Json(artifacts), PROBATION_DAYS, experiment_id),
            )


def _dataset_name_version(manifest: Dict[str, Any], dataset_uri: str) -> tuple:
    """Derive (name, version) from a manifest and its URI path."""
    name = manifest.get("name")
    if not name:
        raise ApplyError(f"Dataset manifest has no name: {dataset_uri}")
    version = manifest.get("version")
    if not version:
        # URI convention: datasets/{name}_{version}/manifest.json
        dirname = Path(dataset_uri).parent.name
        version = dirname[len(name) + 1:] if dirname.startswith(f"{name}_") else dirname
    if not version:
        raise ApplyError(f"Cannot derive dataset version from {dataset_uri}")
    return name, version


def _universe_flags(manifest: Dict[str, Any]) -> List[str]:
    """Universe selection flags shared by predict and backtest."""
    universe = manifest.get("universe") or {}
    if universe.get("exchange"):
        flags = ["--exchange", str(universe["exchange"])]
        if universe.get("limit"):
            flags.extend(["--limit", str(universe["limit"])])
        return flags
    if universe.get("symbols"):
        return ["--symbols", ",".join(universe["symbols"])]
    raise ApplyError("Dataset manifest defines no universe (exchange or symbols)")


def apply_experiment(
    experiment_id: int,
    on_progress: Optional[Callable] = None,
    db_url: Optional[str] = None,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
) -> Dict[str, Any]:
    """Take an experiment winner through retrain -> predict -> backtest.

    Args:
        experiment_id: The winning experiment to apply.
        on_progress: Optional callback(phase, message, detail) — the same
            contract as CycleRunner.run_cycle's on_progress.
        db_url: Database URL override (defaults to configured pool).
        backtest_days: History window to predict over and backtest against.

    Returns:
        Dict with model_name, model_version, dataset, backtest results.

    Raises:
        ApplyError: If the experiment is not eligible or any stage fails.
    """
    def _emit(phase: str, message: str, detail: Optional[Dict] = None):
        logger.info(f"[apply {phase}] {message}")
        if on_progress:
            on_progress(phase, message, detail)

    with create_span("experiments.apply", experiment_id=experiment_id) as span:
        # --- validate -----------------------------------------------------
        _emit("validate", f"Loading experiment #{experiment_id}...")
        exp = _load_experiment(experiment_id, db_url=db_url)
        if exp is None:
            raise ApplyError(f"Experiment {experiment_id} not found")
        if exp["status"] != "completed":
            raise ApplyError(
                f"Experiment {experiment_id} must be completed (status: {exp['status']})"
            )
        if exp["experiment_type"] not in SUPPORTED_TYPES:
            raise ApplyError(
                f"Unsupported experiment type '{exp['experiment_type']}': "
                f"apply supports {sorted(SUPPORTED_TYPES)}"
            )
        if exp["cycle_id"] is not None and not exp["fdr_survived"] and not exp["promoted_at"]:
            raise ApplyError(
                f"Experiment {experiment_id} did not survive FDR correction — "
                "only promoted winners can be applied"
            )

        config = exp["config"]
        algorithm = config.get("algorithm", "quantile_regression")
        horizon = config.get("horizon_days", 7)
        dataset_uri = config.get("dataset_uri")
        if not dataset_uri:
            raise ApplyError(
                f"Experiment {experiment_id} has no dataset_uri in its config; "
                "cannot rebuild the pipeline it was tested on"
            )
        manifest = _load_manifest(dataset_uri)
        dataset_name, dataset_version = _dataset_name_version(manifest, dataset_uri)
        universe = _universe_flags(manifest)
        set_attributes(span, experiment_type=exp["experiment_type"],
                       algorithm=algorithm, horizon_days=horizon)

        # --- dataset ------------------------------------------------------
        if exp["experiment_type"] == "feature_engineering":
            # The promoted feature is active now; rebuild so it's included
            dataset_version = f"applied-exp-{experiment_id}"
            _emit("dataset",
                  f"Rebuilding dataset {dataset_name}:{dataset_version} with promoted feature...")
            with create_span("experiments.apply.dataset"):
                cmd = ["ml", "dataset-build",
                       "--name", dataset_name,
                       "--version", dataset_version,
                       "--horizons", str(horizon),
                       "--format", manifest.get("format", "parquet")]
                exchange = (manifest.get("universe") or {}).get("exchange")
                if exchange:
                    cmd.extend(["--exchange", str(exchange)])
                cmd.append("--json")
                _run_cli(cmd, timeout=_STAGE_TIMEOUTS["dataset"])
        else:
            _emit("dataset",
                  f"Reusing experiment dataset {dataset_name}:{dataset_version} "
                  "(no new features to include)")

        # --- train --------------------------------------------------------
        model_name = f"exp{experiment_id}_{algorithm}"
        model_version = f"applied-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        best_params = (exp["results"] or {}).get("best_params") or {}
        _emit("train",
              f"Training {model_name}:{model_version} ({algorithm}, "
              f"{len(best_params)} tuned params)...",
              {"model_name": model_name, "best_params": best_params})
        with create_span("experiments.apply.train", model_name=model_name):
            cmd = ["ml", "train",
                   "--dataset-name", dataset_name,
                   "--dataset-version", dataset_version,
                   "--model-name", model_name,
                   "--model-version", model_version,
                   "--algorithm", algorithm]
            for key, flag in _TRAIN_PARAM_FLAGS.items():
                if key in best_params:
                    cmd.extend([flag, str(best_params[key])])
            cmd.append("--json")
            train_result = _run_cli(cmd, timeout=_STAGE_TIMEOUTS["train"])

        # --- predict --------------------------------------------------------
        end = date.today()
        start = end - timedelta(days=backtest_days)
        _emit("predict",
              f"Generating predictions {start} → {end} for the backtest window...")
        with create_span("experiments.apply.predict", model_name=model_name):
            cmd = ["ml", "predict",
                   "--model-name", model_name,
                   "--model-version", model_version,
                   "--start-date", str(start),
                   "--end-date", str(end),
                   *universe,
                   "--json"]
            predict_result = _run_cli(cmd, timeout=_STAGE_TIMEOUTS["predict"])

        # --- backtest -------------------------------------------------------
        _emit("backtest",
              f"Backtesting ml_signal with {model_name} over {backtest_days} days...")
        with create_span("experiments.apply.backtest", model_name=model_name):
            cmd = ["backtest", "run",
                   "--strategy", "ml_signal",
                   "--model-name", model_name,
                   "--model-version", model_version,
                   "--horizon-days", str(horizon),
                   "--start-date", str(start),
                   "--end-date", str(end),
                   *universe,
                   "--json"]
            backtest_result = _run_cli(cmd, timeout=_STAGE_TIMEOUTS["backtest"])

        # --- record ---------------------------------------------------------
        artifacts = {
            "model_name": model_name,
            "model_version": model_version,
            "dataset_name": dataset_name,
            "dataset_version": dataset_version,
            "predictions": {"start": str(start), "end": str(end)},
            "backtest": backtest_result,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        _record_artifacts(experiment_id, artifacts, db_url=db_url)

        result = {
            "status": "ok",
            "experiment_id": experiment_id,
            "model_name": model_name,
            "model_version": model_version,
            "dataset_name": dataset_name,
            "dataset_version": dataset_version,
            "train": train_result,
            "predictions": predict_result,
            "backtest": backtest_result,
            "baseline_value": exp["baseline_value"],
            "objective_metric": exp["objective_metric"],
            "probation_days": PROBATION_DAYS,
        }
        _emit("complete",
              f"Applied experiment #{experiment_id}: model {model_name}:{model_version} "
              f"trained, predictions generated, backtest complete. "
              f"Probation window: {PROBATION_DAYS} days.",
              {"model_name": model_name})
        set_attributes(span, status="ok", model_name=model_name)
        return result
