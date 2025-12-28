"""End-to-end ML pipeline testing.

This module provides functionality to run and validate the full ML pipeline,
from data ingestion through ensemble predictions.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from g2.observability import create_span


# Define pipeline steps
E2E_STEPS = {
    "data_update": "Update price data from AlphaVantage",
    "dataset_build": "Build ML dataset with features and labels",
    "train_model": "Train single XGBoost model",
    "train_ensemble": "Train ensemble combining XGBoost and LightGBM",
    "predict": "Generate predictions with single model",
    "predict_ensemble": "Generate predictions with ensemble",
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
) -> E2ETestResult:
    """Run end-to-end ML pipeline test.

    Args:
        exchange: Exchange to fetch data from (default: NASDAQ)
        limit: Number of symbols to use (default: 10)
        name: Test name prefix for artifacts (default: e2e_test)
        skip_data_update: Skip data update step if True
        cleanup: Remove test artifacts after completion if True
        conn: Database connection (optional, creates one if not provided)

    Returns:
        E2ETestResult with success status, completed steps, and artifacts
    """
    with create_span("run_e2e_test") as span:
        span.set_attribute("exchange", exchange)
        span.set_attribute("limit", limit)
        span.set_attribute("name", name)

        start_time = time.time()
        steps_completed = []
        steps_failed = []
        artifacts = {}
        errors = {}

        # Import here to avoid circular imports
        from g2.db import get_connection

        should_close_conn = False
        if conn is None:
            conn = get_connection()
            should_close_conn = True

        try:
            # Step 1: Data Update
            if not skip_data_update:
                try:
                    with create_span("e2e_data_update"):
                        _run_data_update(exchange, limit, conn)
                    steps_completed.append("data_update")
                except Exception as e:
                    steps_failed.append("data_update")
                    errors["data_update"] = str(e)
                    return _build_result(
                        False, steps_completed, steps_failed,
                        time.time() - start_time, artifacts, errors
                    )
            else:
                steps_completed.append("data_update")  # Mark as skipped/complete

            # Step 2: Dataset Build
            try:
                with create_span("e2e_dataset_build"):
                    dataset_info = _run_dataset_build(exchange, limit, name, conn)
                    artifacts["dataset_name"] = dataset_info["name"]
                    artifacts["dataset_version"] = dataset_info["version"]
                steps_completed.append("dataset_build")
            except Exception as e:
                steps_failed.append("dataset_build")
                errors["dataset_build"] = str(e)
                return _build_result(
                    False, steps_completed, steps_failed,
                    time.time() - start_time, artifacts, errors
                )

            # Step 3: Train Single Model
            try:
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
            except Exception as e:
                steps_failed.append("train_model")
                errors["train_model"] = str(e)
                return _build_result(
                    False, steps_completed, steps_failed,
                    time.time() - start_time, artifacts, errors
                )

            # Step 4: Train Ensemble
            try:
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
            except Exception as e:
                steps_failed.append("train_ensemble")
                errors["train_ensemble"] = str(e)
                return _build_result(
                    False, steps_completed, steps_failed,
                    time.time() - start_time, artifacts, errors
                )

            # Step 5: Generate Predictions (single model)
            try:
                with create_span("e2e_predict"):
                    pred_info = _run_predict(
                        artifacts["model_name"],
                        artifacts["model_version"],
                        exchange,
                        limit,
                        conn,
                    )
                    artifacts["predictions_count"] = pred_info["count"]
                steps_completed.append("predict")
            except Exception as e:
                steps_failed.append("predict")
                errors["predict"] = str(e)
                return _build_result(
                    False, steps_completed, steps_failed,
                    time.time() - start_time, artifacts, errors
                )

            # Step 6: Generate Ensemble Predictions
            try:
                with create_span("e2e_predict_ensemble"):
                    ensemble_pred_info = _run_predict_ensemble(
                        artifacts["ensemble_name"],
                        artifacts["ensemble_version"],
                        exchange,
                        limit,
                        conn,
                    )
                    artifacts["ensemble_predictions_count"] = ensemble_pred_info["count"]
                steps_completed.append("predict_ensemble")
            except Exception as e:
                steps_failed.append("predict_ensemble")
                errors["predict_ensemble"] = str(e)
                return _build_result(
                    False, steps_completed, steps_failed,
                    time.time() - start_time, artifacts, errors
                )

            # Cleanup if requested
            if cleanup:
                with create_span("e2e_cleanup"):
                    _run_cleanup(artifacts, conn)

            return _build_result(
                True, steps_completed, steps_failed,
                time.time() - start_time, artifacts, errors
            )

        finally:
            if should_close_conn:
                conn.close()


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


def _run_data_update(exchange: str, limit: int, conn) -> None:
    """Run data update step."""
    from g2.data import update_exchange_data

    update_exchange_data(
        exchange=exchange,
        limit=limit,
        timeframe="compact",  # Use compact for faster testing
        conn=conn,
    )


def _run_dataset_build(
    exchange: str, limit: int, name: str, conn
) -> Dict[str, str]:
    """Build dataset and return info."""
    from g2.ml.dataset import build_dataset
    import datetime

    version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    build_dataset(
        name=name,
        version=version,
        exchange=exchange,
        limit=limit,
        horizons=[7, 30],
        export=False,  # Don't export files for e2e test
        conn=conn,
    )

    return {"name": name, "version": version}


def _run_train_model(
    dataset_name: str, dataset_version: str, name: str, conn
) -> Dict[str, str]:
    """Train single model and return info."""
    from g2.ml.training import train_model
    import datetime

    model_name = f"{name}_xgboost"
    model_version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    train_model(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        model_name=model_name,
        model_version=model_version,
        algorithm="xgboost",
        conn=conn,
    )

    return {"name": model_name, "version": model_version}


def _run_train_ensemble(
    dataset_name: str, dataset_version: str, name: str, conn
) -> Dict[str, str]:
    """Train ensemble model and return info."""
    from g2.ml.training import train_ensemble_model
    import datetime

    ensemble_name = f"{name}_ensemble"
    ensemble_version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    train_ensemble_model(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        model_name=ensemble_name,
        model_version=ensemble_version,
        algorithms=["xgboost", "lightgbm"],
        conn=conn,
    )

    return {"name": ensemble_name, "version": ensemble_version}


def _run_predict(
    model_name: str, model_version: str, exchange: str, limit: int, conn
) -> Dict[str, int]:
    """Generate predictions and return count."""
    from g2.ml.prediction import generate_predictions

    result = generate_predictions(
        model_name=model_name,
        model_version=model_version,
        exchange=exchange,
        limit=limit,
        conn=conn,
    )

    return {"count": result.get("predictions_stored", 0)}


def _run_predict_ensemble(
    model_name: str, model_version: str, exchange: str, limit: int, conn
) -> Dict[str, int]:
    """Generate ensemble predictions and return count."""
    from g2.ml.prediction import generate_ensemble_predictions

    result = generate_ensemble_predictions(
        model_name=model_name,
        model_version=model_version,
        exchange=exchange,
        limit=limit,
        conn=conn,
    )

    return {"count": result.get("predictions_stored", 0)}


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
