"""End-to-end ML pipeline testing.

This module provides functionality to run and validate the full ML pipeline,
from data ingestion through ensemble predictions.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gefion.observability import create_span


# Define pipeline steps
E2E_STEPS = {
    "data_update": "Update price data from AlphaVantage",
    "dataset_build": "Build ML dataset with features and labels",
    "train_model": "Train single XGBoost model",
    "train_ensemble": "Train ensemble combining XGBoost and LightGBM",
    "predict": "Generate predictions with single model",
    "predict_ensemble": "Generate predictions with ensemble",
    "quality_check": "Validate prediction quality metrics",
}


@dataclass
class E2ETestResult:
    """Result of an end-to-end ML pipeline test."""

    success: bool
    steps_completed: List[str]
    steps_failed: List[str]
    duration_seconds: float
    artifacts: Dict[str, Any] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "success": self.success,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "duration_seconds": self.duration_seconds,
            "artifacts": self.artifacts,
            "errors": self.errors,
        }


def run_e2e_test(
    exchange: str = "NASDAQ",
    limit: int = 10,
    name: str = "e2e_test",
    skip_data_update: bool = False,
    cleanup: bool = False,
    conn=None,
    progress_callback: Optional[callable] = None,
) -> E2ETestResult:
    """Run end-to-end ML pipeline test.

    Args:
        exchange: Exchange to fetch data from (default: NASDAQ)
        limit: Number of symbols to use (default: 10)
        name: Test name prefix for artifacts (default: e2e_test)
        skip_data_update: Skip data update step if True
        cleanup: Remove test artifacts after completion if True
        conn: Database connection (optional, creates one if not provided)
        progress_callback: Optional callback(step_name, status, message) for progress updates
                          status is one of: "starting", "completed", "failed", "skipped"

    Returns:
        E2ETestResult with success status, completed steps, and artifacts
    """
    def _progress(step: str, status: str, message: str = "") -> None:
        if progress_callback:
            progress_callback(step, status, message)
    with create_span("run_e2e_test") as span:
        span.set_attribute("exchange", exchange)
        span.set_attribute("limit", limit)
        span.set_attribute("name", name)

        start_time = time.time()
        steps_completed = []
        steps_failed = []
        artifacts = {}
        errors = {}

        if conn is None:
            raise ValueError("Database connection required. Pass conn parameter.")

        # Step 1: Data Update
        symbols = []
        if not skip_data_update:
            try:
                _progress("data_update", "starting", "Updating price data...")
                with create_span("e2e_data_update"):
                    symbols = _run_data_update(exchange, limit, conn)
                    artifacts["symbols"] = symbols
                steps_completed.append("data_update")
                _progress("data_update", "completed", f"Updated {len(symbols)} symbols")
            except Exception as e:
                steps_failed.append("data_update")
                errors["data_update"] = str(e)
                _progress("data_update", "failed", str(e))
                return _build_result(
                    False, steps_completed, steps_failed,
                    time.time() - start_time, artifacts, errors
                )
        else:
            # When skipping data_update, get symbols with most recent COMPUTED FEATURES
            # (not just price data - we need features for prediction)
            import os
            from gefion.cli_helpers import db_connection
            db_url = os.getenv("DATABASE_URL")
            with db_connection(db_url) as fresh_conn:
                with fresh_conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT s.symbol
                        FROM stocks s
                        JOIN computed_features cf ON s.id = cf.data_id
                        GROUP BY s.symbol
                        ORDER BY MAX(cf.date) DESC
                        LIMIT %s;
                        """,
                        (limit,),
                    )
                    symbols = [row[0] for row in cur.fetchall()]
            if not symbols:
                raise RuntimeError("No symbols with computed features found. Run without --skip-data-update first.")
            artifacts["symbols"] = symbols
            steps_completed.append("data_update")
            _progress("data_update", "skipped", f"Using {len(symbols)} symbols with features")

        # Step 2: Dataset Build
        try:
            _progress("dataset_build", "starting", "Building ML dataset...")
            with create_span("e2e_dataset_build"):
                dataset_info = _run_dataset_build(symbols, name, conn)
                artifacts["dataset_name"] = dataset_info["name"]
                artifacts["dataset_version"] = dataset_info["version"]
            steps_completed.append("dataset_build")
            _progress("dataset_build", "completed", f"Dataset: {dataset_info['name']} {dataset_info['version']}")
        except Exception as e:
            steps_failed.append("dataset_build")
            errors["dataset_build"] = str(e)
            _progress("dataset_build", "failed", str(e))
            return _build_result(
                False, steps_completed, steps_failed,
                time.time() - start_time, artifacts, errors
            )

        # Step 3: Train Single Model
        try:
            _progress("train_model", "starting", "Training XGBoost model...")
            with create_span("e2e_train_model"):
                model_info = _run_train_model(
                    artifacts["dataset_name"],
                    artifacts["dataset_version"],
                    name,
                    conn,
                )
                artifacts["model_name"] = model_info["name"]
                artifacts["model_version"] = model_info["version"]
            steps_completed.append("train_model")
            _progress("train_model", "completed", f"Model: {model_info['name']}")
        except Exception as e:
            steps_failed.append("train_model")
            errors["train_model"] = str(e)
            _progress("train_model", "failed", str(e))
            return _build_result(
                False, steps_completed, steps_failed,
                time.time() - start_time, artifacts, errors
            )

        # Step 4: Train Ensemble
        try:
            _progress("train_ensemble", "starting", "Training ensemble (XGBoost + LightGBM)...")
            with create_span("e2e_train_ensemble"):
                ensemble_info = _run_train_ensemble(
                    artifacts["dataset_name"],
                    artifacts["dataset_version"],
                    name,
                    conn,
                )
                artifacts["ensemble_name"] = ensemble_info["name"]
                artifacts["ensemble_version"] = ensemble_info["version"]
            steps_completed.append("train_ensemble")
            _progress("train_ensemble", "completed", f"Ensemble: {ensemble_info['name']}")
        except Exception as e:
            steps_failed.append("train_ensemble")
            errors["train_ensemble"] = str(e)
            _progress("train_ensemble", "failed", str(e))
            return _build_result(
                False, steps_completed, steps_failed,
                time.time() - start_time, artifacts, errors
            )

        # Step 5: Generate Predictions (single model)
        try:
            _progress("predict", "starting", "Generating predictions (single model)...")
            with create_span("e2e_predict"):
                pred_info = _run_predict(
                    artifacts["model_name"],
                    artifacts["model_version"],
                    symbols,
                    conn,
                )
                artifacts["predictions_count"] = pred_info["count"]
            steps_completed.append("predict")
            _progress("predict", "completed")
        except Exception as e:
            steps_failed.append("predict")
            errors["predict"] = str(e)
            _progress("predict", "failed", str(e))
            return _build_result(
                False, steps_completed, steps_failed,
                time.time() - start_time, artifacts, errors
            )

        # Step 6: Generate Ensemble Predictions
        try:
            _progress("predict_ensemble", "starting", "Generating predictions (ensemble)...")
            with create_span("e2e_predict_ensemble"):
                ensemble_pred_info = _run_predict_ensemble(
                    artifacts["ensemble_name"],
                    artifacts["ensemble_version"],
                    symbols,
                    conn,
                )
                artifacts["ensemble_predictions_count"] = ensemble_pred_info["count"]
            steps_completed.append("predict_ensemble")
            _progress("predict_ensemble", "completed")
        except Exception as e:
            steps_failed.append("predict_ensemble")
            errors["predict_ensemble"] = str(e)
            _progress("predict_ensemble", "failed", str(e))
            return _build_result(
                False, steps_completed, steps_failed,
                time.time() - start_time, artifacts, errors
            )

        # Step 7: Quality Check
        try:
            _progress("quality_check", "starting", "Validating prediction quality...")
            with create_span("e2e_quality_check"):
                quality_info = _run_quality_check(
                    artifacts["model_name"],
                    artifacts["ensemble_name"],
                    conn,
                )
                artifacts["quality"] = quality_info
            steps_completed.append("quality_check")
            quality_msg = f"IQR: {quality_info['avg_iqr']:.1%}, ordering: {'OK' if quality_info['ordering_valid'] else 'FAIL'}"
            _progress("quality_check", "completed", quality_msg)
        except Exception as e:
            steps_failed.append("quality_check")
            errors["quality_check"] = str(e)
            _progress("quality_check", "failed", str(e))
            # Quality check failure is non-fatal - continue to cleanup
            pass

        # Cleanup if requested
        if cleanup:
            with create_span("e2e_cleanup"):
                _run_cleanup(artifacts, conn)

        return _build_result(
            True, steps_completed, steps_failed,
            time.time() - start_time, artifacts, errors
        )


def _build_result(
    success: bool,
    steps_completed: List[str],
    steps_failed: List[str],
    duration: float,
    artifacts: Dict[str, Any],
    errors: Dict[str, str],
) -> E2ETestResult:
    """Build E2ETestResult from components."""
    return E2ETestResult(
        success=success,
        steps_completed=steps_completed,
        steps_failed=steps_failed,
        duration_seconds=round(duration, 2),
        artifacts=artifacts,
        errors=errors,
    )


def _run_data_update(exchange: str, limit: int, conn) -> List[str]:
    """Run data update step by calling CLI command.

    Returns:
        List of symbols that were updated (for use in subsequent steps)
    """
    import subprocess
    import sys
    import os
    from gefion.cli_helpers import db_connection

    # Call CLI via subprocess (simple and reliable)
    cmd = [
        sys.executable, "-m", "gefion.cli",
        "data-update",
        "--exchange", exchange,
        "--limit", str(limit),
        "--timeframe", "compact",
        "--no-progress",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"data-update failed: {result.stderr or result.stdout}")

    # Query which symbols now have the most recent data (these are the ones we updated)
    db_url = os.getenv("DATABASE_URL")
    with db_connection(db_url) as fresh_conn:
        with fresh_conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.symbol
                FROM stocks s
                JOIN stock_ohlcv o ON s.id = o.data_id
                GROUP BY s.symbol
                ORDER BY MAX(o.date) DESC
                LIMIT %s;
                """,
                (limit,),
            )
            symbols = [row[0] for row in cur.fetchall()]
    return symbols


def _run_dataset_build(
    symbols: List[str], name: str, conn
) -> Dict[str, str]:
    """Build dataset by calling CLI command."""
    import subprocess
    import sys
    import datetime

    version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    cmd = [
        sys.executable, "-m", "gefion.cli",
        "ml", "dataset-build",
        "--name", name,
        "--version", version,
        "--symbols", ",".join(symbols),
        "--horizons", "7,30",
        "--weak-thresholds", "0.02,0.05",
        "--strong-thresholds", "0.05,0.10",
        "--export",  # Export features/labels to files for training
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"dataset-build failed: {result.stderr or result.stdout}")

    return {"name": name, "version": version, "symbols": symbols}


def _run_train_model(
    dataset_name: str, dataset_version: str, name: str, conn
) -> Dict[str, str]:
    """Train single model by calling CLI command."""
    import subprocess
    import sys
    import datetime

    model_name = f"{name}_xgboost"
    model_version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    cmd = [
        sys.executable, "-m", "gefion.cli",
        "ml", "train",
        "--dataset-name", dataset_name,
        "--dataset-version", dataset_version,
        "--model-name", model_name,
        "--model-version", model_version,
        "--algorithm", "xgboost",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ml train failed: {result.stderr or result.stdout}")

    return {"name": model_name, "version": model_version}


def _run_train_ensemble(
    dataset_name: str, dataset_version: str, name: str, conn
) -> Dict[str, str]:
    """Train ensemble model by calling CLI command."""
    import subprocess
    import sys
    import datetime

    ensemble_name = f"{name}_ensemble"
    ensemble_version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    cmd = [
        sys.executable, "-m", "gefion.cli",
        "ml", "train-ensemble",
        "--dataset-name", dataset_name,
        "--dataset-version", dataset_version,
        "--model-name", ensemble_name,
        "--model-version", ensemble_version,
        "--algorithms", "xgboost,lightgbm",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ml train-ensemble failed: {result.stderr or result.stdout}")

    return {"name": ensemble_name, "version": ensemble_version}


def _run_predict(
    model_name: str, model_version: str, symbols: List[str], conn
) -> Dict[str, int]:
    """Generate predictions by calling CLI command."""
    import subprocess
    import sys
    import re

    # Let CLI auto-detect the prediction date based on available features
    cmd = [
        sys.executable, "-m", "gefion.cli",
        "ml", "predict",
        "--model-name", model_name,
        "--model-version", model_version,
        "--symbols", ",".join(symbols),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ml predict failed: {result.stderr or result.stdout}")

    # Parse prediction count from output (e.g., "Generated 20 predictions")
    count = 0
    match = re.search(r"Generated (\d+) predictions", result.stdout)
    if match:
        count = int(match.group(1))
    return {"count": count}


def _run_predict_ensemble(
    model_name: str, model_version: str, symbols: List[str], conn
) -> Dict[str, int]:
    """Generate ensemble predictions by calling CLI command."""
    import subprocess
    import sys
    import re

    # Let CLI auto-detect the prediction date based on available features
    cmd = [
        sys.executable, "-m", "gefion.cli",
        "ml", "predict-ensemble",
        "--model-name", model_name,
        "--model-version", model_version,
        "--symbols", ",".join(symbols),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ml predict-ensemble failed: {result.stderr or result.stdout}")

    # Parse prediction count from output (e.g., "Generated 20 predictions")
    count = 0
    match = re.search(r"Generated (\d+) predictions", result.stdout)
    if match:
        count = int(match.group(1))
    return {"count": count}


def _run_quality_check(
    model_name: str, ensemble_name: str, conn
) -> Dict[str, Any]:
    """Check prediction quality metrics.

    Returns:
        Dict with quality metrics:
        - avg_iqr: Average interquartile range (q90 - q10) as fraction
        - ordering_valid: True if q10 <= q50 <= q90 for all predictions
        - prediction_count: Number of predictions checked
        - ensemble_avg_iqr: Ensemble average IQR
        - ensemble_ordering_valid: Ensemble ordering check
    """
    import os
    from gefion.cli_helpers import db_connection

    db_url = os.getenv("DATABASE_URL")
    with db_connection(db_url) as fresh_conn:
        with fresh_conn.cursor() as cur:
            # Check single model predictions
            cur.execute(
                """
                SELECT
                    COUNT(*) as cnt,
                    AVG(q90 - q10) as avg_iqr,
                    SUM(CASE WHEN q10 <= q50 AND q50 <= q90 THEN 1 ELSE 0 END) as valid_ordering
                FROM quantile_predictions qp
                JOIN ml_models m ON qp.model_id = m.id
                WHERE m.name = %s;
                """,
                (model_name,),
            )
            row = cur.fetchone()
            model_count = int(row[0]) if row[0] else 0
            model_avg_iqr = float(row[1]) if row[1] else 0.0
            model_valid = int(row[2]) if row[2] else 0

            # Check ensemble predictions
            cur.execute(
                """
                SELECT
                    COUNT(*) as cnt,
                    AVG(q90 - q10) as avg_iqr,
                    SUM(CASE WHEN q10 <= q50 AND q50 <= q90 THEN 1 ELSE 0 END) as valid_ordering
                FROM quantile_predictions qp
                JOIN ml_models m ON qp.model_id = m.id
                WHERE m.name = %s;
                """,
                (ensemble_name,),
            )
            row = cur.fetchone()
            ensemble_count = int(row[0]) if row[0] else 0
            ensemble_avg_iqr = float(row[1]) if row[1] else 0.0
            ensemble_valid = int(row[2]) if row[2] else 0

    return {
        "avg_iqr": model_avg_iqr,
        "ordering_valid": model_valid == model_count and model_count > 0,
        "prediction_count": model_count,
        "ensemble_avg_iqr": ensemble_avg_iqr,
        "ensemble_ordering_valid": ensemble_valid == ensemble_count and ensemble_count > 0,
        "ensemble_prediction_count": ensemble_count,
    }


def _run_cleanup(artifacts: Dict[str, Any], conn) -> None:
    """Clean up test artifacts from database."""
    with conn.cursor() as cur:
        # Delete predictions
        if "model_name" in artifacts:
            cur.execute(
                """
                DELETE FROM quantile_predictions
                WHERE model_id IN (
                    SELECT id FROM ml_models WHERE name = %s
                );
                """,
                (artifacts["model_name"],),
            )

        if "ensemble_name" in artifacts:
            cur.execute(
                """
                DELETE FROM quantile_predictions
                WHERE model_id IN (
                    SELECT id FROM ml_models WHERE name = %s
                );
                """,
                (artifacts["ensemble_name"],),
            )

        # Delete models
        for model_key in ["model_name", "ensemble_name"]:
            if model_key in artifacts:
                cur.execute(
                    "DELETE FROM ml_models WHERE name = %s;",
                    (artifacts[model_key],),
                )

        # Delete dataset
        if "dataset_name" in artifacts:
            cur.execute(
                "DELETE FROM ml_datasets WHERE name = %s;",
                (artifacts["dataset_name"],),
            )

    conn.commit()
