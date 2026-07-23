#!/usr/bin/env python3
"""
Gefion MCP Server - Natural language interface to gefion ML platform.

Provides MCP tools for:
- ML workflow (dataset build, train, predict, evaluate)
  * Quantile regression models for multi-horizon return prediction
  * Trend classifiers for 5-class trend prediction (strong_down to strong_up)
- Database queries (predictions, model performance)
- Feature management (technical indicators + cross-sectional market-relative features)
- Data ingestion (time-aware to prevent partial intraday data)
- Observability (trace analysis, performance monitoring via Grafana Tempo)
"""

import asyncio
import json
import subprocess
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path to import gefion modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env from project root so DATABASE_URL is available
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Import gefion health check module
try:
    from gefion import health
except ImportError:
    # Fallback if gefion module not in path
    health = None


class GefionExecutor:
    """Execute gefion CLI commands and return JSON results."""

    def __init__(self, db_url: Optional[str] = None, api_key: Optional[str] = None):
        self.env = {}
        if db_url:
            self.env['DATABASE_URL'] = db_url
        if api_key:
            self.env['ALPHAVANTAGE_API_KEY'] = api_key

    async def run(self, *args: str) -> Dict[str, Any]:
        """Run gefion command with --json flag and return parsed output."""
        cmd = ['gefion'] + list(args) + ['--json']

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env={**subprocess.os.environ, **self.env},
                timeout=300  # 5 minute timeout
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': result.stderr or result.stdout,
                    'command': ' '.join(cmd)
                }

            # Try to parse JSON output
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                # If not JSON, return raw output
                return {
                    'success': True,
                    'output': result.stdout,
                    'raw': True
                }

        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'Command timed out after 5 minutes',
                'command': ' '.join(cmd)
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'command': ' '.join(cmd)
            }


# ============================================================================
# Health Check Integration
# ============================================================================

class HealthCheckCache:
    """
    Cache health check results with TTL to minimize overhead.

    Caches service health status for a configurable time period to avoid
    repeated health checks on every MCP tool invocation.
    """

    def __init__(self, ttl_seconds: int = 60):
        """
        Initialize health check cache.

        Args:
            ttl_seconds: Time-to-live for cached results in seconds
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._timestamps: Dict[str, float] = {}

    def get_or_check(self, service: str, check_func: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        """
        Get cached health status or perform check if cache is stale.

        Args:
            service: Service name (postgres, tempo, docker)
            check_func: Function to call if cache miss or stale

        Returns:
            Health status dict
        """
        now = time.time()

        # Check if we have a cached result within TTL
        if service in self._cache and service in self._timestamps:
            age = now - self._timestamps[service]
            if age < self.ttl_seconds:
                return self._cache[service]

        # Cache miss or stale - perform health check
        result = check_func()
        self._cache[service] = result
        self._timestamps[service] = now
        return result

    def invalidate(self, service: str) -> None:
        """Invalidate cached health status for a service."""
        self._cache.pop(service, None)
        self._timestamps.pop(service, None)


def check_service_health(service: str) -> Dict[str, Any]:
    """
    Check health of a specific service.

    Args:
        service: Service name (postgres, tempo, docker)

    Returns:
        Health status dict with running, message, and optionally suggestion
    """
    if health is None:
        return {
            "running": True,
            "message": f"{service} health check unavailable (gefion.health module not found)",
            "warning": "Health checks disabled"
        }

    if service == "postgres":
        return health.check_postgres_health()
    elif service == "tempo":
        return health.check_tempo_health()
    elif service == "docker":
        return health.check_docker_services()
    else:
        return {
            "running": False,
            "message": f"Unknown service: {service}",
            "error_type": "unknown_service"
        }


def format_service_error(service: str, health_status: Dict[str, Any]) -> str:
    """
    Format a helpful error message when a service is down.

    Args:
        service: Service name
        health_status: Health status dict from check_service_health

    Returns:
        Formatted error message with suggestions
    """
    message = f"❌ {service.upper()} is not available\n\n"
    message += f"Status: {health_status.get('message', 'Unknown error')}\n"

    if "suggestion" in health_status:
        message += f"\n{health_status['suggestion']}"
    elif service == "postgres":
        message += "\nStart PostgreSQL:\n  docker compose up -d postgres\n"
        message += "\nCheck status:\n  docker compose ps postgres"
    elif service == "tempo":
        message += "\nStart Tempo (for tracing):\n"
        message += "  cd docker/tempo\n"
        message += "  docker compose -f docker-compose.tempo.yml up -d\n"
        message += "\nOr disable tracing:\n"
        message += "  export OTEL_ENABLED=false"

    return message


# Initialize server and health cache
app = Server("gefion-mcp-server")
executor = GefionExecutor()
health_cache = HealthCheckCache(ttl_seconds=60)


# ============================================================================
# Role-Based Access Control (RBAC)
# ============================================================================

# Role configuration from environment (default: operator for safety)
MCP_ROLE = os.environ.get('GEFION_MCP_ROLE', 'operator').lower()
if MCP_ROLE not in ('developer', 'operator'):
    MCP_ROLE = 'operator'  # Default to operator for invalid values

# Tools blocked for operator role
OPERATOR_BLOCKED_TOOLS = {'dev_status'}

# Role descriptions and guidelines
ROLE_INFO = {
    'developer': {
        'description': 'Full access for development and operations',
        'guidelines': [
            'Full access to all tools including dev_status',
            'Can read and modify source code',
            'Can run arbitrary SQL queries',
            'Intended for local development environment',
        ]
    },
    'operator': {
        'description': 'Data operations and monitoring only',
        'guidelines': [
            'Focus on data operations, ML training, and monitoring',
            'Do not suggest code changes or modifications',
            'Do not attempt to read or modify source files',
            'Use MCP tools for all operations',
            'SQL queries are read-only (SELECT only)',
        ]
    }
}

# Log role at startup
import sys
print(f"[gefion-mcp-server] Starting with role: {MCP_ROLE}", file=sys.stderr)


# ============================================================================
# ML Workflow Tools
# ============================================================================

@app.list_tools()
async def list_tools() -> List[Tool]:
    """List all available MCP tools, filtered by role."""
    tools = [
        # ML Workflow
        Tool(
            name="ml_dataset_build",
            description=(
                "Build ML training dataset with features and labels. "
                "Creates manifest, exports data files (prices, features, labels). "
                "Supports CSV (default) or Parquet format for faster loading. "
                "Specify either --symbols (comma-separated) or --exchange + --limit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Dataset name"},
                    "version": {"type": "string", "description": "Dataset version (e.g., v1, v2)"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT,GOOGL)"},
                    "exchange": {"type": "string", "description": "Exchange name (e.g., NASDAQ, NYSE)"},
                    "limit": {"type": "integer", "description": "Limit number of symbols from exchange"},
                    "horizons": {"type": "string", "description": "Comma-separated horizons in days (e.g., 7,30,90)", "default": "7,30,90"},
                    "weak_thresholds": {"type": "string", "description": "Weak move thresholds (e.g., 0.02,0.05,0.10)", "default": "0.02,0.05,0.10"},
                    "strong_thresholds": {"type": "string", "description": "Strong move thresholds (e.g., 0.05,0.10,0.20)", "default": "0.05,0.10,0.20"},
                    "format": {"type": "string", "description": "Export format: csv (default) or parquet (faster loading)", "default": "csv", "enum": ["csv", "parquet"]},
                    "out_dir": {"type": "string", "description": "Output directory for dataset files", "default": "datasets"},
                    "export": {"type": "boolean", "description": "Export data files", "default": True},
                },
                "required": ["name", "version"],
            },
        ),

        Tool(
            name="ml_dataset_inspect",
            description=(
                "Inspect a dataset's metadata and show dependent models. "
                "Returns dataset configuration (universe, horizons, features, thresholds) "
                "and lists all models trained on this dataset."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Dataset name"},
                    "version": {"type": "string", "description": "Dataset version"},
                },
                "required": ["name", "version"],
            },
        ),

        Tool(
            name="ml_train",
            description=(
                "Train quantile regression models for multi-horizon prediction. "
                "Trains q10/q50/q90 models for each horizon. "
                "Saves model artifacts to out_dir/{model_name}_{model_version}_hN/. "
                "Supports warm-start for XGBoost/LightGBM (10-100x faster retraining)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_name": {"type": "string", "description": "Dataset name to train on"},
                    "dataset_version": {"type": "string", "description": "Dataset version to train on"},
                    "model_name": {"type": "string", "description": "Model name for registry"},
                    "model_version": {"type": "string", "description": "Model version (e.g., YYYYMMDD)"},
                    "algorithm": {
                        "type": "string",
                        "description": "Algorithm: quantile_regression (sklearn), xgboost, or lightgbm",
                        "default": "quantile_regression",
                        "enum": ["quantile_regression", "xgboost", "lightgbm"]
                    },
                    "out_dir": {"type": "string", "description": "Output directory for model artifacts", "default": "models"},
                    "warm_start": {
                        "type": "boolean",
                        "description": "Continue training from base model (10-100x faster). Requires base_model path.",
                        "default": False
                    },
                    "base_model": {
                        "type": "string",
                        "description": "Path to base model for warm-start (e.g., models/my_model_v1_h7). Required if warm_start=true."
                    },
                },
                "required": ["dataset_name", "dataset_version", "model_name", "model_version"],
            },
        ),

        Tool(
            name="ml_predict",
            description=(
                "Generate predictions for symbols on a specific date. "
                "Fetches features from database, loads model artifacts, generates q10/q50/q90 predictions. "
                "Stores results in predictions table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name to use"},
                    "model_version": {"type": "string", "description": "Model version to use"},
                    "prediction_date": {"type": "string", "description": "Date for predictions (YYYY-MM-DD)"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT)"},
                    "exchange": {"type": "string", "description": "Exchange name (alternative to symbols)"},
                    "limit": {"type": "integer", "description": "Limit symbols from exchange"},
                },
                "required": ["model_name", "model_version", "prediction_date"],
            },
        ),

        Tool(
            name="ml_predict_backfill",
            description=(
                "Point-in-time prediction backfill for a VINTAGE model (spec 012). "
                "Fills every post-cutoff trading day the model hasn't predicted yet, "
                "over the model's own dataset universe. Resumable (starts after the "
                "last stored prediction), idempotent, and lookahead-proof: refuses "
                "models without a recorded training cutoff and any end date at or "
                "before it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Vintage model name"},
                    "model_version": {"type": "string", "description": "Vintage model version"},
                    "end": {"type": "string", "description": "Backfill through this date (YYYY-MM-DD; default: latest price date)"},
                },
                "required": ["model_name", "model_version"],
            },
        ),

        Tool(
            name="ml_materialize_signals",
            description=(
                "Expose a vintage model's stored predictions as discovery signals "
                "(spec 012): per-stock features named with the model identity "
                "(pred_q50_h30__<model>_<version>) plus two market bodies "
                "(model_outlook_q50, model_confidence_width) seeded into the "
                "DB-resident market dispatcher — compute the series afterwards "
                "with macro derive."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Vintage model name"},
                    "model_version": {"type": "string", "description": "Vintage model version"},
                },
                "required": ["model_name", "model_version"],
            },
        ),

        Tool(
            name="ml_eval",
            description=(
                "Evaluate model performance on historical predictions. "
                "Calculates calibration metrics (q10/q50/q90 coverage, pinball loss, IQR). "
                "Generates evaluation report and stores summary in model_performance table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name to evaluate"},
                    "model_version": {"type": "string", "description": "Model version to evaluate"},
                    "start_date": {"type": "string", "description": "Start date for evaluation period (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date for evaluation period (YYYY-MM-DD)"},
                },
                "required": ["model_name", "model_version", "start_date", "end_date"],
            },
        ),

        Tool(
            name="ml_calibrate",
            description=(
                "Calibrate a quantile model using conformal prediction. "
                "Computes additive shift corrections from a holdout period so that "
                "predicted quantiles achieve their nominal coverage rates (10%, 50%, 90%). "
                "Saves calibration.json alongside model artifacts. "
                "Future predictions automatically apply calibration shifts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name to calibrate"},
                    "model_version": {"type": "string", "description": "Model version to calibrate"},
                    "start_date": {"type": "string", "description": "Calibration period start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Calibration period end date (YYYY-MM-DD)"},
                },
                "required": ["model_name", "model_version", "start_date", "end_date"],
            },
        ),

        Tool(
            name="ml_feature_importance",
            description=(
                "Compute SHAP-based feature importance for a trained model. "
                "Shows which features contribute most to predictions. "
                "Works with XGBoost, LightGBM (fast TreeSHAP) and sklearn models (permutation importance)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name"},
                    "model_version": {"type": "string", "description": "Model version"},
                    "horizon": {"type": "integer", "description": "Horizon in days (e.g., 7, 30, 90)"},
                    "quantile": {"type": "string", "description": "Quantile to analyze (q10, q50, q90)", "default": "q50"},
                    "top_k": {"type": "integer", "description": "Number of top features to return", "default": 20},
                    "out_dir": {"type": "string", "description": "Model artifacts directory", "default": "models"},
                },
                "required": ["model_name", "model_version", "horizon"],
            },
        ),

        Tool(
            name="ml_tune",
            description=(
                "Tune model hyperparameters using Optuna with time-series cross-validation. "
                "Uses Bayesian optimization to find optimal parameters while preventing data leakage. "
                "Supports XGBoost, LightGBM, and sklearn algorithms for both quantile and classifier models."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_name": {"type": "string", "description": "Dataset name to use for tuning"},
                    "dataset_version": {"type": "string", "description": "Dataset version"},
                    "algorithm": {
                        "type": "string",
                        "description": "Algorithm: xgboost, lightgbm, or sklearn",
                        "default": "xgboost",
                        "enum": ["xgboost", "lightgbm", "sklearn"]
                    },
                    "model_type": {
                        "type": "string",
                        "description": "Model type: quantile or classifier",
                        "default": "quantile",
                        "enum": ["quantile", "classifier"]
                    },
                    "horizon": {"type": "integer", "description": "Horizon in days for quantile models", "default": 7},
                    "quantile": {"type": "number", "description": "Quantile to optimize (0.1, 0.5, 0.9)", "default": 0.5},
                    "n_trials": {"type": "integer", "description": "Number of optimization trials", "default": 50},
                    "cv_splits": {"type": "integer", "description": "Number of time-series CV splits", "default": 5},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (optional)"},
                },
                "required": ["dataset_name", "dataset_version"],
            },
        ),

        Tool(
            name="ml_train_classifier",
            description=(
                "Train multi-class trend classifier (5-class: strong_down, weak_down, flat, weak_up, strong_up). "
                "Uses gradient boosting (XGBoost/LightGBM) for trend prediction. "
                "Saves model artifacts to out_dir/{model_name}_{model_version}_hN/"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_name": {"type": "string", "description": "Dataset name to train on"},
                    "dataset_version": {"type": "string", "description": "Dataset version to train on"},
                    "model_name": {"type": "string", "description": "Model name for registry"},
                    "model_version": {"type": "string", "description": "Model version (e.g., YYYYMMDD)"},
                    "algorithm": {
                        "type": "string",
                        "description": "Algorithm: xgboost or lightgbm",
                        "default": "xgboost",
                        "enum": ["xgboost", "lightgbm"]
                    },
                    "out_dir": {"type": "string", "description": "Output directory for model artifacts", "default": "models"},
                },
                "required": ["dataset_name", "dataset_version", "model_name", "model_version"],
            },
        ),

        Tool(
            name="ml_predict_classifier",
            description=(
                "Generate trend class predictions for symbols on a specific date. "
                "Fetches features from database, loads classifier model, predicts trend classes. "
                "Returns probabilities for each class (strong_down, weak_down, flat, weak_up, strong_up). "
                "Stores results in trend_predictions table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name to use"},
                    "model_version": {"type": "string", "description": "Model version to use"},
                    "prediction_date": {"type": "string", "description": "Date for predictions (YYYY-MM-DD)"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT)"},
                    "exchange": {"type": "string", "description": "Exchange name (alternative to symbols)"},
                    "limit": {"type": "integer", "description": "Limit symbols from exchange"},
                },
                "required": ["model_name", "model_version", "prediction_date"],
            },
        ),

        Tool(
            name="ml_train_ensemble",
            description=(
                "Train ensemble model combining multiple algorithms for improved accuracy. "
                "Trains each algorithm separately, then combines predictions via weighted averaging. "
                "Supports quantile_regression, xgboost, and lightgbm algorithms."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_name": {"type": "string", "description": "Dataset name to train on"},
                    "dataset_version": {"type": "string", "description": "Dataset version to train on"},
                    "model_name": {"type": "string", "description": "Ensemble model name for registry"},
                    "model_version": {"type": "string", "description": "Model version (e.g., YYYYMMDD)"},
                    "algorithms": {
                        "type": "string",
                        "description": "Comma-separated algorithms (e.g., xgboost,lightgbm)",
                        "default": "quantile_regression,quantile_regression"
                    },
                    "weights": {
                        "type": "string",
                        "description": "Comma-separated weights (must sum to 1.0). Defaults to equal weights."
                    },
                    "out_dir": {"type": "string", "description": "Output directory for model artifacts", "default": "models"},
                },
                "required": ["dataset_name", "dataset_version", "model_name", "model_version"],
            },
        ),

        Tool(
            name="ml_predict_ensemble",
            description=(
                "Generate predictions using a trained ensemble model. "
                "Loads each base model, generates predictions, computes weighted average. "
                "Stores results in predictions table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Ensemble model name"},
                    "model_version": {"type": "string", "description": "Model version"},
                    "prediction_date": {"type": "string", "description": "Date for predictions (YYYY-MM-DD)"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT)"},
                    "exchange": {"type": "string", "description": "Exchange name (alternative to symbols)"},
                    "limit": {"type": "integer", "description": "Limit symbols from exchange"},
                },
                "required": ["model_name", "model_version", "prediction_date"],
            },
        ),

        Tool(
            name="ml_delete_model",
            description=(
                "Delete one ML model and its OWNED artifacts (predictions, "
                "outcomes, performance rows, materialized signal features) in "
                "dependency order (#76 deletion door). Dry-run by default "
                "(confirm=true executes); an ACTIVE model refuses without "
                "force=true. Training runs/datasets are never deleted. "
                "DESTRUCTIVE — only at explicit user direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Model name"},
                    "version": {"type": "string", "description": "Model version"},
                    "confirm": {"type": "boolean",
                                "description": "Execute (default: dry-run report)"},
                    "force": {"type": "boolean",
                              "description": "Delete even an active model"},
                },
                "required": ["name", "version"],
            },
        ),
        Tool(
            name="ml_e2e_test",
            description=(
                "Run end-to-end ML pipeline test. Tests the complete workflow: "
                "data update -> dataset build -> model training -> ensemble training -> predictions. "
                "Returns success/failure status and metrics for each step."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "exchange": {
                        "type": "string",
                        "description": "Exchange for test data (default: NASDAQ)",
                        "default": "NASDAQ"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of symbols to test with (default: 10)",
                        "default": 10
                    },
                    "skip_data_update": {
                        "type": "boolean",
                        "description": "Skip data update step if data already exists",
                        "default": False
                    },
                },
            },
        ),

        # Database Query Tools
        Tool(
            name="query_predictions",
            description=(
                "Query stored predictions from the unified predictions table. "
                "Returns predictions with symbol, date, horizon, and prediction values. "
                "Use prediction_type to filter by 'quantile' or 'trend_class'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Filter by symbol (e.g., AAPL)"},
                    "model_name": {"type": "string", "description": "Filter by model name"},
                    "prediction_type": {"type": "string", "description": "Filter by prediction type ('quantile' or 'trend_class')", "default": "quantile"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "horizon": {"type": "integer", "description": "Filter by horizon in days (7, 30, or 90)"},
                    "limit": {"type": "integer", "description": "Limit results", "default": 100},
                },
            },
        ),

        Tool(
            name="query_model_performance",
            description=(
                "Query model performance metrics from evaluation runs. "
                "Returns calibration scores, pinball loss, coverage percentages."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Filter by model name"},
                    "limit": {"type": "integer", "description": "Limit results", "default": 10},
                },
            },
        ),

        # Data Management Tools
        Tool(
            name="data_update",
            description=(
                "Update prices and features for an exchange. "
                "Fetches latest OHLCV data from AlphaVantage and computes technical indicators. "
                "Includes time-aware filtering to prevent inserting partial intraday data. "
                "Before 4pm ET: fetches yesterday's data only. After 4pm ET: includes today's data. "
                "Features include technical indicators (RSI, MACD, Bollinger Bands) and cross-sectional "
                "market-relative features (percentile ranks, z-scores)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "exchange": {"type": "string", "description": "Exchange name (e.g., NASDAQ)", "default": "NASDAQ"},
                    "timeframe": {"type": "string", "description": "Timeframe: auto, compact, or full", "default": "auto"},
                    "limit": {"type": "integer", "description": "Limit number of symbols"},
                },
            },
        ),

        Tool(
            name="features_list",
            description=(
                "List all registered feature definitions with metadata. "
                "Includes technical indicators (RSI, MACD, Bollinger Bands, etc.) and "
                "cross-sectional features (market-relative percentile ranks, z-scores)."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        Tool(
            name="feature_show",
            description=(
                "Show details for a single feature definition. "
                "Returns the feature's function, parameters, source/store tables, and active status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature": {"type": "string", "description": "Feature name (e.g., indicator_rsi_14)"},
                },
                "required": ["feature"],
            },
        ),

        Tool(
            name="feature_function_toggle",
            description=(
                "Enable or disable a feature function (#89). Disabling orphans its "
                "definitions (visible in feature_definitions_validate). MUTATING: "
                "confirm with the user before invoking."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Function name"},
                    "enabled": {"type": "boolean", "description": "true=enable, false=disable"},
                },
                "required": ["name", "enabled"],
            },
        ),
        Tool(
            name="feature_definition_delete",
            description=(
                "Delete a feature definition and its computed values (#76 "
                "deletion door). Dry-run by default (confirm=true executes). "
                "Refuses while a regime expression references the feature; "
                "dataset provenance is reported, never mutated. DESTRUCTIVE "
                "— only at explicit user direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Definition name"},
                    "confirm": {"type": "boolean",
                                "description": "Execute (default: dry-run report)"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="feature_function_delete",
            description=(
                "Delete a feature function (#76 deletion door). Dry-run by "
                "default (confirm=true executes); refuses while any "
                "definition routes to it. The candidate ledger survives "
                "(audit). DESTRUCTIVE — only at explicit user direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Function name"},
                    "confirm": {"type": "boolean",
                                "description": "Execute (default: dry-run report)"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="feature_definition_toggle",
            description=(
                "Activate or deactivate a feature definition (feat-compute skips "
                "inactive ones). MUTATING: confirm with the user before invoking."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Definition name"},
                    "active": {"type": "boolean", "description": "true=enable, false=disable"},
                },
                "required": ["name", "active"],
            },
        ),
        Tool(
            name="feature_definitions_validate",
            description=(
                "Report orphaned feature definitions (function missing or disabled). "
                "Read-only; pass fix=true + confirm=true to deactivate orphans "
                "(dry-run report first, always)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "fix": {"type": "boolean", "description": "Run feat-def-fix instead"},
                    "confirm": {"type": "boolean",
                                "description": "With fix: actually deactivate (default dry-run)"},
                },
            },
        ),
        Tool(
            name="feature_functions_list",
            description=(
                "List all registered feature functions. "
                "Feature functions are the computation logic (indicator, derivative, fundamental, etc.) "
                "that feature definitions reference. Shows function name, description, and parameters."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "function": {"type": "string", "description": "Optional function name to filter"},
                    "show_body": {"type": "boolean", "description": "Include function body/code in output", "default": False},
                },
            },
        ),

        Tool(
            name="feature_compute",
            description=(
                "Compute features for symbols using the dispatcher. "
                "Supports all feature types (indicators, derivatives, fundamentals). "
                "Features must be defined in feature_definitions table. "
                "Use --all-features to compute all active features, or specify individual features."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT)"},
                    "features": {"type": "string", "description": "Comma-separated feature names to compute"},
                    "all_features": {"type": "boolean", "description": "Compute all active features", "default": False},
                    "function_names": {"type": "string", "description": "Filter by function type (indicator, derivative, fundamental)"},
                    "full": {"type": "boolean", "description": "Full refresh instead of incremental", "default": False},
                    "update_existing": {"type": "boolean", "description": "Update existing rows on conflict", "default": False},
                },
            },
        ),

        Tool(
            name="feature_definitions_export",
            description=(
                "Export feature definitions to individual JSON files. "
                "By default, exports all definitions to the 'feature-definitions/' directory. "
                "Each definition is saved as <name>.json. "
                "Useful for version control and backup of feature configurations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Directory to write files (default: feature-definitions)"},
                    "features": {"type": "string", "description": "Comma-separated list of feature names to export (default: all)"},
                },
            },
        ),

        Tool(
            name="feature_definitions_import",
            description=(
                "Import feature definitions from individual JSON files. "
                "By default, imports all JSON files from the 'feature-definitions/' directory. "
                "Idempotent: re-running will upsert by name. "
                "Use this to restore feature definitions from version control."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Directory containing JSON files (default: feature-definitions)"},
                    "features": {"type": "string", "description": "Comma-separated list of feature names to import (default: all)"},
                },
            },
        ),

        Tool(
            name="feature_functions_export",
            description=(
                "Export feature functions to individual JSON files. "
                "By default, exports all functions to the 'feature-functions/' directory. "
                "Each function is saved as <name>_v<version>.json. "
                "Useful for version control and backup of custom function definitions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Directory to write files (default: feature-functions)"},
                    "functions": {"type": "string", "description": "Comma-separated list of function names to export (default: all)"},
                },
            },
        ),

        Tool(
            name="feature_functions_import",
            description=(
                "Import feature functions from individual JSON files. "
                "By default, imports all JSON files from the 'feature-functions/' directory. "
                "Idempotent: re-running will upsert by (name, version). "
                "Use this to restore feature functions from version control."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Directory containing JSON files (default: feature-functions)"},
                    "functions": {"type": "string", "description": "Comma-separated list of function names to import (default: all)"},
                },
            },
        ),

        Tool(
            name="cross_sectional_compute",
            description=(
                "Compute cross-sectional rankings for a feature. "
                "Cross-sectional features compare stocks to their peers at the same point in time. "
                "Rankings are computed for different comparison groups: "
                "market (all stocks), sector:X (same sector), industry:X (same industry). "
                "Results stored in cross_sectional_features table with rank and percentile. "
                "Use this after computing features to generate relative rankings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_name": {"type": "string", "description": "Feature to rank (e.g., indicator_rsi_14)"},
                    "date": {"type": "string", "description": "Target date (YYYY-MM-DD). Defaults to latest."},
                    "include_market": {"type": "boolean", "description": "Include market-wide rankings", "default": True},
                    "include_sectors": {"type": "boolean", "description": "Include sector-relative rankings", "default": True},
                    "include_industries": {"type": "boolean", "description": "Include industry-relative rankings", "default": False},
                },
                "required": ["feature_name"],
            },
        ),

        Tool(
            name="query_database",
            description=(
                "Execute read-only SQL queries for data exploration and analysis. "
                "Use for: counting records, checking data coverage, finding gaps, "
                "analyzing distributions, exploring schema. "
                "Returns up to 1000 rows. Automatically adds LIMIT if missing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL query to execute (SELECT only)"},
                    "description": {"type": "string", "description": "Human-readable description of what you're querying"},
                },
                "required": ["sql"],
            },
        ),

        # Observability Tools
        Tool(
            name="span_check",
            description=(
                "Check recent traces for performance monitoring and debugging. "
                "Returns trace statistics, span counts, error detection, and recent trace list. "
                "Use this to: validate tracing is working, find slow operations, detect errors, "
                "get trace IDs for deeper analysis, monitor system health after operations. "
                "Backend-agnostic: uses configured trace backend (Tempo by default)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of recent traces to inspect (1-100)", "default": 10},
                    "trace_id": {"type": "string", "description": "Specific trace ID to inspect (default: most recent)"},
                    "service_name": {"type": "string", "description": "Service name tag to filter by", "default": "gefion"},
                    "backend": {"type": "string", "description": "Trace backend to use (default: tempo)", "default": "tempo"},
                    "backend_url": {"type": "string", "description": "Backend base URL (default: http://localhost:3200 for Tempo)"},
                    "show_spans": {"type": "boolean", "description": "Include detailed span list in output", "default": True},
                },
            },
        ),

        Tool(
            name="trace_search",
            description=(
                "Search for traces by criteria (tags, duration, service). "
                "Returns list of matching traces with metadata (traceID, duration, root span name). "
                "Use this to: find traces for specific symbols, find slow operations, "
                "filter by feature names, search for operations in a time range. "
                "Backend-agnostic: works with any OpenTelemetry-compatible backend."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Service name to search for", "default": "gefion"},
                    "tags": {"type": "string", "description": "Tags to filter by (e.g., 'symbol=AAPL' or 'function_name=indicator')"},
                    "min_duration": {"type": "string", "description": "Minimum duration (e.g., '1s', '500ms')"},
                    "max_duration": {"type": "string", "description": "Maximum duration (e.g., '10s', '5000ms')"},
                    "limit": {"type": "integer", "description": "Maximum number of traces to return", "default": 20},
                    "backend": {"type": "string", "description": "Trace backend to use (default: tempo)", "default": "tempo"},
                    "backend_url": {"type": "string", "description": "Backend base URL (default: http://localhost:3200 for Tempo)"},
                },
            },
        ),

        Tool(
            name="trace_detail",
            description=(
                "Get detailed trace information for a specific trace ID. "
                "Returns complete trace with all spans, attributes, timing, and hierarchy. "
                "Use this to: analyze bottlenecks in a specific operation, investigate errors, "
                "understand span relationships, extract custom attributes and events. "
                "Backend-agnostic: works with any OpenTelemetry-compatible backend."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string", "description": "Trace ID to fetch (required)"},
                    "backend": {"type": "string", "description": "Trace backend to use (default: tempo)", "default": "tempo"},
                    "backend_url": {"type": "string", "description": "Backend base URL (default: http://localhost:3200 for Tempo)"},
                    "include_raw": {"type": "boolean", "description": "Include raw trace data (very verbose, default: false)", "default": False},
                    "max_spans": {"type": "integer", "description": "Limit number of spans returned (default: unlimited)"},
                },
                "required": ["trace_id"],
            },
        ),

        Tool(
            name="trace_compare",
            description=(
                "Compare two traces to quantify performance improvements or regressions. "
                "Analyzes differences in total duration, span counts, and individual span timings. "
                "Use this to: validate optimizations worked, measure performance improvements, "
                "identify which specific operations got faster/slower, compare different approaches. "
                "Returns percentage improvements and detailed breakdown by span type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id_before": {"type": "string", "description": "Trace ID from before the change (baseline)"},
                    "trace_id_after": {"type": "string", "description": "Trace ID from after the change (optimized)"},
                    "focus_spans": {
                        "type": "array",
                        "description": "Optional list of span names to focus comparison on (e.g., ['insert_computed_features', 'compute_features'])",
                        "items": {"type": "string"}
                    },
                    "backend": {"type": "string", "description": "Trace backend to use (default: tempo)", "default": "tempo"},
                    "backend_url": {"type": "string", "description": "Backend base URL (default: http://localhost:3200 for Tempo)"},
                },
                "required": ["trace_id_before", "trace_id_after"],
            },
        ),

        # Infrastructure Tools
        Tool(
            name="system_status",
            description=(
                "Comprehensive system status check with intelligent suggestions. "
                "Analyzes infrastructure health (PostgreSQL, Tempo, Docker), data freshness, "
                "missing features, and provides prioritized actionable suggestions for next steps. "
                "Use this to: get complete system overview, diagnose issues, understand what to do next, "
                "plan workflow (data update → feature computation → ML training). "
                "Returns: infrastructure status, data metrics, identified issues with priorities, "
                "specific commands to run, ordered next steps."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        Tool(
            name="health_check",
            description=(
                "Quick infrastructure health check (PostgreSQL, Tempo, Docker). "
                "For comprehensive status with suggestions, use system_status instead. "
                "Use this for: fast targeted health check without data analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Specific service to check (postgres, tempo, docker) or omit to check all",
                        "enum": ["postgres", "tempo", "docker"]
                    },
                },
            },
        ),

        Tool(
            name="docker_status",
            description=(
                "Check docker compose services status. "
                "For comprehensive status, use system_status instead. "
                "Use this for: quick docker-specific check."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        Tool(
            name="dev_status",
            description=(
                "Analyze development roadmap and suggest next steps. "
                "Parses DEVELOPMENT.md, NEXT_STEPS.md, and PROGRESS.md to provide: "
                "current phase, completed items, in-progress work, strategic path options (Trading/ML/Scale), "
                "ready-to-start tasks with prerequisites met, effort estimates, and development rules. "
                "Use this to: plan what to work on next, understand project status, identify quick wins, "
                "check if prerequisites are met for a task, get reminded of TDD/commit requirements."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Filter by strategic path (A: Trading-First, B: ML-First, C: Scale-First)",
                        "enum": ["A", "B", "C"]
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by completion status",
                        "enum": ["completed", "in_progress", "planned"]
                    },
                    "priority": {
                        "type": "string",
                        "description": "Filter by priority level",
                        "enum": ["high", "medium", "low"]
                    },
                },
            },
        ),

        # Strategy Management Tools
        Tool(
            name="strategy_list",
            description=(
                "List all registered trading strategies. "
                "Returns strategy name, description, tags, and default parameters. "
                "Strategies are Python implementations registered in the database."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        Tool(
            name="strategy_configs",
            description=(
                "List all active strategy configurations. "
                "Configs are parameterized instances of strategies with custom parameters. "
                "Returns config name, strategy reference, merged parameters, and description."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        Tool(
            name="strategy_create_config",
            description=(
                "Create a new strategy configuration. "
                "A config is a named instance of a strategy with custom parameters. "
                "Parameters are merged with strategy defaults (config overrides defaults)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique name for the config (e.g., momentum_aggressive)"
                    },
                    "strategy": {
                        "type": "string",
                        "description": "Strategy name from registry (e.g., momentum, breakout)"
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameters to override defaults (e.g., {lookback_days: 10})"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description of this config"
                    },
                },
                "required": ["name", "strategy"],
            },
        ),

        # Volatility Tools
        Tool(
            name="volatility_compute",
            description=(
                "Compute volatility thresholds for stocks. "
                "Calculates per-stock adaptive thresholds based on historical volatility. "
                "Thresholds scale by sqrt(T) for different horizons. "
                "Stores results in volatility_thresholds table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT)"},
                    "horizons": {"type": "string", "description": "Comma-separated horizons in days (e.g., 7,30,90)", "default": "7,30,90"},
                    "date": {"type": "string", "description": "Calculation date (YYYY-MM-DD). Defaults to today."},
                },
                "required": ["symbols"],
            },
        ),

        # Backtesting Tools
        Tool(
            name="backtest_run",
            description=(
                "Run backtest for a trading strategy with optional realistic execution modeling. "
                "Supports transaction costs (commission, spread, market impact), "
                "slippage (fixed, volume-based, volatility-based), "
                "risk management (stop loss, take profit, position limits), "
                "and position sizing (fixed dollar, fixed percent, Kelly criterion, volatility targeting). "
                "Returns trades, equity curve, and performance metrics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "description": "Strategy name (momentum, mean_reversion, ma_crossover, breakout, pairs_trading, rsi_divergence, volatility_contraction, ml_signal, ml_filter)",
                        "enum": ["momentum", "mean_reversion", "ma_crossover", "breakout", "pairs_trading", "rsi_divergence", "volatility_contraction", "ml_signal", "ml_filter"]
                    },
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., AAPL,MSFT,GOOGL)"},
                    "exchange": {"type": "string", "description": "Exchange name (alternative to symbols)"},
                    "limit": {"type": "integer", "description": "Limit symbols from exchange"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "initial_cash": {"type": "number", "description": "Initial portfolio cash", "default": 100000},
                    "mode": {
                        "type": "string",
                        "description": "long_only (default) or long_short — enable short-side execution so strategies act on bearish signals (spec 009). Surface margin events + short costs, never a short's return without them.",
                        "enum": ["long_only", "long_short"]
                    },
                    "borrow_rate": {"type": "number", "description": "Annualized short borrow fee (long_short)"},
                    "max_short_exposure": {"type": "number", "description": "Cap on short notional / equity (long_short)"},
                    "cost_preset": {
                        "type": "string",
                        "description": "Transaction cost preset (zero, retail, institutional)",
                        "enum": ["zero", "retail", "institutional"]
                    },
                    "slippage_preset": {
                        "type": "string",
                        "description": "Slippage preset (zero, realistic)",
                        "enum": ["zero", "realistic"]
                    },
                    "risk_preset": {
                        "type": "string",
                        "description": "Risk management preset (none, conservative, aggressive)",
                        "enum": ["none", "conservative", "aggressive"]
                    },
                    "sizing_method": {
                        "type": "string",
                        "description": "Position sizing method (fixed_dollar, fixed_percent, kelly, volatility_target)",
                        "enum": ["fixed_dollar", "fixed_percent", "kelly", "volatility_target"]
                    },
                    "sizing_amount": {"type": "number", "description": "Sizing parameter (dollar amount or percent)"},
                    "model_name": {"type": "string", "description": "Model name (required for ml_signal and ml_filter strategies)"},
                    "model_version": {"type": "string", "description": "Model version (required for ml_signal and ml_filter strategies)"},
                    "horizon": {"type": "integer", "description": "Prediction horizon in days (for ML strategies)", "default": 7},
                    "by_regime": {"type": "string", "description": "Slice results by a regime (name) — adds per-regime metrics (spec 005)"},
                },
                "required": ["strategy", "start_date", "end_date"],
            },
        ),

        Tool(
            name="backtest_compare",
            description=(
                "Compare multiple trading strategies on the same data. "
                "Returns side-by-side comparison of performance metrics (total return, Sharpe ratio, max drawdown, etc.). "
                "Can rank strategies by any metric."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "strategies": {
                        "type": "string",
                        "description": "Comma-separated strategy names (e.g., momentum,mean_reversion)"
                    },
                    "all_strategies": {"type": "boolean", "description": "Compare all available strategies"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols"},
                    "exchange": {"type": "string", "description": "Exchange name"},
                    "limit": {"type": "integer", "description": "Limit symbols from exchange"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "initial_cash": {"type": "number", "description": "Initial portfolio cash", "default": 100000},
                    "rank_by": {
                        "type": "string",
                        "description": "Metric to rank by",
                        "default": "sharpe_ratio"
                    },
                    "model_name": {"type": "string", "description": "Model name (required when comparing ml_signal or ml_filter strategies)"},
                    "model_version": {"type": "string", "description": "Model version (required when comparing ml_signal or ml_filter strategies)"},
                },
                "required": ["start_date", "end_date"],
            },
        ),

        # RBAC: Role info tool (available to all roles)
        Tool(
            name="get_role_info",
            description=(
                "Get current MCP server role and behavioral guidelines. "
                "Returns role (developer/operator), description, and guidelines for LLM behavior. "
                "Use this to understand access permissions and expected behavior."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        # ============================================================
        # AI Experimentation Framework Tools
        # ============================================================
        Tool(
            name="experiment_propose",
            description=(
                "Propose a new experiment for approval. "
                "Creates an experiment with 'proposed' status. "
                "Supports all experiment types: strategy_params, hyperparameter, model_comparison, "
                "feature_engineering, feature_selection, label_engineering, pipeline. "
                "For feature_engineering: write a compute(df, **params) function body and pass via "
                "extra_config as JSON with feature_config.function_name and feature_config.function_body. "
                "Functions run in a security sandbox with numpy, pandas, scipy, sklearn, talib."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Experiment name"},
                    "experiment_type": {"type": "string", "default": "strategy_params",
                                        "description": "Experiment type: strategy_params, hyperparameter, model_comparison, feature_engineering, feature_selection, label_engineering, pipeline"},
                    "search_space": {"type": "string", "description": "JSON search space definition"},
                    "objective": {"type": "string", "default": "sharpe_ratio", "description": "Metric to optimize (sharpe_ratio, quantile_loss, etc.)"},
                    "objective_direction": {"type": "string", "default": "maximize", "description": "minimize or maximize"},
                    "max_trials": {"type": "integer", "default": 10, "description": "Maximum trials"},
                    "search_method": {"type": "string", "default": "bayesian", "description": "Search method: grid, random, or bayesian"},
                    "model_type": {"type": "string", "description": "ML model type (lightgbm, xgboost, quantile_regression) — for hyperparameter type"},
                    "dataset_uri": {"type": "string", "description": "Path to dataset manifest (e.g., datasets/baseline_v2/manifest.json)"},
                    "horizon_days": {"type": "integer", "description": "Prediction horizon in days (7, 30)"},
                    "strategy": {"type": "string", "description": "Strategy name — for strategy_params type"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols — for strategy_params type"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "extra_config": {"type": "string", "description": "Extra config as JSON (merged into experiment config)"},
                    "goal_type": {"type": "string", "description": "Goal type: achieve, improve"},
                    "goal_target": {"type": "number", "description": "Target value for goal"},
                    "baseline": {"type": "number", "description": "Baseline for improvement goals"},
                    "early_stop": {"type": "boolean", "default": False, "description": "Stop when goal achieved"},
                },
                "required": ["name", "search_space"],
            },
        ),
        Tool(
            name="experiment_list",
            description=(
                "List experiments with optional filters. "
                "Returns experiment summaries with status, scores, and trial counts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by status (proposed, approved, running, completed, failed)"},
                    "experiment_type": {"type": "string", "description": "Filter by type (strategy_params, hyperparameter, model_comparison, feature_engineering, feature_selection, label_engineering, pipeline)"},
                    "limit": {"type": "integer", "default": 20, "description": "Max results"},
                },
            },
        ),
        Tool(
            name="experiment_approve",
            description=(
                "Approve a proposed experiment for execution. "
                "Changes status from 'proposed' to 'approved'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID to approve"},
                },
                "required": ["experiment_id"],
            },
        ),
        Tool(
            name="experiment_run",
            description=(
                "Run an approved experiment. "
                "Executes all trials, tracks results, and updates best score. "
                "Supports early stopping when goal is achieved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID to run"},
                    "by_regime": {"type": "string", "description": "Also evaluate the holdout conditionally by a regime (name) — spec 005"},
                },
                "required": ["experiment_id"],
            },
        ),
        Tool(
            name="experiment_results",
            description=(
                "Get results for a completed experiment. "
                "Returns best params, best score, trial count, and goal achievement."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID"},
                    "show_trials": {"type": "boolean", "default": False, "description": "Include trial details"},
                },
                "required": ["experiment_id"],
            },
        ),
        Tool(
            name="experiment_chain",
            description=(
                "Create a child experiment chained to a parent. "
                "Child can use parent's best_params or best_score. "
                "Parent must be completed before chaining."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_id": {"type": "integer", "description": "Parent experiment ID"},
                    "name": {"type": "string", "description": "Child experiment name"},
                    "search_space": {"type": "string", "description": "JSON search space for child"},
                    "depends_on": {"type": "string", "default": "best_params", "description": "Parent output to use"},
                    "strategy": {"type": "string", "description": "Strategy name"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols"},
                    "start_date": {"type": "string", "description": "Start date"},
                    "end_date": {"type": "string", "description": "End date"},
                    "max_trials": {"type": "integer", "default": 50, "description": "Max trials"},
                    "search_method": {"type": "string", "default": "grid", "description": "Search method"},
                },
                "required": ["parent_id", "name", "search_space"],
            },
        ),
        Tool(
            name="experiment_children",
            description=(
                "List child experiments of a parent. "
                "Shows all experiments that were chained from a parent experiment."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_id": {"type": "integer", "description": "Parent experiment ID"},
                },
                "required": ["parent_id"],
            },
        ),
        Tool(
            name="experiment_status",
            description=(
                "Get detailed status of an experiment. "
                "Returns full experiment details including config, progress, and results."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID"},
                },
                "required": ["experiment_id"],
            },
        ),

        # ============================================================
        # Autonomous Experiment Framework Tools
        # ============================================================
        Tool(
            name="experiment_discover",
            description=(
                "Discover available data sources and experiment opportunities"
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="experiment_cycle_start",
            description=(
                "Start a new experiment cycle with holdout and FDR configuration"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Cycle name"},
                    "fdr_rate": {"type": "number", "default": 0.10, "description": "False discovery rate threshold"},
                    "holdout_weeks": {"type": "integer", "default": 6, "description": "Holdout period in weeks"},
                    "max_experiments": {"type": "integer", "default": 20, "description": "Maximum experiments per cycle"},
                },
            },
        ),
        Tool(
            name="experiment_cycle_run",
            description=(
                "Run an autonomous experiment cycle. Discovers hypotheses from research themes, "
                "proposes experiments based on cycle guardrails, auto-approves, runs all experiments "
                "in parallel with resource checks, and applies FDR correction to filter false discoveries. "
                "Use experiment_cycle_start first to create the cycle, then this to execute it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cycle_id": {"type": "integer", "description": "Cycle ID to run (from experiment_cycle_start)"},
                },
                "required": ["cycle_id"],
            },
        ),
        Tool(
            name="experiment_apply",
            description=(
                "Apply a promoted experiment winner to production. Runs the full pipeline: "
                "rebuild dataset (with promoted features), retrain the model with winning "
                "parameters, generate predictions, backtest the ml_signal strategy, record "
                "artifacts, and open the probation window. Only completed experiments that "
                "survived FDR correction (or standalone manual experiments) can be applied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID to apply"},
                    "backtest_days": {"type": "integer", "description": "History window in days for predictions and backtest (default 90)"},
                },
                "required": ["experiment_id"],
            },
        ),
        Tool(
            name="experiment_cycle_status",
            description=(
                "Get status of an experiment cycle"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cycle_id": {"type": "integer", "description": "Cycle ID"},
                },
                "required": ["cycle_id"],
            },
        ),
        Tool(
            name="experiment_cycle_list",
            description=(
                "List experiment cycles with status, FDR rate, and completion times"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max cycles to return (default 20)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="experiment_probation_check",
            description=(
                "Check promoted experiments on probation and auto-demote measurably "
                "degraded ones. Compares each applied model's realized quantile loss "
                "against the experiment's score; skips (never demotes) experiments "
                "without an applied model, with too few realized outcomes, or with a "
                "non-comparable objective. Idempotent; also runs automatically at the "
                "end of data_update."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tolerance": {"type": "number", "description": "Relative degradation before demotion (default 0.25 = 25% worse)"},
                    "min_samples": {"type": "integer", "description": "Realized outcomes required before demotion (default 30)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="experiment_delete",
            description=(
                "Delete an experiment, its trials, and its OWNED experimental "
                "features (#76 deletion door). Dry-run by default "
                "(confirm=true executes). Refuses with deliberately NO force "
                "flag: promoted experiments/features (production influence is "
                "an audit fact), regime_discovery experiments (their own "
                "guarded door), experiments with children. DESTRUCTIVE — only "
                "at explicit user direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID"},
                    "confirm": {"type": "boolean",
                                "description": "Execute (default: dry-run report)"},
                },
                "required": ["experiment_id"],
            },
        ),
        Tool(
            name="experiment_demote",
            description=(
                "Manually demote a promoted experiment artifact. Reverses promotion: "
                "stamps demoted_at, sets the feature function to 'demoted', deactivates "
                "its feature definition, and records the reason on the experiment. "
                "Idempotent — demoting an already demoted experiment is a no-op."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID to demote"},
                    "reason": {"type": "string", "description": "Why this artifact is being demoted (recorded on the experiment)"},
                },
                "required": ["experiment_id", "reason"],
            },
        ),
        Tool(
            name="docs_list",
            description=(
                "List gefion's documentation files (user guide, architecture, "
                "backtesting, MCP workflows, troubleshooting, ...) with one-line "
                "summaries. Use docs_read to fetch one."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="docs_read",
            description=(
                "Read one documentation file by name (from docs_list), e.g. "
                "USER_GUIDE.md or README.md. Use this to ground how-to answers "
                "in the actual documentation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Doc filename, e.g. USER_GUIDE.md"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="docs_search",
            description=(
                "Case-insensitive search across all documentation; returns "
                "doc name, line number, and surrounding context per hit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for"},
                    "max_results": {"type": "integer", "description": "Max hits (default 20)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="chart_experiment_trials",
            description=(
                "Generate trial performance scatter chart for an experiment: trial number "
                "vs score with the best trial highlighted. Also writes a parameter-sensitivity "
                "heatmap when exactly two numeric parameters vary. Returns chart file paths."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer", "description": "Experiment ID"},
                },
                "required": ["experiment_id"],
            },
        ),
        Tool(
            name="chart_experiment_fdr",
            description=(
                "Generate FDR cycle summary chart: each experiment's holdout p-value on a "
                "log scale, the FDR threshold line, and promoted vs rejected markers. "
                "Returns the chart file path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cycle_id": {"type": "integer", "description": "Experiment cycle ID"},
                },
                "required": ["cycle_id"],
            },
        ),
        Tool(
            name="principles_list",
            description=(
                "List principles from the quantitative finance catalog"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Filter by domain"},
                    "experiment_type": {"type": "string", "description": "Filter by experiment type"},
                    "status": {"type": "string", "description": "Filter by status"},
                },
            },
        ),
        Tool(
            name="principles_suggest",
            description=(
                "Suggest experiments based on principles and current data"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_type": {"type": "string", "description": "Filter suggestions by experiment type"},
                },
            },
        ),

        # ============================================================
        # Chart Tools
        # ============================================================
        Tool(
            name="chart_price",
            description=(
                "Generate interactive candlestick price chart for a symbol. "
                "Creates HTML chart file and returns rich context with price insights, "
                "volume trends, and technical signals. Auto-opens in browser unless --no-open."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol (e.g., AAPL)"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "indicators": {"type": "string", "description": "Comma-separated indicator names to overlay"},
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="chart_predictions",
            description=(
                "Generate price chart with ML prediction bands (q10/q50/q90). "
                "Shows historical price with quantile prediction confidence intervals. "
                "Returns rich context with predicted direction, confidence width, and insights."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol (e.g., AAPL)"},
                    "model": {"type": "string", "description": "Model name for predictions"},
                    "horizon": {"type": "integer", "description": "Prediction horizon in days", "default": 7},
                },
                "required": ["symbol", "model"],
            },
        ),
        Tool(
            name="chart_features",
            description=(
                "Generate price chart with technical indicator overlays. "
                "Creates subplots for each feature (RSI, MACD, etc.) below price chart. "
                "Returns rich context with technical signals and pattern detection."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol (e.g., AAPL)"},
                    "features": {"type": "string", "description": "Comma-separated feature names (e.g., indicator_rsi_14,indicator_macd)"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                },
                "required": ["symbol", "features"],
            },
        ),
        Tool(
            name="chart_calibration",
            description=(
                "Generate model calibration curve. Shows how well predicted "
                "quantile levels (q10/q50/q90) match observed coverage. "
                "Requires prediction outcomes data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name (e.g., quantile)"},
                },
                "required": ["model_name"],
            },
        ),
        Tool(
            name="chart_confusion_matrix",
            description=(
                "Generate confusion matrix for trend classifier model. "
                "Shows predicted vs actual trend classes (strong_down to strong_up). "
                "Requires prediction outcomes data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name"},
                },
                "required": ["model_name"],
            },
        ),
        Tool(
            name="chart_pipeline_health",
            description=(
                "Generate pipeline health dashboard showing data freshness, "
                "feature coverage, and prediction distribution at a glance."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="chart_pred_vs_actual",
            description=(
                "Generate scatter plot comparing predicted returns (q50) to "
                "actual returns. Shows prediction accuracy visually."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Model name"},
                },
                "required": ["model_name"],
            },
        ),

        # Backup/Restore Tools
        Tool(
            name="backup",
            description=(
                "Backup database data to parquet files. "
                "Creates backup directory with parquet files for each table and manifest. "
                "Supports filtering by data type (ohlcv, features, definitions, functions, all), "
                "date range, and symbols. Use --dry-run to estimate size without creating backup. "
                "Supports incremental backups to only capture new data since last backup."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output": {"type": "string", "description": "Output directory path (required)"},
                    "data_types": {"type": "string", "description": "Data types to backup: ohlcv, features, definitions, functions, all", "default": "all"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "symbols": {"type": "string", "description": "Comma-separated symbols to backup"},
                    "incremental": {"type": "boolean", "description": "Only backup data since last backup", "default": False},
                    "compress": {"type": "boolean", "description": "Compress output files", "default": True},
                    "dry_run": {"type": "boolean", "description": "Show size estimate without creating backup", "default": False},
                    "timestamped": {"type": "boolean", "description": "Treat output as a ROOT: write <root>/<UTC stamp>/ and apply tiered retention (keep 56d dense, newest-per-month 12mo, newest-per-year forever) to siblings after success", "default": False},
                },
                "required": ["output"],
            },
        ),
        Tool(
            name="restore",
            description=(
                "Restore database data from a backup. "
                "Reads parquet files from backup directory and imports into database. "
                "Supports merge mode (skip conflicts) or replace mode (overwrite existing). "
                "Use --dry-run to preview what would be restored. "
                "Verifies backup integrity before restoring by default."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Input backup directory path (required)"},
                    "mode": {"type": "string", "description": "Restore mode: merge (skip conflicts) or replace", "default": "merge"},
                    "data_types": {"type": "string", "description": "Filter data types to restore"},
                    "dry_run": {"type": "boolean", "description": "Show what would be restored without restoring", "default": False},
                    "verify": {"type": "boolean", "description": "Verify backup integrity before restoring", "default": True},
                },
                "required": ["input"],
            },
        ),

        # --- Regime slicing (spec 005) ---
        Tool(
            name="regime_define",
            description="Define and store a regime (declarative expression AST + bucketing).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Regime name (kebab-case slug)"},
                    "scope": {"type": "string", "description": "market|sector|industry|asset"},
                    "expression": {"type": "string", "description": "Path to RegimeExpression AST JSON"},
                    "bucketing": {"type": "string", "description": "Path to bucketing JSON"},
                    "min_dwell": {"type": "integer", "description": "Optional persistence min dwell"},
                },
                "required": ["name", "scope", "expression", "bucketing"],
            },
        ),
        Tool(
            name="regime_list",
            description="List regime definitions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Filter by scope"},
                    "status": {"type": "string", "description": "Filter by status"},
                },
            },
        ),
        Tool(
            name="regime_show",
            description="Show a regime definition (AST, bucketing, persistence, metadata).",
            inputSchema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Regime name"}},
                "required": ["name"],
            },
        ),
        Tool(
            name="regime_compute",
            description="Compute causal labels for a regime from its referenced features.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Regime name"},
                    "dataset": {"type": "string", "description": "Dataset version tag", "default": "dev"},
                    "window": {"type": "integer", "description": "Rolling window", "default": 60},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="regime_labels",
            description="Summarize computed labels: bucket frequencies, episodes, dwell-time.",
            inputSchema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Regime name"}},
                "required": ["name"],
            },
        ),
        Tool(
            name="regime_archive",
            description="Archive a regime definition.",
            inputSchema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Regime name"}},
                "required": ["name"],
            },
        ),
        Tool(
            name="regime_definitions_export",
            description="Export all regime definitions to JSON files (Database-First backup).",
            inputSchema={
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Output directory"}},
                "required": ["directory"],
            },
        ),
        Tool(
            name="regime_definitions_import",
            description="Import regime definitions from JSON files.",
            inputSchema={
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Directory of regime JSON files"}},
                "required": ["directory"],
            },
        ),
        Tool(
            name="regime_interaction",
            description="Test whether a signal's edge varies continuously with a conditioning variable.",
            inputSchema={
                "type": "object",
                "properties": {
                    "signal": {"type": "string", "description": "Signal feature name"},
                    "by": {"type": "string", "description": "Conditioning variable (feature name)"},
                    "horizon_days": {"type": "integer", "description": "Forward-return horizon", "default": 7},
                },
                "required": ["signal", "by"],
            },
        ),
        Tool(
            name="chart_regime",
            description="Chart a symbol's price with regime-episode bands overlaid (spec 005 visualization).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Regime name (must have computed labels)"},
                    "symbol": {"type": "string", "description": "Price symbol to overlay (e.g., SPY)"},
                    "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                },
                "required": ["name", "symbol"],
            },
        ),
        Tool(
            name="regime_discover_start",
            description=(
                "Pre-register and run an agentic regime-discovery run (spec 006): "
                "enumerate a bounded atom grammar, freeze the candidate set, evaluate "
                "conditional edges on the outer holdout, one flat FDR family. MUTATING "
                "and potentially long — confirm with the user before invoking; expect "
                "mostly/entirely rejections (that is correct behavior)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Run name (kebab-case slug)"},
                    "atoms": {"type": "string", "description": "Path to atom-library JSON"},
                    "depth": {"type": "integer", "description": "Max composition depth K", "default": 2},
                    "budget": {"type": "integer", "description": "Per-cycle candidate budget", "default": 100},
                    "tiers": {"type": "array", "items": {"type": "string"},
                              "description": "Tiers enabled: interaction|grammar|expressive"},
                    "signal_source": {"type": "string", "description": "Declared signal universe (v1: features)"},
                    "grading_scheme": {"type": "string", "description": "Declared grading scheme (v1: walk_forward)"},
                    "universe_filter": {"type": "string",
                                        "description": "Declared filter chain; 'passthrough' for unfiltered"},
                    "fresh_holdout": {"type": "string",
                                      "description": "Reserve block START:END (required for expressive tier)"},
                    "freeform": {"type": "string",
                                 "description": "Path to JSON list of free-form ASTs (expressive tier)"},
                    "principles": {"type": "string",
                                   "description": "Comma-separated principle ids to seed atoms/detectors"},
                    "reserve_justification": {"type": "string",
                                              "description": "Recorded justification for re-declaring a consumed reserve"},
                    "min_effective_n": {"type": "integer",
                                        "description": "Episode-based effective-sample floor per bucket (default 20)"},
                    "max_date": {"type": "string",
                                 "description": "Declared vintage YYYY-MM-DD: discover as of a past date (deep validation)"},
                    "seed": {"type": "integer", "description": "Run seed"},
                    "dataset": {"type": "string", "description": "Dataset version tag"},
                },
                "required": ["name", "atoms"],
            },
        ),
        Tool(
            name="regime_discover_list",
            description="List regime-discovery runs (status, family size, dataset).",
            inputSchema={
                "type": "object",
                "properties": {"status": {"type": "string", "description": "Filter by run status"}},
            },
        ),
        Tool(
            name="regime_discover_show",
            description="Inspect a discovery run: pre-registration, segregation boundaries, family size, status.",
            inputSchema={
                "type": "object",
                "properties": {"run": {"type": "string", "description": "Run id or name"}},
                "required": ["run"],
            },
        ),
        Tool(
            name="regime_discover_ledger",
            description=(
                "The candidate ledger of a discovery run: every candidate evaluated, "
                "losers included — they are the FDR family's denominator."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "run": {"type": "string", "description": "Run id or name"},
                    "verdict": {"type": "string",
                                "description": "Filter: admitted|rejected|refused_low_power|"
                                               "refused_degenerate|refused_unstable"},
                },
                "required": ["run"],
            },
        ),
        Tool(
            name="regime_discover_verdicts",
            description=(
                "FDR survivors of a discovery run (most runs: none), always with the "
                "family size beside them. Never present an unadmitted candidate as a finding."
            ),
            inputSchema={
                "type": "object",
                "properties": {"run": {"type": "string", "description": "Run id or name"}},
                "required": ["run"],
            },
        ),
        Tool(
            name="macro_derive",
            description=(
                "Compute derived macro series from the stock cross-section: "
                "breadth_sma200 (% of universe above its own 200-day SMA) and "
                "dispersion_20 (cross-sectional std of 20-day returns). "
                "Idempotent/incremental; thin days get no value. The series "
                "become discovery atoms (macro_breadth_sma200, "
                "macro_dispersion_20) with zero DDL. Mutating (writes feature "
                "values) but derived and re-runnable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "series": {"type": "string",
                               "description": "Comma list or 'all' (default)"},
                    "min_stocks": {"type": "integer",
                                   "description": "Cross-section floor (default 100)"},
                    "full": {"type": "boolean",
                             "description": "Recompute from the beginning"},
                },
            },
        ),
        Tool(
            name="regime_delete",
            description=(
                "Delete a regime definition and its labels. Dry-run by default "
                "(confirm=false): reports labels count, discovery provenance, and "
                "stored experiment results referencing the name (soft references are "
                "reported, never mutated). With confirm=true it is MUTATING and "
                "destructive: always show the user the dry-run and get explicit "
                "approval first. Machine-origin (discovery-admitted) regimes need "
                "force=true; the candidate ledger is never touched either way. "
                "regime_archive is the recommended lifecycle exit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Regime name"},
                    "confirm": {"type": "boolean",
                                "description": "Actually delete (default false = dry-run)"},
                    "force": {"type": "boolean",
                              "description": "Allow deleting a machine-origin regime"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="regime_discover_delete",
            description=(
                "Delete a discovery run and its ledger rows (candidates, grades, "
                "diagnostics, SPA re-verdicts — via the run cascade). For invalid/"
                "test runs. Dry-run by default (confirm=false). A run with ADMITTED "
                "candidates refuses always — the ledger behind an admitted artifact "
                "is the multiple-testing audit trail; there is deliberately no force. "
                "With confirm=true it is MUTATING and destructive: always show the "
                "user the dry-run and get explicit approval first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "run": {"type": "string", "description": "Run id or name"},
                    "confirm": {"type": "boolean",
                                "description": "Actually delete (default false = dry-run)"},
                },
                "required": ["run"],
            },
        ),
        Tool(
            name="regime_discover_spa",
            description=(
                "Selection-aware SPA re-verdict over a completed discovery run's counted "
                "candidate family: reconstructs each unit from the ledger + pre-registration, "
                "verifies recomputed p-values reproduce stored ones (refuses on drift), runs "
                "Hansen's SPA with a joint stationary bootstrap, and records the result "
                "append-only beside the run. Never rewrites BH verdicts or the ledger."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "run": {"type": "string", "description": "Run id or name"},
                    "iterations": {"type": "integer",
                                   "description": "Bootstrap iterations (default 1000)"},
                    "seed": {"type": "integer",
                             "description": "Bootstrap seed (default: the run's own seed)"},
                    "level": {"type": "number",
                              "description": "Pass threshold on the consistent p-value "
                                             "(default: the run's FDR rate)"},
                    "block_length": {"type": "number",
                                     "description": "Expected block length override "
                                                    "(default: Politis-White automatic)"},
                },
                "required": ["run"],
            },
        ),
        Tool(
            name="regime_discover_diagnostics",
            description=(
                "The diagnostics ledger of a discovery run: every limit the search hit "
                "(budget/depth exhaustion, min-sample refusals, uncomputable proposals), "
                "tagged sample-dependent (re-test on new data) vs structural (accumulate)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "run": {"type": "string", "description": "Run id or name"},
                    "kind": {"type": "string",
                             "description": "Filter: sample_dependent | structural"},
                },
                "required": ["run"],
            },
        ),
        Tool(
            name="regime_discover_grades",
            description=(
                "Trust grades for admitted edges: forward folds as they accrue "
                "(fold 1 = probation). Descriptive rows are backward era-slices — "
                "display context only, never counted toward the grade."
            ),
            inputSchema={
                "type": "object",
                "properties": {"candidate": {"type": "string",
                                             "description": "Candidate id (omit for all graded)"}},
            },
        ),
        Tool(
            name="regime_discover_register",
            description=(
                "Re-declare an admitted edge's grading grid (fold width). Allowed only "
                "until real evidence exists — after the first confirmed/failed fold the "
                "grid is locked. MUTATING; confirm with the user."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate": {"type": "string", "description": "Candidate id"},
                    "fold_length_days": {"type": "integer",
                                         "description": "Declared fold width in days"},
                },
                "required": ["candidate", "fold_length_days"],
            },
        ),
        Tool(
            name="regime_discover_grade_fold",
            description=(
                "Re-test an admitted edge on a forward fold window and record the "
                "outcome (MUTATING — appends a trust-grade row; confirm with the user)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate": {"type": "string", "description": "Candidate id"},
                    "fold": {"type": "integer", "description": "Fold number (1 = probation)"},
                },
                "required": ["candidate", "fold"],
            },
        ),
        Tool(
            name="entity_delete",
            description=(
                "Delete an entity (stock, macro series) and its feature-store values, "
                "registry-driven. Dry-run by default (confirm=false): reports the full "
                "blast radius and changes NOTHING. With confirm=true it is MUTATING and "
                "DESTRUCTIVE — the operator must confirm with the user first. Audit "
                "ledgers are never in scope."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_table": {"type": "string",
                                     "description": "Entity table (stocks, macro_series, …)"},
                    "key": {"type": "string",
                            "description": "Natural key (symbol/name) or integer id"},
                    "confirm": {"type": "boolean",
                                "description": "Execute the deletion (default false = dry-run)"},
                },
                "required": ["entity_table", "key"],
            },
        ),
        Tool(
            name="macro_ingest",
            description=(
                "Ingest a macro series (VIX, CPI, …) into the macro home and "
                "materialize its macro_<name> feature for discovery/regimes "
                "(MUTATING — may take minutes with full=true; confirm with the "
                "user first). Default provider fred:<SERIES> is keyless."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Series name (e.g. vix)"},
                    "provider": {"type": "string",
                                 "description": "fred:<SERIES> (default fred:VIXCLS) "
                                                "or alphavantage:INDEX_DATA"},
                    "kind": {"type": "string", "description": "index | rate | breadth"},
                    "cadence": {"type": "string", "description": "daily | weekly | monthly"},
                    "full": {"type": "boolean", "description": "Decades backfill"},
                    "refresh_all": {"type": "boolean",
                                    "description": "Refresh EVERY registered "
                                    "external series incrementally (017 — "
                                    "the nightly-chain form; omit name)"},
                    "include_flagged": {"type": "boolean",
                                        "description": "Carry quality-convicted "
                                        "values into the feature (default: excluded)"},
                },
            },
        ),
        Tool(
            name="macro_seed_sectors",
            description=(
                "Seed generated sector-signal bodies (spec 013): relative "
                "strength and breadth per sector, discovered from "
                "stocks.sector — create-if-absent, an edited DB body is "
                "never overwritten. Compute afterwards with macro derive "
                "--series all. MUTATING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sectors": {"type": "string",
                                "description": "Comma list of sector names "
                                               "(default: every sector meeting min_members)"},
                    "min_members": {"type": "integer",
                                    "description": "Census floor (default 100)"},
                    "body_floor": {"type": "integer",
                                   "description": "Per-date MIN_MEMBERS written "
                                                  "into each body (default 30)"},
                },
            },
        ),
        Tool(
            name="macro_seed_industries",
            description=(
                "Seed generated industry-signal bodies (016): relative "
                "strength and breadth per industry, discovered from "
                "stocks.industry — census counts MODELING-UNIVERSE members "
                "only (spec 015), so shell companies never earn a series. "
                "Create-if-absent; an edited DB body is never overwritten. "
                "Compute afterwards with macro_derive. MUTATING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "industries": {"type": "string",
                                   "description": "Comma list of industry "
                                                  "names (default: every "
                                                  "industry meeting "
                                                  "min_members)"},
                    "min_members": {"type": "integer",
                                    "description": "Census floor over gated "
                                                   "members (default 100)"},
                    "body_floor": {"type": "integer",
                                   "description": "Per-date MIN_MEMBERS "
                                                  "written into each body "
                                                  "(default 30)"},
                },
            },
        ),
        Tool(
            name="observe",
            description=(
                "Record a system observation in the ledger (#144). WHEN TO "
                "USE: while OPERATING gefion (running hunts, cycles, health "
                "checks, investigating anomalies), record anything you notice "
                "about how the system could be improved — a power limitation, "
                "a tuning opportunity, an anomaly, a hypothesis — at the "
                "moment you notice it. Observations are advisory only: "
                "nothing acts on them automatically; a human adopts or "
                "rejects each one. During DEVELOPMENT work, file a GitHub "
                "issue instead. Mutating (one ledger row)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "observation": {"type": "string", "description": "The observation"},
                    "category": {"type": "string",
                                 "description": "improvement | anomaly | tuning | hypothesis"},
                    "observer": {"type": "string",
                                 "description": "Provenance (default claude_session)"},
                    "suggested_action": {"type": "string",
                                         "description": "What the observer would do"},
                    "evidence": {"type": "string",
                                 "description": "JSON evidence (p-values, counts, trace ids)"},
                },
                "required": ["observation", "category"],
            },
        ),
        Tool(
            name="observations_list",
            description=(
                "The system-observations queue (default: open) — what the "
                "machinery noticed and a human has not yet reviewed. "
                "Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {"type": "string",
                              "description": "open (default) | acknowledged | adopted | rejected | all"},
                },
            },
        ),
        Tool(
            name="observations_review",
            description=(
                "HUMAN-DIRECTED act: acknowledge, adopt, or reject an "
                "observation (reject requires a reason; terminal states are "
                "immutable). Only invoke at the user's explicit direction. "
                "MUTATING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "observation_id": {"type": "integer", "description": "Observation id"},
                    "state": {"type": "string",
                              "description": "acknowledged | adopted | rejected"},
                    "reviewer": {"type": "string", "description": "Reviewer identity"},
                    "reason": {"type": "string",
                               "description": "Required for rejected; recommended for adopted"},
                },
                "required": ["observation_id", "state"],
            },
        ),
        Tool(
            name="universe_list",
            description=(
                "All modeling-universe definitions (spec 015) with rule "
                "counts and current exclusion counts. The universe decides "
                "which stocks every cross-section consumer (datasets, "
                "breadth series, rankings, backtests, experiments) sees. "
                "Read-only."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="universe_show",
            description=(
                "One universe in full: rules with reasons, pins, membership "
                "summary, flap counts for time-varying rules. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Universe name"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="universe_members",
            description=(
                "Member symbols of a universe as of a date (membership is "
                "date-aware — intervals, not snapshots). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "Universe name (omit = default; "
                                            "'all' = unfiltered)"},
                    "as_of": {"type": "string",
                              "description": "YYYY-MM-DD (default today)"},
                    "limit": {"type": "integer",
                              "description": "Cap the listing"},
                },
            },
        ),
        Tool(
            name="universe_explain",
            description=(
                "Why is/isn't a symbol in the universe (as of a date)? "
                "Names the exact rule or pin and its reason. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol"},
                    "universe": {"type": "string",
                                 "description": "Universe name (omit = default)"},
                    "as_of": {"type": "string",
                              "description": "YYYY-MM-DD (default today)"},
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="universe_refresh",
            description=(
                "Re-evaluate universe rules and reconcile membership "
                "intervals; prints the delta. MUTATING and consequential: "
                "changes the population every modeling consumer sees. "
                "Refuses empty or outsized-shrink results (guard); only "
                "force past the guard at the user's explicit direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "Universe name (omit = default)"},
                    "force": {"type": "boolean",
                              "description": "Override the shrink guard "
                                             "(HUMAN-DIRECTED only)"},
                },
            },
        ),
        Tool(
            name="universe_define",
            description=(
                "OWNER-GATED act: create or update a universe definition "
                "from YAML rules. Universe definitions are owner-controlled "
                "objects (like feature/regime definitions) — only invoke at "
                "the user's explicit direction, and run universe_refresh "
                "afterwards to apply. MUTATING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Universe name"},
                    "rules_yaml": {"type": "string",
                                   "description": "YAML list of rules "
                                                  "({name, attribute, op, "
                                                  "value, reason}), or a "
                                                  "mapping with rules + pins"},
                    "description": {"type": "string",
                                    "description": "Human description"},
                    "default": {"type": "boolean",
                                "description": "Make this the default "
                                               "universe"},
                },
                "required": ["name", "rules_yaml"],
            },
        ),
        Tool(
            name="universe_delete",
            description=(
                "DESTRUCTIVE, HUMAN-DIRECTED: delete a universe definition "
                "and its membership intervals. Dry-run by default (full "
                "blast radius, changes nothing); refuses while referenced "
                "by result provenance or while default. Only pass "
                "confirm=true at the user's explicit direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Universe name"},
                    "confirm": {"type": "boolean",
                                "description": "Actually delete (default "
                                               "false = dry-run)"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="universe_export",
            description=(
                "Export all universe definitions as YAML (git backup — the "
                "database is the source of truth). Read-only."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="universe_import",
            description=(
                "OWNER-GATED act: import universe definitions from YAML "
                "(validates everything before writing; dry_run reports the "
                "diff). Only invoke at the user's explicit direction. "
                "MUTATING unless dry_run."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "yaml_text": {"type": "string",
                                  "description": "YAML from universe_export"},
                    "dry_run": {"type": "boolean",
                                "description": "Report the diff without "
                                               "writing"},
                },
                "required": ["yaml_text"],
            },
        ),
        Tool(
            name="macro_candidate_list",
            description=(
                "The generated market-function candidate queue (spec 014, "
                "default: pending review). Candidates are machine-proposed "
                "market-scope bodies waiting on the OWNER GATE — they cannot "
                "execute until a human approves. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {"type": "string",
                              "description": "pending (default) | approved | rejected | all"},
                },
            },
        ),
        Tool(
            name="macro_candidate_show",
            description=(
                "The review packet for one candidate: function body, declared "
                "inputs, provenance (origin/principle/generator), and the "
                "seeded sandbox dry-run result. Everything the approve/reject "
                "decision needs, in one place. Read-only (optionally re-runs "
                "the synthetic dry-run)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer", "description": "Candidate id"},
                    "rerun_dry_run": {"type": "boolean",
                                      "description": "Re-execute the seeded dry-run"},
                },
                "required": ["candidate_id"],
            },
        ),
        Tool(
            name="macro_candidate_approve",
            description=(
                "HUMAN-DIRECTED act: approve a reviewed candidate — promotes "
                "it into feature_functions (scope=market, active) with its "
                "paired macro-home definition; the nightly derive adopts it "
                "automatically. Refuses failed/missing dry-runs. Only invoke "
                "when the human has reviewed the packet and asked for "
                "approval — automation must never call this. MUTATING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer", "description": "Candidate id"},
                    "approver": {"type": "string",
                                 "description": "Reviewer identity to record"},
                },
                "required": ["candidate_id"],
            },
        ),
        Tool(
            name="macro_candidate_reject",
            description=(
                "HUMAN-DIRECTED act: reject a candidate with a required "
                "reason. Terminal; the candidate and decision are retained "
                "for audit (never erased). Only invoke at explicit human "
                "direction. MUTATING."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer", "description": "Candidate id"},
                    "reason": {"type": "string",
                               "description": "Required: why this candidate is refused"},
                    "approver": {"type": "string",
                                 "description": "Reviewer identity to record"},
                },
                "required": ["candidate_id", "reason"],
            },
        ),
        Tool(
            name="macro_register_composite",
            description=(
                "Register an OWNER-authored composite market function (spec "
                "014): declared inputs are named macro series; per date the "
                "body receives their stored values and returns one value or "
                "a gap. Unknown inputs and dependency cycles refuse at "
                "registration; the nightly derive runs composites after "
                "their inputs. MUTATING (registers the function; values come "
                "from macro_derive)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Composite series name"},
                    "series": {"type": "string",
                               "description": "Comma list of input macro series"},
                    "body_file": {"type": "string",
                                  "description": "Path to a Python file defining compute(row)"},
                    "description": {"type": "string",
                                    "description": "What the composite measures"},
                },
                "required": ["name", "series", "body_file"],
            },
        ),
        Tool(
            name="macro_propose",
            description=(
                "Explicitly generate a candidate market-scope function body "
                "from a principle (spec 014). The candidate queues for human "
                "review — generation NEVER shortens the gate. MUTATING "
                "(writes a candidate row only; no feature values)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "principle": {"type": "string",
                                  "description": "Principle id driving generation"},
                    "design": {"type": "string",
                               "description": "Free-text design context"},
                    "kind": {"type": "string",
                             "description": "cross_section (default) | composite"},
                },
                "required": ["principle"],
            },
        ),
        Tool(
            name="macro_list",
            description=(
                "List the macro-series catalog with value coverage (first/last "
                "date, row count) and materialization status (read-only)."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="quality_findings",
            description=(
                "List data-quality findings — provider-garbage detections "
                "(rule, observed vs expected, trash/suspect verdict). Default: "
                "unresolved, newest first. Always show the verdict tier: a "
                "suspect is not a conviction. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "symbol": {"type": "string"},
                    "entity_table": {"type": "string"},
                    "entity_id": {"type": "integer"},
                    "verdict": {"type": "string", "description": "trash | suspect"},
                    "since": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="quality_catalog",
            description=(
                "Show the data-quality validation catalog: covered metrics "
                "(bounds, derivations) and uncovered numeric columns — the "
                "coverage gap is enumerable. Read-only."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="quality_backfill",
            description=(
                "Validate already-stored history against the catalog and record "
                "findings for pre-existing provider garbage. **Mutating (ledger "
                "only)** — creates findings, changes NO stored value; may take "
                "minutes on full history. Operator confirms first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_table": {"type": "string", "description": "stocks | macro_series"},
                    "metric": {"type": "string"},
                },
            },
        ),
        Tool(
            name="quality_normalize_taxonomy",
            description=(
                "Normalize stored sector/industry taxonomy: provider sentinels "
                "('NONE', 'OTHER') become NULL, vendor-taxonomy aliases "
                "('FINANCIALS', 'CAPITAL MARKETS') map to the canonical sector. "
                "Dry-run by default (reports every mapping + row count). "
                "**Mutating with apply=true** — rewrites stocks.sector/industry; "
                "operator confirms first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "apply": {"type": "boolean",
                              "description": "Write the changes (default: dry-run)"},
                },
            },
        ),
        Tool(
            name="quality_resolve",
            description=(
                "Supersede a data-quality finding (sets resolution; never "
                "deletes). **Mutating** — operator MUST confirm; reason required."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "finding_id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["finding_id", "reason"],
            },
        ),
    ]

    # RBAC: Filter tools based on role
    if MCP_ROLE == 'operator':
        tools = [t for t in tools if t.name not in OPERATOR_BLOCKED_TOOLS]

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> List[TextContent]:
    """Handle tool invocations."""

    # RBAC: Check if tool is blocked for current role
    if MCP_ROLE == 'operator' and name in OPERATOR_BLOCKED_TOOLS:
        return [TextContent(
            type="text",
            text=json.dumps({
                "success": False,
                "error": f"Access denied: '{name}' is not available in operator role",
                "role": MCP_ROLE,
                "tool": name
            }, indent=2)
        )]

    try:
        import time as _time
        _tool_start = _time.monotonic()
        _span = None

        # Initialize OTEL tracing for MCP server
        try:
            from gefion.observability import create_span, set_attributes, OTEL_ENABLED
            if OTEL_ENABLED:
                from opentelemetry import trace as _trace
                _tracer = _trace.get_tracer("mcp-server")
                _span = _tracer.start_span(f"mcp.{name}")
                _span.set_attribute("mcp.tool", name)
        except Exception:
            pass

        # RBAC: Handle get_role_info tool
        if name == "get_role_info":
            result = await _get_role_info(arguments)
        elif name == "ml_dataset_build":
            result = await _ml_dataset_build(arguments)
        elif name == "ml_dataset_inspect":
            result = await _ml_dataset_inspect(arguments)
        elif name == "ml_train":
            result = await _ml_train(arguments)
        elif name == "ml_predict":
            result = await _ml_predict(arguments)
        elif name == "ml_predict_backfill":
            result = await _ml_predict_backfill(arguments)
        elif name == "ml_materialize_signals":
            result = await _ml_materialize_signals(arguments)
        elif name == "ml_eval":
            result = await _ml_eval(arguments)
        elif name == "ml_calibrate":
            result = await _ml_calibrate(arguments)
        elif name == "ml_feature_importance":
            result = await _ml_feature_importance(arguments)
        elif name == "ml_tune":
            result = await _ml_tune(arguments)
        elif name == "ml_train_classifier":
            result = await _ml_train_classifier(arguments)
        elif name == "ml_predict_classifier":
            result = await _ml_predict_classifier(arguments)
        elif name == "ml_train_ensemble":
            result = await _ml_train_ensemble(arguments)
        elif name == "ml_predict_ensemble":
            result = await _ml_predict_ensemble(arguments)
        elif name == "ml_delete_model":
            result = await _ml_delete_model(arguments)
        elif name == "ml_e2e_test":
            result = await _ml_e2e_test(arguments)
        elif name == "query_predictions":
            result = await _query_predictions(arguments)
        elif name == "query_model_performance":
            result = await _query_model_performance(arguments)
        elif name == "data_update":
            result = await _data_update(arguments)
        elif name == "features_list":
            result = await _features_list(arguments)
        elif name == "feature_show":
            result = await _feature_show(arguments)
        elif name == "feature_function_toggle":
            result = await _feature_function_toggle(arguments)
        elif name == "feature_definition_delete":
            result = await _feature_definition_delete(arguments)
        elif name == "feature_function_delete":
            result = await _feature_function_delete(arguments)
        elif name == "feature_definition_toggle":
            result = await _feature_definition_toggle(arguments)
        elif name == "feature_definitions_validate":
            result = await _feature_definitions_validate(arguments)
        elif name == "feature_functions_list":
            result = await _feature_functions_list(arguments)
        elif name == "feature_compute":
            result = await _feature_compute(arguments)
        elif name == "feature_definitions_export":
            result = await _feature_definitions_export(arguments)
        elif name == "feature_definitions_import":
            result = await _feature_definitions_import(arguments)
        elif name == "feature_functions_export":
            result = await _feature_functions_export(arguments)
        elif name == "feature_functions_import":
            result = await _feature_functions_import(arguments)
        elif name == "cross_sectional_compute":
            result = await _cross_sectional_compute(arguments)
        elif name == "query_database":
            result = await _query_database(arguments)
        elif name == "span_check":
            result = await _span_check(arguments)
        elif name == "trace_search":
            result = await _trace_search(arguments)
        elif name == "trace_detail":
            result = await _trace_detail(arguments)
        elif name == "trace_compare":
            result = await _trace_compare(arguments)
        elif name == "system_status":
            result = await _system_status(arguments)
        elif name == "health_check":
            result = await _health_check(arguments)
        elif name == "docker_status":
            result = await _docker_status(arguments)
        elif name == "dev_status":
            result = await _dev_status(arguments)
        elif name == "strategy_list":
            result = await _strategy_list(arguments)
        elif name == "strategy_configs":
            result = await _strategy_configs(arguments)
        elif name == "strategy_create_config":
            result = await _strategy_create_config(arguments)
        elif name == "volatility_compute":
            result = await _volatility_compute(arguments)
        elif name == "backtest_run":
            result = await _backtest_run(arguments)
        elif name == "backtest_compare":
            result = await _backtest_compare(arguments)
        # Experiment tools
        elif name == "experiment_propose":
            result = await _experiment_propose(arguments)
        elif name == "experiment_list":
            result = await _experiment_list(arguments)
        elif name == "experiment_approve":
            result = await _experiment_approve(arguments)
        elif name == "experiment_run":
            result = await _experiment_run(arguments)
        elif name == "experiment_results":
            result = await _experiment_results(arguments)
        elif name == "experiment_chain":
            result = await _experiment_chain(arguments)
        elif name == "experiment_children":
            result = await _experiment_children(arguments)
        elif name == "experiment_status":
            result = await _experiment_status(arguments)
        # Autonomous experiment framework tools
        elif name == "experiment_discover":
            result = await _experiment_discover(arguments)
        elif name == "experiment_cycle_start":
            result = await _experiment_cycle_start(arguments)
        elif name == "experiment_cycle_run":
            result = await _experiment_cycle_run(arguments)
        elif name == "experiment_apply":
            result = await _experiment_apply(arguments)
        elif name == "experiment_cycle_list":
            result = await _experiment_cycle_list(arguments)
        elif name == "experiment_probation_check":
            result = await _experiment_probation_check(arguments)
        elif name == "experiment_delete":
            result = await _experiment_delete(arguments)
        elif name == "experiment_demote":
            result = await _experiment_demote(arguments)
        elif name == "docs_list":
            result = await _docs_list(arguments)
        elif name == "docs_read":
            result = await _docs_read(arguments)
        elif name == "docs_search":
            result = await _docs_search(arguments)
        elif name == "chart_experiment_trials":
            result = await _chart_experiment_trials(arguments)
        elif name == "chart_experiment_fdr":
            result = await _chart_experiment_fdr(arguments)
        elif name == "experiment_cycle_status":
            result = await _experiment_cycle_status(arguments)
        elif name == "principles_list":
            result = await _principles_list(arguments)
        elif name == "principles_suggest":
            result = await _principles_suggest(arguments)
        # Chart tools
        elif name == "chart_price":
            result = await _chart_price(arguments)
        elif name == "chart_predictions":
            result = await _chart_predictions(arguments)
        elif name == "chart_features":
            result = await _chart_features(arguments)
        elif name == "chart_calibration":
            result = await _chart_calibration(arguments)
        elif name == "chart_confusion_matrix":
            result = await _chart_confusion_matrix(arguments)
        elif name == "chart_pipeline_health":
            result = await _chart_pipeline_health(arguments)
        elif name == "chart_pred_vs_actual":
            result = await _chart_pred_vs_actual(arguments)
        # Backup/Restore tools
        elif name == "backup":
            result = await _backup(arguments)
        elif name == "restore":
            result = await _restore(arguments)
        # Regime slicing (spec 005)
        elif name == "regime_define":
            result = await _regime_define(arguments)
        elif name == "regime_list":
            result = await _regime_list(arguments)
        elif name == "regime_show":
            result = await _regime_show(arguments)
        elif name == "regime_compute":
            result = await _regime_compute(arguments)
        elif name == "regime_labels":
            result = await _regime_labels(arguments)
        elif name == "regime_archive":
            result = await _regime_archive(arguments)
        elif name == "regime_definitions_export":
            result = await _regime_definitions_export(arguments)
        elif name == "regime_definitions_import":
            result = await _regime_definitions_import(arguments)
        elif name == "regime_interaction":
            result = await _regime_interaction(arguments)
        elif name == "chart_regime":
            result = await _chart_regime(arguments)
        elif name == "regime_discover_start":
            result = await _regime_discover_start(arguments)
        elif name == "regime_discover_list":
            result = await _regime_discover_list(arguments)
        elif name == "regime_discover_show":
            result = await _regime_discover_show(arguments)
        elif name == "regime_discover_ledger":
            result = await _regime_discover_ledger(arguments)
        elif name == "regime_discover_verdicts":
            result = await _regime_discover_verdicts(arguments)
        elif name == "macro_derive":
            result = await _macro_derive(arguments)
        elif name == "observe":
            result = await _observe(arguments)
        elif name == "observations_list":
            result = await _observations_list(arguments)
        elif name == "observations_review":
            result = await _observations_review(arguments)
        elif name == "universe_list":
            result = await _universe_list(arguments)
        elif name == "universe_show":
            result = await _universe_show(arguments)
        elif name == "universe_members":
            result = await _universe_members(arguments)
        elif name == "universe_explain":
            result = await _universe_explain(arguments)
        elif name == "universe_refresh":
            result = await _universe_refresh(arguments)
        elif name == "universe_define":
            result = await _universe_define(arguments)
        elif name == "universe_delete":
            result = await _universe_delete(arguments)
        elif name == "universe_export":
            result = await _universe_export(arguments)
        elif name == "universe_import":
            result = await _universe_import(arguments)
        elif name == "macro_candidate_list":
            result = await _macro_candidate_list(arguments)
        elif name == "macro_candidate_show":
            result = await _macro_candidate_show(arguments)
        elif name == "macro_candidate_approve":
            result = await _macro_candidate_approve(arguments)
        elif name == "macro_candidate_reject":
            result = await _macro_candidate_reject(arguments)
        elif name == "macro_propose":
            result = await _macro_propose(arguments)
        elif name == "macro_register_composite":
            result = await _macro_register_composite(arguments)
        elif name == "regime_delete":
            result = await _regime_delete(arguments)
        elif name == "regime_discover_delete":
            result = await _regime_discover_delete(arguments)
        elif name == "regime_discover_spa":
            result = await _regime_discover_spa(arguments)
        elif name == "regime_discover_diagnostics":
            result = await _regime_discover_diagnostics(arguments)
        elif name == "regime_discover_grades":
            result = await _regime_discover_grades(arguments)
        elif name == "regime_discover_register":
            result = await _regime_discover_register(arguments)
        elif name == "regime_discover_grade_fold":
            result = await _regime_discover_grade_fold(arguments)
        elif name == "entity_delete":
            result = await _entity_delete(arguments)
        elif name == "macro_ingest":
            result = await _macro_ingest(arguments)
        elif name == "macro_seed_sectors":
            result = await _macro_seed_sectors(arguments)
        elif name == "macro_seed_industries":
            result = await _macro_seed_industries(arguments)
        elif name == "macro_list":
            result = await _macro_list(arguments)
        elif name == "quality_findings":
            result = await _quality_findings(arguments)
        elif name == "quality_catalog":
            result = await _quality_catalog(arguments)
        elif name == "quality_backfill":
            result = await _quality_backfill(arguments)
        elif name == "quality_normalize_taxonomy":
            result = await _quality_normalize_taxonomy(arguments)
        elif name == "quality_resolve":
            result = await _quality_resolve(arguments)
        else:
            result = {"success": False, "error": f"Unknown tool: {name}"}

        # Close span with success attributes
        duration_ms = int((_time.monotonic() - _tool_start) * 1000)
        if _span:
            success = result.get("success", True) if isinstance(result, dict) else True
            _span.set_attribute("duration_ms", duration_ms)
            _span.set_attribute("success", success)
            _span.end()

        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]

    except Exception as e:
        # Close span with error
        if _span:
            duration_ms = int((_time.monotonic() - _tool_start) * 1000)
            _span.set_attribute("duration_ms", duration_ms)
            _span.set_attribute("success", False)
            _span.set_attribute("error", str(e))
            _span.record_exception(e)
            _span.end()

        return [TextContent(
            type="text",
            text=json.dumps({
                "success": False,
                "error": str(e),
                "tool": name
            }, indent=2)
        )]


# ============================================================================
# Tool Implementations
# ============================================================================

async def _execute_with_health_check(
    required_services: List[str],
    executor_func: Callable[[], Any]
) -> Dict[str, Any]:
    """
    Execute a function after checking required services are healthy.

    Args:
        required_services: List of service names to check (postgres, tempo, docker)
        executor_func: Async function to execute if services are healthy

    Returns:
        Result from executor_func or error dict if services are down
    """
    for service in required_services:
        # Use cached health check (60 second TTL)
        status = health_cache.get_or_check(
            service,
            lambda s=service: check_service_health(s)
        )

        if not status.get("running", True):
            # Service is down - return helpful error
            error_msg = format_service_error(service, status)
            return {
                "success": False,
                "error": error_msg,
                "service_down": service,
                "health_status": status
            }

    # All services healthy - execute the function
    return await executor_func()


async def _ml_dataset_build(args: Dict[str, Any]) -> Dict[str, Any]:
    """Build ML dataset."""
    async def _build():
        cmd = ['ml', 'dataset-build', '--name', args['name'], '--version', args['version']]

        if args.get('symbols'):
            cmd.extend(['--symbols', args['symbols']])
        elif args.get('exchange'):
            cmd.extend(['--exchange', args['exchange']])
            if args.get('limit'):
                cmd.extend(['--limit', str(args['limit'])])

        if args.get('horizons'):
            cmd.extend(['--horizons', args['horizons']])
        if args.get('weak_thresholds'):
            cmd.extend(['--weak-thresholds', args['weak_thresholds']])
        if args.get('strong_thresholds'):
            cmd.extend(['--strong-thresholds', args['strong_thresholds']])
        if args.get('format'):
            cmd.extend(['--format', args['format']])
        if args.get('out_dir'):
            cmd.extend(['--out-dir', args['out_dir']])
        if args.get('export', True):
            cmd.append('--export')

        return await executor.run(*cmd)

    # ML operations require PostgreSQL
    return await _execute_with_health_check(['postgres'], _build)


async def _ml_dataset_inspect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect ML dataset metadata and dependent models."""
    async def _inspect():
        cmd = ['ml', 'dataset-inspect', '--name', args['name'], '--version', args['version']]
        return await executor.run(*cmd)

    # Inspect requires PostgreSQL
    return await _execute_with_health_check(['postgres'], _inspect)


async def _ml_train(args: Dict[str, Any]) -> Dict[str, Any]:
    """Train ML model."""
    cmd = [
        'ml', 'train',
        '--dataset-name', args['dataset_name'],
        '--dataset-version', args['dataset_version'],
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
    ]

    if args.get('algorithm'):
        cmd.extend(['--algorithm', args['algorithm']])
    if args.get('out_dir'):
        cmd.extend(['--out-dir', args['out_dir']])
    if args.get('warm_start'):
        cmd.append('--warm-start')
    if args.get('base_model'):
        cmd.extend(['--base-model', args['base_model']])

    return await executor.run(*cmd)


async def _ml_predict(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate ML predictions."""
    cmd = [
        'ml', 'predict',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
        '--prediction-date', args['prediction_date'],
    ]

    if args.get('symbols'):
        cmd.extend(['--symbols', args['symbols']])
    elif args.get('exchange'):
        cmd.extend(['--exchange', args['exchange']])
        if args.get('limit'):
            cmd.extend(['--limit', str(args['limit'])])

    return await executor.run(*cmd)


async def _ml_predict_backfill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Point-in-time prediction backfill for a vintage model (spec 012)."""
    cmd = [
        'ml', 'predict-backfill',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
    ]
    if args.get('end'):
        cmd.extend(['--end', args['end']])
    return await executor.run(*cmd)


async def _ml_materialize_signals(args: Dict[str, Any]) -> Dict[str, Any]:
    """Expose a vintage model's predictions as discovery signals (spec 012)."""
    cmd = [
        'ml', 'materialize-signals',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
    ]
    return await executor.run(*cmd)


async def _ml_eval(args: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate ML model."""
    cmd = [
        'ml', 'eval',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
        '--start-date', args['start_date'],
        '--end-date', args['end_date'],
    ]

    return await executor.run(*cmd)


async def _ml_calibrate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Calibrate a quantile model using conformal prediction."""
    cmd = [
        'ml', 'calibrate',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
        '--start-date', args['start_date'],
        '--end-date', args['end_date'],
        '--json',
    ]

    return await executor.run(*cmd)


async def _ml_feature_importance(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compute feature importance for ML model."""
    cmd = [
        'ml', 'feature-importance',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
        '--horizon', str(args['horizon']),
        '--json',
    ]

    if args.get('quantile'):
        cmd.extend(['--quantile', args['quantile']])
    if args.get('top_k'):
        cmd.extend(['--top-k', str(args['top_k'])])
    if args.get('out_dir'):
        cmd.extend(['--out-dir', args['out_dir']])

    return await executor.run(*cmd)


async def _ml_tune(args: Dict[str, Any]) -> Dict[str, Any]:
    """Tune hyperparameters using Optuna."""
    cmd = [
        'ml', 'tune',
        '--dataset-name', args['dataset_name'],
        '--dataset-version', args['dataset_version'],
        '--json',
    ]

    if args.get('algorithm'):
        cmd.extend(['--algorithm', args['algorithm']])
    if args.get('model_type'):
        cmd.extend(['--model-type', args['model_type']])
    if args.get('horizon'):
        cmd.extend(['--horizon', str(args['horizon'])])
    if args.get('quantile'):
        cmd.extend(['--quantile', str(args['quantile'])])
    if args.get('n_trials'):
        cmd.extend(['--n-trials', str(args['n_trials'])])
    if args.get('cv_splits'):
        cmd.extend(['--cv-splits', str(args['cv_splits'])])
    if args.get('timeout'):
        cmd.extend(['--timeout', str(args['timeout'])])

    return await executor.run(*cmd)


async def _ml_train_classifier(args: Dict[str, Any]) -> Dict[str, Any]:
    """Train trend classifier model."""
    cmd = [
        'ml', 'train-classifier',
        '--dataset-name', args['dataset_name'],
        '--dataset-version', args['dataset_version'],
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
    ]

    if args.get('algorithm'):
        cmd.extend(['--algorithm', args['algorithm']])
    if args.get('out_dir'):
        cmd.extend(['--out-dir', args['out_dir']])

    return await executor.run(*cmd)


async def _ml_predict_classifier(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate trend classifier predictions."""
    cmd = [
        'ml', 'predict-classifier',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
        '--prediction-date', args['prediction_date'],
    ]

    if args.get('symbols'):
        cmd.extend(['--symbols', args['symbols']])
    elif args.get('exchange'):
        cmd.extend(['--exchange', args['exchange']])
        if args.get('limit'):
            cmd.extend(['--limit', str(args['limit'])])

    return await executor.run(*cmd)


async def _ml_train_ensemble(args: Dict[str, Any]) -> Dict[str, Any]:
    """Train ensemble model combining multiple algorithms."""
    cmd = [
        'ml', 'train-ensemble',
        '--dataset-name', args['dataset_name'],
        '--dataset-version', args['dataset_version'],
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
    ]

    if args.get('algorithms'):
        cmd.extend(['--algorithms', args['algorithms']])
    if args.get('weights'):
        cmd.extend(['--weights', args['weights']])
    if args.get('out_dir'):
        cmd.extend(['--out-dir', args['out_dir']])

    return await executor.run(*cmd)


async def _ml_predict_ensemble(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate predictions using trained ensemble model."""
    cmd = [
        'ml', 'predict-ensemble',
        '--model-name', args['model_name'],
        '--model-version', args['model_version'],
        '--prediction-date', args['prediction_date'],
    ]

    if args.get('symbols'):
        cmd.extend(['--symbols', args['symbols']])
    elif args.get('exchange'):
        cmd.extend(['--exchange', args['exchange']])
        if args.get('limit'):
            cmd.extend(['--limit', str(args['limit'])])

    return await executor.run(*cmd)


async def _ml_delete_model(args: Dict[str, Any]) -> Dict[str, Any]:
    """Per-model artifact deletion — dry-run default, refusals surface."""
    async def _run():
        cmd = ["ml", "delete-model", "--name", args["name"],
               "--version", args["version"]]
        if args.get("confirm"):
            cmd.append("--confirm")
        if args.get("force"):
            cmd.append("--force")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _ml_e2e_test(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run end-to-end ML pipeline test."""
    exchange = args.get('exchange', 'NASDAQ')
    limit = args.get('limit', 10)
    skip_data_update = args.get('skip_data_update', False)

    results = {
        'success': True,
        'steps': {},
        'exchange': exchange,
        'limit': limit,
    }

    # Generate unique test identifiers
    import time
    test_id = f"e2e_{int(time.time())}"
    dataset_name = f"{test_id}_dataset"
    model_name = f"{test_id}_model"
    ensemble_name = f"{test_id}_ensemble"
    version = "v1"

    try:
        # Step 1: Data Update (optional)
        if not skip_data_update:
            step1 = await executor.run(
                'data-update',
                '--exchange', exchange,
                '--limit', str(limit),
            )
            results['steps']['1_data_update'] = {
                'success': step1.get('status') == 'ok',
                'output': step1
            }
            if step1.get('status') != 'ok' and 'error' in step1:
                results['success'] = False
                results['error'] = f"Data update failed: {step1.get('error')}"
                return results
        else:
            results['steps']['1_data_update'] = {'skipped': True}

        # Step 2: Build Dataset
        step2 = await executor.run(
            'ml', 'dataset-build',
            '--name', dataset_name,
            '--version', version,
            '--exchange', exchange,
            '--limit', str(limit),
            '--horizons', '7,30',
            '--export',
        )
        results['steps']['2_dataset_build'] = {
            'success': step2.get('status') == 'ok',
            'output': step2
        }
        if step2.get('status') != 'ok':
            results['success'] = False
            results['error'] = f"Dataset build failed: {step2.get('error', step2)}"
            return results

        # Step 3: Train Single Model (baseline)
        step3 = await executor.run(
            'ml', 'train',
            '--dataset-name', dataset_name,
            '--dataset-version', version,
            '--model-name', model_name,
            '--model-version', version,
            '--algorithm', 'quantile_regression',
        )
        results['steps']['3_train_model'] = {
            'success': step3.get('status') == 'ok',
            'output': step3
        }
        if step3.get('status') != 'ok':
            results['success'] = False
            results['error'] = f"Model training failed: {step3.get('error', step3)}"
            return results

        # Step 4: Train Ensemble
        step4 = await executor.run(
            'ml', 'train-ensemble',
            '--dataset-name', dataset_name,
            '--dataset-version', version,
            '--model-name', ensemble_name,
            '--model-version', version,
            '--algorithms', 'quantile_regression,quantile_regression',
        )
        results['steps']['4_train_ensemble'] = {
            'success': step4.get('status') == 'ok',
            'output': step4
        }
        if step4.get('status') != 'ok':
            results['success'] = False
            results['error'] = f"Ensemble training failed: {step4.get('error', step4)}"
            return results

        # Step 5: Get latest date with features for predictions
        step5a = await executor.run(
            'query-database',
            '--sql', 'SELECT MAX(date)::text FROM computed_features',
        )
        # Parse the date from output
        pred_date = None
        if step5a.get('status') == 'ok' and step5a.get('rows'):
            pred_date = step5a['rows'][0][0]

        if not pred_date:
            results['steps']['5_predictions'] = {
                'success': False,
                'error': 'Could not determine prediction date'
            }
            results['success'] = False
            return results

        # Generate predictions with single model
        step5b = await executor.run(
            'ml', 'predict',
            '--model-name', model_name,
            '--model-version', version,
            '--prediction-date', pred_date,
            '--exchange', exchange,
            '--limit', str(limit),
        )
        results['steps']['5_predictions'] = {
            'success': step5b.get('status') == 'ok',
            'prediction_date': pred_date,
            'output': step5b
        }

        # Step 6: Generate ensemble predictions
        step6 = await executor.run(
            'ml', 'predict-ensemble',
            '--model-name', ensemble_name,
            '--model-version', version,
            '--prediction-date', pred_date,
            '--exchange', exchange,
            '--limit', str(limit),
        )
        results['steps']['6_ensemble_predictions'] = {
            'success': step6.get('status') == 'ok',
            'output': step6
        }

        # Summary
        results['summary'] = {
            'dataset_name': dataset_name,
            'model_name': model_name,
            'ensemble_name': ensemble_name,
            'version': version,
            'prediction_date': pred_date,
            'all_steps_passed': all(
                s.get('success', s.get('skipped', False))
                for s in results['steps'].values()
            )
        }
        results['success'] = results['summary']['all_steps_passed']

    except Exception as e:
        results['success'] = False
        results['error'] = str(e)

    return results


async def _query_predictions(args: Dict[str, Any]) -> Dict[str, Any]:
    """Query predictions from database using SQL."""
    # Build SQL query
    prediction_type = args.get('prediction_type', 'quantile')
    where_clauses = [f"p.prediction_type = '{prediction_type}'"]
    if args.get('symbol'):
        where_clauses.append(f"s.symbol = '{args['symbol']}'")
    if args.get('model_name'):
        where_clauses.append(f"m.name = '{args['model_name']}'")
    if args.get('start_date'):
        where_clauses.append(f"p.prediction_date >= '{args['start_date']}'")
    if args.get('end_date'):
        where_clauses.append(f"p.prediction_date <= '{args['end_date']}'")
    if args.get('horizon'):
        where_clauses.append(f"p.horizon_days = {args['horizon']}")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    limit = args.get('limit', 100)

    if prediction_type == 'quantile':
        sql = f"""
            SELECT
                s.symbol,
                p.prediction_date,
                p.horizon_days,
                (p.prediction_values->>'q10')::NUMERIC,
                (p.prediction_values->>'q50')::NUMERIC,
                (p.prediction_values->>'q90')::NUMERIC,
                ((p.prediction_values->>'q90')::NUMERIC - (p.prediction_values->>'q10')::NUMERIC) as iqr,
                m.name as model_name,
                m.version as model_version
            FROM predictions p
            JOIN stocks s ON p.data_id = s.id
            JOIN ml_models m ON p.model_id = m.id
            WHERE {where_sql}
            ORDER BY p.prediction_date DESC, s.symbol, p.horizon_days
            LIMIT {limit}
        """
    else:
        sql = f"""
            SELECT
                s.symbol,
                p.prediction_date,
                p.horizon_days,
                p.prediction_values->>'predicted_class',
                (p.prediction_values->>'p_strong_up')::NUMERIC,
                (p.prediction_values->>'p_weak_up')::NUMERIC,
                (p.prediction_values->>'margin')::NUMERIC,
                m.name as model_name,
                m.version as model_version
            FROM predictions p
            JOIN stocks s ON p.data_id = s.id
            JOIN ml_models m ON p.model_id = m.id
            WHERE {where_sql}
            ORDER BY p.prediction_date DESC, s.symbol, p.horizon_days
            LIMIT {limit}
        """

    # Execute via psql (gefion doesn't have a direct SQL query command)
    import os
    db_url = os.environ.get('DATABASE_URL', 'postgresql://gefion:gefionpass@localhost:6432/gefion')

    try:
        result = subprocess.run(
            ['psql', db_url, '-t', '-A', '-F,', '-c', sql],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {'success': False, 'error': result.stderr}

        # Parse CSV output
        lines = result.stdout.strip().split('\n')
        if not lines or not lines[0]:
            return {'success': True, 'predictions': [], 'count': 0}

        predictions = []
        for line in lines:
            parts = line.split(',')
            if prediction_type == 'quantile' and len(parts) >= 8:
                predictions.append({
                    'symbol': parts[0],
                    'prediction_date': parts[1],
                    'horizon_days': int(parts[2]),
                    'q10': float(parts[3]),
                    'q50': float(parts[4]),
                    'q90': float(parts[5]),
                    'iqr': float(parts[6]),
                    'model_name': parts[7],
                    'model_version': parts[8] if len(parts) > 8 else None,
                })
            elif prediction_type == 'trend_class' and len(parts) >= 8:
                predictions.append({
                    'symbol': parts[0],
                    'prediction_date': parts[1],
                    'horizon_days': int(parts[2]),
                    'predicted_class': parts[3],
                    'p_strong_up': float(parts[4]),
                    'p_weak_up': float(parts[5]),
                    'margin': float(parts[6]),
                    'model_name': parts[7],
                    'model_version': parts[8] if len(parts) > 8 else None,
                })

        return {
            'success': True,
            'predictions': predictions,
            'count': len(predictions)
        }

    except Exception as e:
        return {'success': False, 'error': str(e)}


async def _query_model_performance(args: Dict[str, Any]) -> Dict[str, Any]:
    """Query model performance metrics."""
    where_sql = f"m.name = '{args['model_name']}'" if args.get('model_name') else "1=1"
    limit = args.get('limit', 10)

    sql = f"""
        SELECT
            m.name as model_name,
            m.version as model_version,
            mp.horizon_days,
            mp.eval_start_date,
            mp.eval_end_date,
            mp.num_samples,
            mp.q10_calibration,
            mp.q50_calibration,
            mp.q90_calibration,
            mp.quantile_loss,
            mp.avg_iqr,
            mp.interval_80_coverage,
            mp.evaluated_at
        FROM model_performance mp
        JOIN ml_models m ON mp.model_id = m.id
        WHERE {where_sql}
        ORDER BY mp.evaluated_at DESC
        LIMIT {limit}
    """

    import os
    db_url = os.environ.get('DATABASE_URL', 'postgresql://gefion:gefionpass@localhost:6432/gefion')

    try:
        result = subprocess.run(
            ['psql', db_url, '-t', '-A', '-F,', '-c', sql],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {'success': False, 'error': result.stderr}

        lines = result.stdout.strip().split('\n')
        if not lines or not lines[0]:
            return {'success': True, 'performance': [], 'count': 0}

        performance = []
        for line in lines:
            parts = line.split(',')
            if len(parts) >= 13:
                performance.append({
                    'model_name': parts[0],
                    'model_version': parts[1],
                    'horizon_days': int(parts[2]),
                    'eval_start_date': parts[3],
                    'eval_end_date': parts[4],
                    'num_samples': int(parts[5]),
                    'q10_calibration': float(parts[6]),
                    'q50_calibration': float(parts[7]),
                    'q90_calibration': float(parts[8]),
                    'quantile_loss': float(parts[9]),
                    'avg_iqr': float(parts[10]),
                    'interval_80_coverage': float(parts[11]),
                    'evaluated_at': parts[12],
                })

        return {
            'success': True,
            'performance': performance,
            'count': len(performance)
        }

    except Exception as e:
        return {'success': False, 'error': str(e)}


async def _data_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update prices and features."""
    cmd = ['data-update']

    if args.get('exchange'):
        cmd.extend(['--exchange', args['exchange']])
    if args.get('timeframe'):
        cmd.extend(['--timeframe', args['timeframe']])
    if args.get('limit'):
        cmd.extend(['--limit', str(args['limit'])])

    return await executor.run(*cmd)


async def _backup(args: Dict[str, Any]) -> Dict[str, Any]:
    """Backup database data to parquet files."""
    output = args.get('output')
    if not output:
        return {'success': False, 'error': 'output is required'}

    cmd = ['backup', '--output', output]

    if args.get('data_types'):
        cmd.extend(['--data-types', args['data_types']])
    if args.get('start_date'):
        cmd.extend(['--start-date', args['start_date']])
    if args.get('end_date'):
        cmd.extend(['--end-date', args['end_date']])
    if args.get('symbols'):
        cmd.extend(['--symbols', args['symbols']])
    if args.get('incremental'):
        cmd.append('--incremental')
    if args.get('compress') is False:
        cmd.append('--no-compress')
    if args.get('dry_run'):
        cmd.append('--dry-run')
    if args.get('timestamped'):
        cmd.append('--timestamped')

    return await executor.run(*cmd)


async def _restore(args: Dict[str, Any]) -> Dict[str, Any]:
    """Restore database data from a backup."""
    input_path = args.get('input')
    if not input_path:
        return {'success': False, 'error': 'input is required'}

    cmd = ['restore', '--input', input_path]

    if args.get('mode'):
        cmd.extend(['--mode', args['mode']])
    if args.get('data_types'):
        cmd.extend(['--data-types', args['data_types']])
    if args.get('dry_run'):
        cmd.append('--dry-run')
    if args.get('verify') is False:
        cmd.append('--no-verify')

    return await executor.run(*cmd)


async def _features_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List feature definitions."""
    return await executor.run('feat-def-list', '--json')


async def _feature_show(args: Dict[str, Any]) -> Dict[str, Any]:
    """Show a single feature definition."""
    feature = args.get('feature')
    if not feature:
        return {'success': False, 'error': 'feature is required'}
    return await executor.run('feat-def-show', '--feature', feature, '--json')


async def _feature_function_toggle(args: Dict[str, Any]) -> Dict[str, Any]:
    """Enable/disable a feature function (mutating)."""
    async def _run():
        cmd = "feat-fx-enable" if args["enabled"] else "feat-fx-disable"
        return await GefionExecutor().run(cmd, args["name"])
    return await _execute_with_health_check(['postgres'], _run)


async def _feature_definition_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Definition deletion — dry-run default, refusals surface verbatim."""
    async def _run():
        cmd = ["feat-def-delete", args["name"]]
        if args.get("confirm"):
            cmd.append("--confirm")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _feature_function_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Function deletion — dry-run default, refusals surface verbatim."""
    async def _run():
        cmd = ["feat-fx-delete", args["name"]]
        if args.get("confirm"):
            cmd.append("--confirm")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _feature_definition_toggle(args: Dict[str, Any]) -> Dict[str, Any]:
    """Activate/deactivate a feature definition (mutating)."""
    async def _run():
        cmd = "feat-def-enable" if args["active"] else "feat-def-disable"
        return await GefionExecutor().run(cmd, args["name"])
    return await _execute_with_health_check(['postgres'], _run)


async def _feature_definitions_validate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Orphaned-definition report; optional guarded fix."""
    async def _run():
        if args.get("fix"):
            cmd = ["feat-def-fix"]
            if args.get("confirm"):
                cmd.append("--confirm")
            return await GefionExecutor().run(*cmd)
        return await GefionExecutor().run("feat-def-validate")
    return await _execute_with_health_check(['postgres'], _run)


async def _feature_functions_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List feature functions."""
    cmd = ['feat-fx-list', '--json']
    if args.get('function'):
        cmd.extend(['--feature', args['function']])
    if args.get('show_body'):
        cmd.append('--show-body')
    return await executor.run(*cmd)


async def _feature_compute(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compute features for symbols."""
    cmd = ['feat-compute', '--json']

    if args.get('symbols'):
        cmd.extend(['--symbols', args['symbols']])
    if args.get('features'):
        cmd.extend(['--features', args['features']])
    if args.get('all_features'):
        cmd.append('--all-features')
    if args.get('function_names'):
        cmd.extend(['--function-names', args['function_names']])
    if args.get('full'):
        cmd.append('--full')
    if args.get('update_existing'):
        cmd.append('--update-existing')

    return await executor.run(*cmd)


async def _feature_definitions_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """Export feature definitions to JSON files."""
    cmd = ['feat-def-export', '--json']

    if args.get('dir'):
        cmd.extend(['--dir', args['dir']])
    if args.get('features'):
        cmd.extend(['--features', args['features']])

    return await executor.run(*cmd)


async def _feature_definitions_import(args: Dict[str, Any]) -> Dict[str, Any]:
    """Import feature definitions from JSON files."""
    cmd = ['feat-def-import', '--json']

    if args.get('dir'):
        cmd.extend(['--dir', args['dir']])
    if args.get('features'):
        cmd.extend(['--features', args['features']])

    return await executor.run(*cmd)


async def _feature_functions_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """Export feature functions to JSON files."""
    cmd = ['feat-fx-export', '--json']

    if args.get('dir'):
        cmd.extend(['--dir', args['dir']])
    if args.get('functions'):
        cmd.extend(['--functions', args['functions']])

    return await executor.run(*cmd)


async def _feature_functions_import(args: Dict[str, Any]) -> Dict[str, Any]:
    """Import feature functions from JSON files."""
    cmd = ['feat-fx-import', '--json']

    if args.get('dir'):
        cmd.extend(['--dir', args['dir']])
    if args.get('functions'):
        cmd.extend(['--functions', args['functions']])

    return await executor.run(*cmd)


async def _cross_sectional_compute(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compute cross-sectional rankings for a feature."""
    feature_name = args.get('feature_name')
    if not feature_name:
        return {'success': False, 'error': 'feature_name is required'}

    cmd = ['cross-sectional-compute', '--feature', feature_name, '--json']

    if args.get('date'):
        cmd.extend(['--date', args['date']])
    if args.get('include_market') is False:
        cmd.append('--no-market')
    if args.get('include_sectors') is False:
        cmd.append('--no-sectors')
    if args.get('include_industries') is True:
        cmd.append('--industries')

    return await executor.run(*cmd)


async def _query_database(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute read-only SQL query for data exploration."""
    sql = args['sql'].strip()

    # Safety checks - only allow SELECT queries
    sql_upper = sql.upper()
    dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE', 'GRANT', 'REVOKE']

    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            return {
                'success': False,
                'error': f'Dangerous SQL keyword detected: {keyword}. Only SELECT queries allowed.',
                'sql': sql
            }

    if not sql_upper.startswith('SELECT') and not sql_upper.startswith('WITH'):
        return {
            'success': False,
            'error': 'Only SELECT queries (and CTEs with WITH) are allowed',
            'sql': sql
        }

    # Add LIMIT if missing (safety against huge result sets)
    if 'LIMIT' not in sql_upper:
        sql = f"{sql.rstrip(';')} LIMIT 1000"

    # Execute query
    import os
    db_url = os.environ.get('DATABASE_URL', 'postgresql://gefion:gefionpass@localhost:6432/gefion')

    try:
        result = subprocess.run(
            ['psql', db_url, '-t', '-A', '-F,', '-c', sql],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            return {
                'success': False,
                'error': result.stderr,
                'sql': sql
            }

        # Parse CSV output
        lines = result.stdout.strip().split('\n')
        if not lines or not lines[0]:
            return {
                'success': True,
                'rows': [],
                'count': 0,
                'sql': sql,
                'description': args.get('description', '')
            }

        # Return raw CSV lines (Claude can interpret them)
        rows = [line.split(',') for line in lines if line]

        return {
            'success': True,
            'rows': rows,
            'count': len(rows),
            'sql': sql,
            'description': args.get('description', ''),
            'note': 'Results limited to 1000 rows' if 'LIMIT' not in sql_upper else ''
        }

    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Query timed out after 60 seconds',
            'sql': sql
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'sql': sql
        }


async def _span_check(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check recent traces using gefion span-check command (backend-agnostic)."""
    async def _check():
        cmd = ['span-check']

        if args.get('limit'):
            cmd.extend(['--limit', str(args['limit'])])
        if args.get('trace_id'):
            cmd.extend(['--trace-id', args['trace_id']])
        if args.get('service_name'):
            cmd.extend(['--service-name', args['service_name']])
        if args.get('backend'):
            cmd.extend(['--backend', args['backend']])
        if args.get('backend_url'):
            # Map backend_url to the appropriate CLI flag based on backend
            backend = args.get('backend', 'tempo')
            if backend == 'tempo':
                cmd.extend(['--tempo-url', args['backend_url']])
            # Future backends can be added here
        if args.get('show_spans') is False:
            cmd.append('--no-show-spans')

        return await executor.run(*cmd)

    # Span checking requires Tempo tracing backend
    return await _execute_with_health_check(['tempo'], _check)


async def _trace_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search for traces using the trace backend API (backend-agnostic)."""
    import requests

    backend = args.get('backend', 'tempo')
    backend_url = args.get('backend_url', 'http://localhost:3200')
    service_name = args.get('service_name', 'gefion')
    limit = args.get('limit', 20)

    # Currently only Tempo is implemented, but structured for future backends
    if backend == 'tempo':
        return await _search_tempo(backend_url, service_name, limit, args)
    else:
        return {
            'success': False,
            'error': f'Unsupported trace backend: {backend}',
            'supported_backends': ['tempo']
        }


async def _search_tempo(tempo_url: str, service_name: str, limit: int, args: Dict[str, Any]) -> Dict[str, Any]:
    """Search traces in Tempo backend."""
    import requests

    # Build search parameters
    params = {
        'limit': limit
    }

    # Build tags filter
    tags = []
    tags.append(f'service.name={service_name}')

    if args.get('tags'):
        tags.append(args['tags'])

    params['tags'] = ' && '.join(tags) if len(tags) > 1 else tags[0]

    # Add duration filters if specified
    if args.get('min_duration'):
        params['minDuration'] = args['min_duration']
    if args.get('max_duration'):
        params['maxDuration'] = args['max_duration']

    try:
        url = f"{tempo_url.rstrip('/')}/api/search"
        response = requests.get(url, params=params, timeout=5.0)
        response.raise_for_status()

        data = response.json()
        traces = data.get('traces', [])

        return {
            'success': True,
            'backend': 'tempo',
            'traces': [
                {
                    'trace_id': t.get('traceID'),
                    'root_trace_name': t.get('rootTraceName'),
                    'duration_ms': t.get('durationMs'),
                    'start_time': t.get('startTimeUnixNano'),
                }
                for t in traces
            ],
            'count': len(traces),
            'backend_url': tempo_url,
            'search_params': params,
        }

    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Failed to connect to trace backend: {str(e)}',
            'backend': 'tempo',
            'backend_url': tempo_url,
            'suggestion': 'Ensure Tempo is running: docker compose -f docker/tempo/docker-compose.tempo.yml up -d'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'backend': 'tempo',
            'backend_url': tempo_url
        }


async def _trace_detail(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get detailed trace information for a specific trace ID (backend-agnostic)."""
    backend = args.get('backend', 'tempo')
    backend_url = args.get('backend_url', 'http://localhost:3200')
    trace_id = args['trace_id']
    include_raw = args.get('include_raw', False)
    max_spans = args.get('max_spans')

    # Currently only Tempo is implemented, but structured for future backends
    if backend == 'tempo':
        return await _get_tempo_trace_detail(backend_url, trace_id, include_raw=include_raw, max_spans=max_spans)
    else:
        return {
            'success': False,
            'error': f'Unsupported trace backend: {backend}',
            'supported_backends': ['tempo']
        }


async def _get_tempo_trace_detail(
    tempo_url: str,
    trace_id: str,
    include_raw: bool = False,
    max_spans: Optional[int] = None
) -> Dict[str, Any]:
    """Get detailed trace from Tempo backend."""
    import requests

    try:
        url = f"{tempo_url.rstrip('/')}/api/traces/{trace_id}"
        response = requests.get(url, timeout=5.0)
        response.raise_for_status()

        trace_data = response.json()

        # Extract spans from the trace
        spans = []
        for batch in trace_data.get('batches', []):
            for scope_spans in batch.get('scopeSpans', []):
                scope_name = ((scope_spans.get('scope') or {}).get('name')) or ''
                for span in scope_spans.get('spans', []):
                    spans.append({
                        'scope': scope_name,
                        'name': span.get('name'),
                        'span_id': span.get('spanId'),
                        'parent_span_id': span.get('parentSpanId'),
                        'start_time': span.get('startTimeUnixNano'),
                        'end_time': span.get('endTimeUnixNano'),
                        'attributes': {
                            attr.get('key'): attr.get('value', {})
                            for attr in span.get('attributes', [])
                        },
                        'status': span.get('status', {}),
                    })

        # Count span types
        app_span_count = sum(1 for s in spans if 'gefion.observability' in s.get('scope', ''))
        db_span_count = sum(1 for s in spans if 'opentelemetry.instrumentation' in s.get('scope', ''))
        error_count = sum(1 for s in spans if s.get('status', {}).get('code') in ['STATUS_CODE_ERROR', 2, '2'])

        # Limit spans if max_spans is specified
        total_spans = len(spans)
        if max_spans is not None and max_spans > 0:
            spans = spans[:max_spans]
            truncated = total_spans - len(spans)
        else:
            truncated = 0

        result = {
            'success': True,
            'backend': 'tempo',
            'trace_id': trace_id,
            'backend_url': tempo_url,
            'trace_api_url': url,
            'total_spans': total_spans,
            'application_spans': app_span_count,
            'database_spans': db_span_count,
            'error_spans': error_count,
            'spans': spans,
        }

        # Only include raw trace if explicitly requested
        if include_raw:
            result['raw_trace'] = trace_data

        # Add truncation info if applicable
        if truncated > 0:
            result['spans_truncated'] = truncated
            result['note'] = f'Showing {len(spans)} of {total_spans} spans (use max_spans parameter to adjust)'

        return result

    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Failed to fetch trace from backend: {str(e)}',
            'backend': 'tempo',
            'trace_id': trace_id,
            'backend_url': tempo_url,
            'suggestion': 'Ensure Tempo is running and the trace ID is valid'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'backend': 'tempo',
            'trace_id': trace_id,
            'backend_url': tempo_url
        }


async def _trace_compare(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two traces to quantify performance differences."""
    backend = args.get('backend', 'tempo')
    backend_url = args.get('backend_url', 'http://localhost:3200')
    trace_id_before = args['trace_id_before']
    trace_id_after = args['trace_id_after']
    focus_spans = args.get('focus_spans', [])

    # Fetch both traces
    if backend == 'tempo':
        before_result = await _get_tempo_trace_detail(backend_url, trace_id_before)
        after_result = await _get_tempo_trace_detail(backend_url, trace_id_after)
    else:
        return {
            'success': False,
            'error': f'Unsupported trace backend: {backend}',
            'supported_backends': ['tempo']
        }

    # Check if both fetches succeeded
    if not before_result.get('success'):
        return {
            'success': False,
            'error': f"Failed to fetch 'before' trace: {before_result.get('error')}",
            'trace_id_before': trace_id_before
        }
    if not after_result.get('success'):
        return {
            'success': False,
            'error': f"Failed to fetch 'after' trace: {after_result.get('error')}",
            'trace_id_after': trace_id_after
        }

    # Calculate total duration from root spans
    before_spans = before_result['spans']
    after_spans = after_result['spans']

    # Find root span (no parent) and calculate duration
    def calculate_trace_duration(spans):
        root_spans = [s for s in spans if not s.get('parent_span_id')]
        if root_spans:
            root = root_spans[0]
            start = int(root['start_time'])
            end = int(root['end_time'])
            return (end - start) / 1_000_000  # Convert nanoseconds to milliseconds
        return 0

    before_duration_ms = calculate_trace_duration(before_spans)
    after_duration_ms = calculate_trace_duration(after_spans)

    # Calculate improvement
    duration_improvement_pct = 0
    if before_duration_ms > 0:
        duration_improvement_pct = ((before_duration_ms - after_duration_ms) / before_duration_ms) * 100

    # Compare span counts
    before_total_spans = len(before_spans)
    after_total_spans = len(after_spans)
    before_app_spans = before_result['application_spans']
    after_app_spans = after_result['application_spans']
    before_db_spans = before_result['database_spans']
    after_db_spans = after_result['database_spans']

    # Analyze specific spans if focus_spans provided
    span_comparisons = []
    if focus_spans:
        for span_name in focus_spans:
            before_matches = [s for s in before_spans if s['name'] == span_name]
            after_matches = [s for s in after_spans if s['name'] == span_name]

            if before_matches and after_matches:
                # Calculate average duration for this span type
                before_avg = sum(
                    (int(s['end_time']) - int(s['start_time'])) / 1_000_000
                    for s in before_matches
                ) / len(before_matches)
                after_avg = sum(
                    (int(s['end_time']) - int(s['start_time'])) / 1_000_000
                    for s in after_matches
                ) / len(after_matches)

                improvement = 0
                if before_avg > 0:
                    improvement = ((before_avg - after_avg) / before_avg) * 100

                span_comparisons.append({
                    'span_name': span_name,
                    'before_avg_ms': round(before_avg, 2),
                    'after_avg_ms': round(after_avg, 2),
                    'improvement_pct': round(improvement, 1),
                    'before_count': len(before_matches),
                    'after_count': len(after_matches)
                })

    return {
        'success': True,
        'backend': backend,
        'trace_id_before': trace_id_before,
        'trace_id_after': trace_id_after,
        'overall': {
            'before_duration_ms': round(before_duration_ms, 2),
            'after_duration_ms': round(after_duration_ms, 2),
            'duration_improvement_pct': round(duration_improvement_pct, 1),
            'faster': after_duration_ms < before_duration_ms
        },
        'span_counts': {
            'before': {
                'total': before_total_spans,
                'application': before_app_spans,
                'database': before_db_spans
            },
            'after': {
                'total': after_total_spans,
                'application': after_app_spans,
                'database': after_db_spans
            },
            'changes': {
                'total': after_total_spans - before_total_spans,
                'application': after_app_spans - before_app_spans,
                'database': after_db_spans - before_db_spans
            }
        },
        'focused_spans': span_comparisons if span_comparisons else None
    }


async def _system_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Comprehensive system status with intelligent suggestions.

    Analyzes:
    - Infrastructure health (PostgreSQL, Tempo, Docker)
    - Data freshness and completeness
    - Missing components (features, models, etc.)

    Returns:
    - Complete status overview
    - Prioritized issues
    - Actionable suggestions with commands
    - Ordered next steps
    """
    from datetime import datetime, date

    status_result = {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "infrastructure": {},
        "data": {},
        "issues": [],
        "suggestions": [],
        "next_steps": []
    }

    # 1. Check Infrastructure Health
    infra_health = {}
    for service in ["docker", "postgres", "tempo"]:
        health_status = health_cache.get_or_check(
            service,
            lambda s=service: check_service_health(s)
        )
        infra_health[service] = health_status

        if not health_status.get("running", True):
            status_result["issues"].append({
                "type": "infrastructure_down",
                "service": service,
                "description": f"{service.upper()} is not running",
                "priority": "critical",
                "command": health_status.get("suggestion", f"Start {service}"),
            })

    status_result["infrastructure"] = infra_health

    # 2. Analyze Data State (if PostgreSQL is up)
    if infra_health.get("postgres", {}).get("running", False):
        # Helper to run a query via _query_database and extract rows
        async def _run_query(sql: str):
            result = await _query_database({"sql": sql})
            if result.get("success") and result.get("rows"):
                return result["rows"]
            return None

        try:
            # Query for data freshness
            rows = await _run_query(
                "SELECT "
                "(SELECT COUNT(*) FROM stocks) as total_stocks, "
                "(SELECT COUNT(*) FROM stock_ohlcv) as ohlcv_rows, "
                "(SELECT MAX(date) FROM stock_ohlcv) as latest_date, "
                "(SELECT COUNT(*) FROM computed_features) as feature_rows, "
                "(SELECT COUNT(DISTINCT feature_id) FROM computed_features) as unique_features"
            )

            if rows:
                row = rows[0]
                stocks = int(row[0]) if row[0] else 0
                ohlcv_rows = int(row[1]) if row[1] else 0
                latest_date_str = row[2] if row[2] else None
                feature_rows = int(row[3]) if row[3] else 0
                unique_features = int(row[4]) if row[4] else 0

                status_result["data"] = {
                    "stocks": stocks,
                    "ohlcv_rows": ohlcv_rows,
                    "latest_date": latest_date_str,
                    "feature_rows": feature_rows,
                    "unique_features": unique_features
                }

                # Analyze data freshness
                if latest_date_str:
                    latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d').date()
                    days_old = (date.today() - latest_date).days
                    status_result["data"]["days_since_update"] = days_old

                    if days_old > 1:
                        status_result["issues"].append({
                            "type": "stale_data",
                            "description": f"Price data is {days_old} days old (last: {latest_date_str})",
                            "priority": "high" if days_old > 7 else "medium",
                            "command": "gefion data-update --exchange NASDAQ --limit 10"
                        })

                # Check for missing data
                if stocks == 0:
                    status_result["issues"].append({
                        "type": "no_data",
                        "description": "No stocks in database",
                        "priority": "critical",
                        "command": "gefion data-update --exchange NASDAQ --limit 10"
                    })
                elif ohlcv_rows == 0:
                    status_result["issues"].append({
                        "type": "no_prices",
                        "description": "No price data ingested",
                        "priority": "high",
                        "command": "gefion data-update --exchange NASDAQ --limit 10"
                    })

                # Check for missing features
                if feature_rows == 0 and ohlcv_rows > 0:
                    status_result["issues"].append({
                        "type": "no_features",
                        "description": "Features not computed (0 rows)",
                        "priority": "medium",
                        "command": "gefion feat-compute --symbols AAPL,MSFT --all-features"
                    })

        except Exception as e:
            status_result["data"]["error"] = f"Failed to query database: {str(e)}"

        # Check for unregistered features/functions
        try:
            # Count feature definitions on disk
            feature_def_dir = Path("feature-definitions")
            feat_def_files_count = 0
            if feature_def_dir.exists():
                feat_def_files_count = len(list(feature_def_dir.glob("*.json")))

            # Count feature definitions in DB
            rows = await _run_query("SELECT COUNT(*) FROM feature_definitions")
            if rows:
                feat_def_db_count = int(rows[0][0]) if rows[0][0] else 0
                status_result["data"]["feature_definitions_on_disk"] = feat_def_files_count
                status_result["data"]["feature_definitions_in_db"] = feat_def_db_count

                if feat_def_files_count > feat_def_db_count:
                    unregistered = feat_def_files_count - feat_def_db_count
                    status_result["issues"].append({
                        "type": "unregistered_feature_definitions",
                        "description": f"{unregistered} feature definition(s) on disk not imported to database",
                        "priority": "medium",
                        "command": "gefion feat-def-import --directory feature-definitions"
                    })

            # Count feature functions on disk
            feature_fx_dir = Path("feature-functions")
            feat_fx_files_count = 0
            if feature_fx_dir.exists():
                feat_fx_files_count = len(list(feature_fx_dir.glob("*.json")))

            # Count feature functions in DB
            rows = await _run_query("SELECT COUNT(*) FROM feature_functions")
            if rows:
                feat_fx_db_count = int(rows[0][0]) if rows[0][0] else 0
                status_result["data"]["feature_functions_on_disk"] = feat_fx_files_count
                status_result["data"]["feature_functions_in_db"] = feat_fx_db_count

                if feat_fx_files_count > feat_fx_db_count:
                    unregistered = feat_fx_files_count - feat_fx_db_count
                    status_result["issues"].append({
                        "type": "unregistered_feature_functions",
                        "description": f"{unregistered} feature function(s) on disk not imported to database",
                        "priority": "medium",
                        "command": "gefion feat-fx-import --directory feature-functions"
                    })

        except Exception as e:
            # Don't fail system_status if feature checking fails
            pass

        # Check for stale/missing fundamentals data (sector, industry)
        try:
            rows = await _run_query(
                "SELECT "
                "(SELECT COUNT(*) FROM stocks WHERE sector IS NULL) as missing_sector, "
                "(SELECT COUNT(*) FROM stocks) as total_stocks, "
                "(SELECT MAX(updated_at) FROM stocks) as latest_updated, "
                "(SELECT COUNT(*) FROM stocks WHERE updated_at IS NOT NULL) as has_fundamentals"
            )

            if rows:
                row = rows[0]
                missing_sector = int(row[0]) if row[0] else 0
                total_stocks = int(row[1]) if row[1] else 0
                latest_updated_str = row[2] if row[2] else None
                has_fundamentals = int(row[3]) if row[3] else 0

                status_result["data"]["stocks_missing_sector"] = missing_sector
                status_result["data"]["stocks_with_fundamentals"] = has_fundamentals

                # Check for missing fundamentals
                if total_stocks > 0 and has_fundamentals == 0:
                    status_result["issues"].append({
                        "type": "missing_fundamentals",
                        "description": f"No stocks have fundamentals data (sector/industry)",
                        "priority": "low",
                        "command": "gefion fundamentals-update"
                    })
                elif missing_sector > 0 and has_fundamentals > 0:
                    status_result["issues"].append({
                        "type": "incomplete_fundamentals",
                        "description": f"{missing_sector} stocks missing sector/industry data",
                        "priority": "low",
                        "command": "gefion fundamentals-update"
                    })

                # Check for stale fundamentals (>30 days old)
                if latest_updated_str:
                    try:
                        latest_updated = datetime.fromisoformat(latest_updated_str.replace(' ', 'T'))
                        days_old = (datetime.now() - latest_updated).days
                        status_result["data"]["fundamentals_days_old"] = days_old

                        if days_old > 30:
                            status_result["issues"].append({
                                "type": "stale_fundamentals",
                                "description": f"Fundamentals data is {days_old} days old",
                                "priority": "low",
                                "command": "gefion fundamentals-update"
                            })
                    except (ValueError, TypeError):
                        pass

        except Exception as e:
            # Don't fail system_status if fundamentals checking fails
            pass

    # 3. Generate Prioritized Suggestions
    # Sort issues by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_issues = sorted(
        status_result["issues"],
        key=lambda x: priority_order.get(x.get("priority", "low"), 99)
    )

    status_result["issues"] = sorted_issues
    status_result["suggestions"] = [
        {
            "description": issue["description"],
            "command": issue["command"],
            "priority": issue["priority"]
        }
        for issue in sorted_issues
    ]

    # 4. Generate Ordered Next Steps
    if not sorted_issues:
        status_result["next_steps"] = [
            "✅ System is healthy and up-to-date",
            "Optional: Run 'gefion ml dataset-build' to create ML datasets",
            "Optional: Train models with 'gefion ml train'"
        ]
        status_result["status"] = "healthy"
    else:
        # Build workflow based on issues
        steps = []
        issue_types = [issue["type"] for issue in sorted_issues]

        if any(t == "infrastructure_down" for t in issue_types):
            steps.append("1. Fix infrastructure: Start required services")

        if any(t in ["no_data", "no_prices", "stale_data"] for t in issue_types):
            steps.append(f"{len(steps)+1}. Update price data: gefion data-update")

        if "no_features" in issue_types:
            steps.append(f"{len(steps)+1}. Compute features: gefion feat-compute")

        if steps:
            steps.append(f"{len(steps)+1}. Build ML dataset: gefion ml dataset-build")
            steps.append(f"{len(steps)+1}. Train model: gefion ml train")

        status_result["next_steps"] = steps
        status_result["status"] = "needs_attention"

    status_result["summary"] = f"{len(sorted_issues)} issue(s) found" if sorted_issues else "All systems operational"

    return status_result


async def _dev_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze development roadmap and suggest next steps."""
    import re

    # Filters from arguments
    path_filter = args.get('path')  # A, B, or C
    status_filter = args.get('status')  # completed, in_progress, planned
    priority_filter = args.get('priority')  # high, medium, low

    dev_status_result = {
        "success": True,
        "current_phase": None,
        "development_rules": {},
        "completed_items": [],
        "in_progress_items": [],
        "planned_items": [],
        "strategic_paths": {},
        "recommended_next_steps": [],
        "quick_wins": [],
    }

    # Read documentation files
    base_path = Path(".")

    # Parse DEVELOPMENT.md
    dev_md_path = base_path / "DEVELOPMENT.md"
    if dev_md_path.exists():
        try:
            dev_content = dev_md_path.read_text()

            # Extract development rules
            dev_status_result["development_rules"] = {
                "tdd_required": "Write tests FIRST" in dev_content,
                "commit_format": "conventional commits",
                "test_minimum": 488 if "488 tests passing" in dev_content else None,
                "no_ai_attribution": "NEVER mention AI tools" in dev_content,
            }
        except Exception as e:
            dev_status_result["development_rules"]["error"] = f"Failed to parse DEVELOPMENT.md: {e}"

    # Parse NEXT_STEPS.md
    next_steps_path = base_path / "NEXT_STEPS.md"
    if next_steps_path.exists():
        try:
            next_steps_content = next_steps_path.read_text()

            # Extract current phase
            phase_match = re.search(r'\*\*Next Up\*\*:\s*(.+)', next_steps_content)
            if phase_match:
                dev_status_result["current_phase"] = phase_match.group(1).strip()
            elif "Strategic Direction" in next_steps_content or "Choose strategic path" in next_steps_content:
                dev_status_result["current_phase"] = "Strategic Direction Choice (Path A/B/C)"

            # Parse items with regex patterns
            # Pattern: ### N. Title\n**Status**: ✅ Complete / In Progress / Planned
            item_pattern = r'###\s+(\d+)\.\s+(.+?)\n.*?\*\*Status\*\*:\s*(.*?)\n.*?\*\*Priority\*\*:\s*(.*?)\n.*?\*\*Effort\*\*:\s*(.*?)(?:\n|$)'

            items = []

            # Find all items (numbered sections)
            for match in re.finditer(r'###\s+(\d+)\.\s+(.+?)(?=\n\n|\n###|$)', next_steps_content, re.DOTALL):
                item_num = match.group(1)
                item_text = match.group(2)

                # Extract title (first line)
                title_match = re.match(r'(.+?)(?:\n|$)', item_text)
                title = title_match.group(1).strip() if title_match else f"Item #{item_num}"

                # Extract status
                status = "planned"
                if "✅ Complete" in item_text or "Status**: ✅" in item_text:
                    status = "completed"
                elif "In Progress" in item_text:
                    status = "in_progress"

                # Extract priority
                priority_match = re.search(r'\*\*Priority\*\*:\s*(\w+)', item_text)
                priority = priority_match.group(1).lower() if priority_match else None

                # Extract effort
                effort_match = re.search(r'\*\*Effort\*\*:\s*(.+?)(?:\n|$)', item_text)
                effort = effort_match.group(1).strip() if effort_match else None

                # Extract path (A/B/C)
                path = None
                if "Path A:" in item_text or "Trading-First" in item_text:
                    path = "A"
                elif "Path B:" in item_text or "ML-First" in item_text:
                    path = "B"
                elif "Path C:" in item_text or "Scale-First" in item_text:
                    path = "C"

                # Extract files to create/modify
                files_to_create = []
                files_match = re.search(r'\*\*Files (?:created|to create)\*\*:(.+?)(?:\n\n|\*\*)', item_text, re.DOTALL)
                if files_match:
                    for line in files_match.group(1).split('\n'):
                        line = line.strip()
                        if line.startswith('- '):
                            files_to_create.append(line[2:].strip())

                item_data = {
                    "number": int(item_num),
                    "title": title,
                    "status": status,
                    "priority": priority,
                    "effort": effort,
                    "path": path,
                    "files_to_create": files_to_create if files_to_create else None,
                }

                # Apply filters
                if status_filter and status != status_filter:
                    continue
                if priority_filter and priority != priority_filter:
                    continue
                if path_filter and path != path_filter:
                    continue

                # Categorize
                if status == "completed":
                    dev_status_result["completed_items"].append(item_data)
                elif status == "in_progress":
                    dev_status_result["in_progress_items"].append(item_data)
                else:
                    dev_status_result["planned_items"].append(item_data)

                # Identify quick wins (high priority, low effort)
                if priority == "high" and effort and ("1 week" in effort or "days" in effort):
                    dev_status_result["quick_wins"].append(item_data)

                items.append(item_data)

            # Parse strategic paths
            paths_section = re.search(r'## Strategic Direction: Three Paths Forward(.+?)(?=\n##|\Z)', next_steps_content, re.DOTALL)
            if paths_section:
                paths_text = paths_section.group(1)

                # Path A
                path_a = re.search(r'## Path A: (.+?)\n\n\*\*Goal\*\*:\s*(.+?)\n.*?\*\*Timeline\*\*:\s*(.+?)\n.*?\*\*Best For\*\*:\s*(.+?)\n', paths_text, re.DOTALL)
                if path_a:
                    dev_status_result["strategic_paths"]["A"] = {
                        "name": path_a.group(1).strip(),
                        "goal": path_a.group(2).strip(),
                        "timeline": path_a.group(3).strip(),
                        "best_for": path_a.group(4).strip(),
                    }

                # Path B
                path_b = re.search(r'## Path B: (.+?)\n\n\*\*Goal\*\*:\s*(.+?)\n.*?\*\*Timeline\*\*:\s*(.+?)\n.*?\*\*Best For\*\*:\s*(.+?)\n', paths_text, re.DOTALL)
                if path_b:
                    dev_status_result["strategic_paths"]["B"] = {
                        "name": path_b.group(1).strip(),
                        "goal": path_b.group(2).strip(),
                        "timeline": path_b.group(3).strip(),
                        "best_for": path_b.group(4).strip(),
                    }

                # Path C
                path_c = re.search(r'## Path C: (.+?)\n\n\*\*Goal\*\*:\s*(.+?)\n.*?\*\*Timeline\*\*:\s*(.+?)\n.*?\*\*Best For\*\*:\s*(.+?)\n', paths_text, re.DOTALL)
                if path_c:
                    dev_status_result["strategic_paths"]["C"] = {
                        "name": path_c.group(1).strip(),
                        "goal": path_c.group(2).strip(),
                        "timeline": path_c.group(3).strip(),
                        "best_for": path_c.group(4).strip(),
                    }

            # Recommend next steps (planned items with no dependencies)
            for item in dev_status_result["planned_items"][:5]:  # Top 5
                recommendation = {
                    "item": f"#{item['number']}",
                    "title": item["title"],
                    "priority": item["priority"],
                    "effort": item["effort"],
                    "path": item["path"],
                }
                dev_status_result["recommended_next_steps"].append(recommendation)

        except Exception as e:
            dev_status_result["error"] = f"Failed to parse NEXT_STEPS.md: {e}"
    else:
        dev_status_result["error"] = "NEXT_STEPS.md not found"

    # Parse PROGRESS.md for recent changes
    progress_path = base_path / "PROGRESS.md"
    if progress_path.exists():
        try:
            progress_content = progress_path.read_text()

            # Extract recent changes (latest heading with date)
            recent_match = re.search(r'###\s+(.+?202\d.*?)\n\n(.+?)(?=\n###|\Z)', progress_content, re.DOTALL)
            if recent_match:
                dev_status_result["recent_changes"] = {
                    "date": recent_match.group(1).strip(),
                    "summary": recent_match.group(2).strip()[:500] + "..." if len(recent_match.group(2)) > 500 else recent_match.group(2).strip(),
                }
        except Exception as e:
            dev_status_result["progress_error"] = f"Failed to parse PROGRESS.md: {e}"

    return dev_status_result


async def _health_check(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check health of infrastructure services."""
    service = args.get('service')

    if service:
        # Check specific service
        cmd = ['health', '--service', service]
    else:
        # Check all services
        cmd = ['health']

    return await executor.run(*cmd)


async def _docker_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check docker compose services status."""
    try:
        result = subprocess.run(
            ['docker', 'compose', 'ps', '--format', 'json'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {
                'success': False,
                'error': result.stderr or result.stdout,
                'suggestion': 'Check if Docker is running:\n  docker --version\n\nOr start services:\n  docker compose up -d'
            }

        # Parse JSON output
        try:
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    container = json.loads(line)
                    containers.append({
                        'name': container.get('Name'),
                        'service': container.get('Service'),
                        'state': container.get('State'),
                        'status': container.get('Status'),
                        'health': container.get('Health', 'N/A'),
                        'ports': container.get('Publishers', [])
                    })

            return {
                'success': True,
                'containers': containers,
                'count': len(containers)
            }
        except json.JSONDecodeError:
            # Fallback to plain text output
            result_plain = subprocess.run(
                ['docker', 'compose', 'ps'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return {
                'success': True,
                'output': result_plain.stdout,
                'note': 'Plain text output (JSON parsing failed)'
            }

    except FileNotFoundError:
        return {
            'success': False,
            'error': 'Docker not found',
            'suggestion': 'Install Docker:\n  https://docs.docker.com/get-docker/'
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Docker command timed out',
            'suggestion': 'Docker may be unresponsive. Check Docker Desktop or daemon.'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


# ============================================================================
# Strategy Management Tools
# ============================================================================

async def _strategy_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all registered trading strategies."""
    async def _list():
        return await executor.run('strategy', 'list')

    return await _execute_with_health_check(['postgres'], _list)


async def _strategy_configs(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all active strategy configurations."""
    async def _list():
        return await executor.run('strategy', 'configs')

    return await _execute_with_health_check(['postgres'], _list)


async def _strategy_create_config(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new strategy configuration."""
    async def _create():
        cmd = [
            'strategy', 'create-config',
            '--name', args['name'],
            '--strategy', args['strategy'],
        ]

        if args.get('params'):
            cmd.extend(['--params', json.dumps(args['params'])])
        if args.get('description'):
            cmd.extend(['--description', args['description']])

        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _create)


# ============================================================================
# Volatility Tools
# ============================================================================

async def _volatility_compute(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compute volatility thresholds for stocks."""
    async def _compute():
        cmd = ['volatility', 'compute', '--symbols', args['symbols']]

        if args.get('horizons'):
            cmd.extend(['--horizons', args['horizons']])
        if args.get('date'):
            cmd.extend(['--date', args['date']])

        cmd.append('--json')
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _compute)


# ============================================================================
# Backtesting Tools
# ============================================================================

async def _backtest_run(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run backtest for a trading strategy.

    Supports advanced features:
    - Transaction costs (commission, spread, market impact)
    - Slippage modeling
    - Risk management (stop loss, take profit)
    - Position sizing methods
    """
    async def _run():
        cmd = ['backtest', 'run']

        # Required arguments
        if args.get('strategy'):
            cmd.extend(['--strategy', args['strategy']])
        if args.get('start_date'):
            cmd.extend(['--start-date', args['start_date']])
        if args.get('end_date'):
            cmd.extend(['--end-date', args['end_date']])

        # Short-side execution (spec 009)
        if args.get('mode'):
            cmd.extend(['--mode', args['mode']])
        if args.get('borrow_rate') is not None:
            cmd.extend(['--borrow-rate', str(args['borrow_rate'])])
        if args.get('max_short_exposure') is not None:
            cmd.extend(['--max-short-exposure', str(args['max_short_exposure'])])

        # Symbol selection
        if args.get('symbols'):
            cmd.extend(['--symbols', args['symbols']])
        elif args.get('exchange'):
            cmd.extend(['--exchange', args['exchange']])
            if args.get('limit'):
                cmd.extend(['--limit', str(args['limit'])])

        # Portfolio settings
        if args.get('initial_cash'):
            cmd.extend(['--initial-cash', str(args['initial_cash'])])

        # ML strategy parameters
        if args.get('model_name'):
            cmd.extend(['--model-name', args['model_name']])
        if args.get('model_version'):
            cmd.extend(['--model-version', args['model_version']])
        if args.get('horizon'):
            cmd.extend(['--horizon', str(args['horizon'])])

        # Advanced features (CLI flags to be added when CLI is updated)
        # For now, we note the requested features in the output
        requested_features = {}
        if args.get('cost_preset'):
            requested_features['costs'] = args['cost_preset']
        if args.get('slippage_preset'):
            requested_features['slippage'] = args['slippage_preset']
        if args.get('risk_preset'):
            requested_features['risk'] = args['risk_preset']
        if args.get('sizing_method'):
            requested_features['sizing'] = {
                'method': args['sizing_method'],
                'amount': args.get('sizing_amount')
            }

        # Regime slicing (spec 005) — additive per-regime metrics
        if args.get('by_regime'):
            cmd.extend(['--by-regime', args['by_regime']])

        result = await executor.run(*cmd)

        # Add feature info to result
        if requested_features:
            result['note'] = (
                "Advanced features (costs, slippage, risk, sizing) are implemented in BacktestEngine. "
                "CLI integration pending. Use Python API directly for full feature access."
            )
            result['requested_features'] = requested_features

        return result

    return await _execute_with_health_check(['postgres'], _run)


async def _backtest_compare(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compare multiple trading strategies on the same data."""
    async def _compare():
        cmd = ['backtest', 'compare']

        # Strategy selection
        if args.get('strategies'):
            cmd.extend(['--strategies', args['strategies']])
        if args.get('all_strategies'):
            cmd.append('--all')

        # Date range
        if args.get('start_date'):
            cmd.extend(['--start-date', args['start_date']])
        if args.get('end_date'):
            cmd.extend(['--end-date', args['end_date']])

        # Symbol selection
        if args.get('symbols'):
            cmd.extend(['--symbols', args['symbols']])
        elif args.get('exchange'):
            cmd.extend(['--exchange', args['exchange']])
            if args.get('limit'):
                cmd.extend(['--limit', str(args['limit'])])

        # Portfolio settings
        if args.get('initial_cash'):
            cmd.extend(['--initial-cash', str(args['initial_cash'])])

        # Ranking
        if args.get('rank_by'):
            cmd.extend(['--rank-by', args['rank_by']])

        # ML strategy parameters
        if args.get('model_name'):
            cmd.extend(['--model-name', args['model_name']])
        if args.get('model_version'):
            cmd.extend(['--model-version', args['model_version']])

        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _compare)


# ============================================================================
# Experiment Tools
# ============================================================================

async def _experiment_propose(args: Dict[str, Any]) -> Dict[str, Any]:
    """Propose a new experiment for approval. Supports all experiment types."""
    async def _propose():
        cmd = ["experiment", "propose"]

        # Required arguments
        cmd.extend(["--name", args["name"]])
        cmd.extend(["--search-space", args["search_space"]])

        # Experiment type
        if args.get("experiment_type"):
            cmd.extend(["--type", args["experiment_type"]])

        # ML-specific options
        if args.get("model_type"):
            cmd.extend(["--model-type", args["model_type"]])
        if args.get("dataset_uri"):
            cmd.extend(["--dataset-uri", args["dataset_uri"]])
        if args.get("horizon_days"):
            cmd.extend(["--horizon-days", str(args["horizon_days"])])
        if args.get("objective_direction"):
            cmd.extend(["--objective-direction", args["objective_direction"]])

        # Strategy options
        if args.get("strategy"):
            cmd.extend(["--strategy", args["strategy"]])
        if args.get("symbols"):
            cmd.extend(["--symbols", args["symbols"]])
        if args.get("start_date"):
            cmd.extend(["--start-date", args["start_date"]])
        if args.get("end_date"):
            cmd.extend(["--end-date", args["end_date"]])

        # General options
        if args.get("objective"):
            cmd.extend(["--objective", args["objective"]])
        if args.get("max_trials"):
            cmd.extend(["--max-trials", str(args["max_trials"])])
        if args.get("search_method"):
            cmd.extend(["--search-method", args["search_method"]])
        if args.get("extra_config"):
            cmd.extend(["--config", args["extra_config"]])

        # Goal options
        if args.get("goal_type"):
            cmd.extend(["--goal-type", args["goal_type"]])
        if args.get("goal_target") is not None:
            cmd.extend(["--goal-target", str(args["goal_target"])])
        if args.get("baseline") is not None:
            cmd.extend(["--baseline", str(args["baseline"])])
        if args.get("early_stop"):
            cmd.append("--early-stop")

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _propose)


async def _experiment_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List experiments with optional filters."""
    async def _list():
        cmd = ["experiment", "list"]

        if args.get("status"):
            cmd.extend(["--status", args["status"]])
        if args.get("experiment_type"):
            cmd.extend(["--type", args["experiment_type"]])
        if args.get("limit"):
            cmd.extend(["--limit", str(args["limit"])])

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _list)


async def _experiment_approve(args: Dict[str, Any]) -> Dict[str, Any]:
    """Approve an experiment for execution."""
    async def _approve():
        cmd = ["experiment", "approve", "--id", str(args["experiment_id"])]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _approve)


async def _experiment_run(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run an approved experiment."""
    async def _run():
        cmd = ["experiment", "run", "--id", str(args["experiment_id"])]
        if args.get("by_regime"):
            cmd.extend(["--by-regime", args["by_regime"]])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _run)


async def _experiment_results(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get results for a completed experiment."""
    async def _results():
        cmd = ["experiment", "results", "--id", str(args["experiment_id"])]

        if args.get("show_trials"):
            cmd.append("--show-trials")

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _results)


async def _experiment_chain(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a child experiment chained to a parent."""
    async def _chain():
        cmd = [
            "gefion", "experiment", "chain",
            "--parent-id", str(args["parent_id"]),
            "--name", args["name"],
            "--search-space", args["search_space"],
        ]

        if args.get("depends_on"):
            cmd.extend(["--depends-on", args["depends_on"]])
        if args.get("strategy"):
            cmd.extend(["--strategy", args["strategy"]])
        if args.get("symbols"):
            cmd.extend(["--symbols", args["symbols"]])
        if args.get("start_date"):
            cmd.extend(["--start-date", args["start_date"]])
        if args.get("end_date"):
            cmd.extend(["--end-date", args["end_date"]])
        if args.get("max_trials"):
            cmd.extend(["--max-trials", str(args["max_trials"])])
        if args.get("search_method"):
            cmd.extend(["--search-method", args["search_method"]])

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _chain)


async def _experiment_children(args: Dict[str, Any]) -> Dict[str, Any]:
    """List child experiments of a parent."""
    async def _children():
        cmd = ["experiment", "children", "--parent-id", str(args["parent_id"])]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _children)


async def _experiment_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get detailed status of an experiment."""
    async def _status():
        cmd = ["experiment", "status", "--id", str(args["experiment_id"])]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _status)


# ============================================================================
# Autonomous Experiment Framework Tools
# ============================================================================

async def _experiment_discover(args: Dict[str, Any]) -> Dict[str, Any]:
    """Discover available data sources and experiment opportunities."""
    async def _discover():
        cmd = ["experiment", "discover", "--json"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _discover)


async def _experiment_cycle_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Start a new experiment cycle with holdout and FDR configuration."""
    async def _start():
        cmd = ["experiment", "cycle-start", "--json"]

        if args.get("name"):
            cmd.extend(["--name", args["name"]])
        if args.get("fdr_rate") is not None:
            cmd.extend(["--fdr-rate", str(args["fdr_rate"])])
        if args.get("holdout_weeks") is not None:
            cmd.extend(["--holdout-weeks", str(args["holdout_weeks"])])
        if args.get("max_experiments") is not None:
            cmd.extend(["--max-experiments", str(args["max_experiments"])])

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _start)


async def _experiment_cycle_run(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run an autonomous experiment cycle."""
    async def _run():
        cmd = ["experiment", "cycle-run", str(args["cycle_id"])]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _run)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS_DIR = _REPO_ROOT / "docs"


def _resolve_doc(name: str) -> Optional[Path]:
    """Resolve a doc name to a real file, refusing anything outside docs/.

    Accepts bare names of markdown files in docs/ plus README.md at the
    repo root. Path separators and traversal are rejected outright.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if name == "README.md":
        path = _REPO_ROOT / "README.md"
        return path if path.exists() else None
    path = (_DOCS_DIR / name).resolve()
    try:
        path.relative_to(_DOCS_DIR.resolve())
    except ValueError:
        return None
    return path if (path.exists() and path.suffix == ".md") else None


def _doc_summary(path: Path) -> str:
    """First heading or non-empty line of a doc."""
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                return line.lstrip("# ").strip()[:120]
    except OSError:
        pass
    return ""


async def _docs_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List available documentation files with one-line summaries."""
    docs = []
    candidates = [_REPO_ROOT / "README.md"] + sorted(_DOCS_DIR.glob("*.md"))
    for path in candidates:
        if path.exists():
            docs.append({"name": path.name, "summary": _doc_summary(path)})
    return {"docs": docs, "count": len(docs)}


async def _docs_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read one documentation file by name."""
    path = _resolve_doc(str(args.get("name", "")))
    if path is None:
        return {"error": f"Unknown doc: {args.get('name')!r}. Use docs_list for names."}
    return {"name": path.name, "content": path.read_text()}


async def _docs_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Case-insensitive search across documentation, with line context."""
    query = str(args.get("query", "")).strip().lower()
    if not query:
        return {"error": "query is required"}
    max_hits = int(args.get("max_results", 20))
    hits = []
    candidates = [_REPO_ROOT / "README.md"] + sorted(_DOCS_DIR.glob("*.md"))
    for path in candidates:
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if query in line.lower():
                start, end = max(0, i - 1), min(len(lines), i + 2)
                hits.append({"doc": path.name, "line": i + 1,
                             "context": "\n".join(lines[start:end])})
                if len(hits) >= max_hits:
                    return {"hits": hits, "truncated": True}
    return {"hits": hits, "truncated": False}


async def _experiment_apply(args: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a promoted experiment winner to production."""
    async def _apply():
        cmd = ["experiment", "apply", "--id", str(args["experiment_id"]), "--json"]
        if args.get("backtest_days"):
            cmd.extend(["--backtest-days", str(args["backtest_days"])])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _apply)


async def _experiment_cycle_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List experiment cycles."""
    async def _list():
        cmd = ["experiment", "cycle-list", "--json"]
        if args.get("limit"):
            cmd.extend(["--limit", str(args["limit"])])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _list)


async def _experiment_probation_check(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check promoted experiments on probation; auto-demote degraded ones."""
    async def _check():
        cmd = ["experiment", "probation-check", "--json"]
        if args.get("tolerance") is not None:
            cmd.extend(["--tolerance", str(args["tolerance"])])
        if args.get("min_samples") is not None:
            cmd.extend(["--min-samples", str(args["min_samples"])])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _check)


async def _experiment_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Experiment deletion — dry-run default, refusals surface verbatim."""
    async def _run():
        cmd = ["experiment", "delete", "--id", str(args["experiment_id"])]
        if args.get("confirm"):
            cmd.append("--confirm")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _experiment_demote(args: Dict[str, Any]) -> Dict[str, Any]:
    """Manually demote a promoted experiment artifact."""
    async def _demote():
        cmd = ["experiment", "demote",
               "--id", str(args["experiment_id"]),
               "--reason", str(args["reason"]),
               "--json"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _demote)


async def _chart_experiment_trials(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate trial scatter (and heatmap when applicable) for an experiment."""
    async def _generate():
        cmd = ["chart", "experiment-trials", str(args["experiment_id"]),
               "--no-open", "--json"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_experiment_fdr(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate FDR cycle summary chart."""
    async def _generate():
        cmd = ["chart", "experiment-fdr", str(args["cycle_id"]),
               "--no-open", "--json"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _experiment_cycle_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get status of an experiment cycle."""
    async def _status():
        cmd = ["experiment", "cycle-status", str(args["cycle_id"]), "--json"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _status)


async def _principles_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List principles from the quantitative finance catalog."""
    async def _list():
        cmd = ["principles", "list", "--json"]

        if args.get("domain"):
            cmd.extend(["--domain", args["domain"]])
        if args.get("experiment_type"):
            cmd.extend(["--experiment-type", args["experiment_type"]])
        if args.get("status"):
            cmd.extend(["--status", args["status"]])

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _list)


async def _principles_suggest(args: Dict[str, Any]) -> Dict[str, Any]:
    """Suggest experiments based on principles and current data."""
    async def _suggest():
        cmd = ["principles", "suggest", "--json"]

        if args.get("experiment_type"):
            cmd.extend(["--experiment-type", args["experiment_type"]])

        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _suggest)


# ============================================================================
# Chart Tools
# ============================================================================

async def _chart_price(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate candlestick price chart with rich context."""
    async def _generate():
        cmd = ["chart", "price", args["symbol"], "--no-open"]
        if args.get("start_date"):
            cmd.extend(["--start-date", args["start_date"]])
        if args.get("end_date"):
            cmd.extend(["--end-date", args["end_date"]])
        if args.get("indicators"):
            cmd.extend(["--indicators", args["indicators"]])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_predictions(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate prediction chart with rich context."""
    async def _generate():
        cmd = ["chart", "predictions", args["symbol"],
               "--model", args["model"], "--no-open"]
        if args.get("horizon"):
            cmd.extend(["--horizon", str(args["horizon"])])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_features(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate feature overlay chart with rich context."""
    async def _generate():
        cmd = ["chart", "features", args["symbol"],
               "--features", args["features"], "--no-open"]
        if args.get("start_date"):
            cmd.extend(["--start-date", args["start_date"]])
        if args.get("end_date"):
            cmd.extend(["--end-date", args["end_date"]])
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_calibration(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate model calibration chart."""
    async def _generate():
        cmd = ["chart", "calibration", args["model_name"], "--no-open"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_confusion_matrix(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate confusion matrix chart."""
    async def _generate():
        cmd = ["chart", "confusion-matrix", args["model_name"], "--no-open"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_pipeline_health(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate pipeline health dashboard."""
    async def _generate():
        cmd = ["chart", "pipeline-health", "--no-open"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


async def _chart_pred_vs_actual(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate predictions vs actual scatter plot."""
    async def _generate():
        cmd = ["chart", "pred-vs-actual", args["model_name"], "--no-open"]
        executor = GefionExecutor()
        return await executor.run(*cmd)

    return await _execute_with_health_check(['postgres'], _generate)


# ============================================================================
# RBAC Tools
# ============================================================================

async def _get_role_info(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return current role and behavioral guidelines."""
    role_info = ROLE_INFO.get(MCP_ROLE, ROLE_INFO['operator'])
    return {
        "success": True,
        "role": MCP_ROLE,
        "description": role_info['description'],
        "guidelines": role_info['guidelines'],
        "blocked_tools": list(OPERATOR_BLOCKED_TOOLS) if MCP_ROLE == 'operator' else [],
    }


# ============================================================================
# Regime slicing tools (spec 005) — wrap the `gefion regime` CLI
# ============================================================================

async def _regime_define(args: Dict[str, Any]) -> Dict[str, Any]:
    """Define and store a regime."""
    async def _run():
        cmd = ["regime", "define", "--name", args["name"], "--scope", args["scope"],
               "--expression", args["expression"], "--bucketing", args["bucketing"]]
        if args.get("min_dwell"):
            cmd.extend(["--min-dwell", str(args["min_dwell"])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List regime definitions."""
    async def _run():
        cmd = ["regime", "list"]
        if args.get("scope"):
            cmd.extend(["--scope", args["scope"]])
        if args.get("status"):
            cmd.extend(["--status", args["status"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_show(args: Dict[str, Any]) -> Dict[str, Any]:
    """Show a regime definition."""
    async def _run():
        return await GefionExecutor().run("regime", "show", args["name"])
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_compute(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compute causal labels for a regime."""
    async def _run():
        cmd = ["regime", "compute", args["name"]]
        if args.get("dataset"):
            cmd.extend(["--dataset", args["dataset"]])
        if args.get("window"):
            cmd.extend(["--window", str(args["window"])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_labels(args: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize computed regime labels."""
    async def _run():
        return await GefionExecutor().run("regime", "labels", args["name"])
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_archive(args: Dict[str, Any]) -> Dict[str, Any]:
    """Archive a regime definition."""
    async def _run():
        return await GefionExecutor().run("regime", "archive", args["name"])
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_definitions_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """Export regime definitions to JSON files."""
    async def _run():
        return await GefionExecutor().run("regime", "export", args["directory"])
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_definitions_import(args: Dict[str, Any]) -> Dict[str, Any]:
    """Import regime definitions from JSON files."""
    async def _run():
        return await GefionExecutor().run("regime", "import", args["directory"])
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_interaction(args: Dict[str, Any]) -> Dict[str, Any]:
    """Continuous-interaction test: does a signal's edge vary with a conditioning variable."""
    async def _run():
        cmd = ["regime", "interaction", "--signal", args["signal"], "--by", args["by"]]
        if args.get("horizon_days"):
            cmd.extend(["--horizon-days", str(args["horizon_days"])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _chart_regime(args: Dict[str, Any]) -> Dict[str, Any]:
    """Chart a symbol's price with regime-episode bands overlaid."""
    async def _run():
        cmd = ["chart", "regime", args["name"], "--symbol", args["symbol"], "--no-open"]
        if args.get("start_date"):
            cmd.extend(["--start-date", args["start_date"]])
        if args.get("end_date"):
            cmd.extend(["--end-date", args["end_date"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Pre-register and run an agentic regime-discovery run (mutating, long)."""
    async def _run():
        cmd = ["regime", "discover", "start",
               "--name", args["name"], "--atoms", args["atoms"]]
        if args.get("depth") is not None:
            cmd.extend(["--depth", str(args["depth"])])
        if args.get("budget") is not None:
            cmd.extend(["--budget", str(args["budget"])])
        for tier in args.get("tiers") or []:
            cmd.extend(["--tier", tier])
        if args.get("signal_source"):
            cmd.extend(["--signal-source", args["signal_source"]])
        if args.get("grading_scheme"):
            cmd.extend(["--grading-scheme", args["grading_scheme"]])
        if args.get("universe_filter"):
            cmd.extend(["--universe-filter", args["universe_filter"]])
        if args.get("fresh_holdout"):
            cmd.extend(["--fresh-holdout", args["fresh_holdout"]])
        if args.get("freeform"):
            cmd.extend(["--freeform", args["freeform"]])
        if args.get("principles"):
            cmd.extend(["--principles", args["principles"]])
        if args.get("reserve_justification"):
            cmd.extend(["--reserve-justification", args["reserve_justification"]])
        if args.get("min_effective_n") is not None:
            cmd.extend(["--min-effective-n", str(args["min_effective_n"])])
        if args.get("max_date"):
            cmd.extend(["--max-date", args["max_date"]])
        if args.get("seed") is not None:
            cmd.extend(["--seed", str(args["seed"])])
        if args.get("dataset"):
            cmd.extend(["--dataset", args["dataset"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List regime-discovery runs."""
    async def _run():
        cmd = ["regime", "discover", "list"]
        if args.get("status"):
            cmd.extend(["--status", args["status"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_show(args: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect a discovery run's pre-registration, segregation, and status."""
    async def _run():
        return await GefionExecutor().run("regime", "discover", "show", args["run"])
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_ledger(args: Dict[str, Any]) -> Dict[str, Any]:
    """Candidate ledger of a discovery run (losers included)."""
    async def _run():
        cmd = ["regime", "discover", "ledger", args["run"]]
        if args.get("verdict"):
            cmd.extend(["--verdict", args["verdict"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_verdicts(args: Dict[str, Any]) -> Dict[str, Any]:
    """FDR survivors of a discovery run, with the family size beside them."""
    async def _run():
        return await GefionExecutor().run("regime", "discover", "verdicts", args["run"])
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_derive(args: Dict[str, Any]) -> Dict[str, Any]:
    """Derived macro series (breadth/dispersion) — idempotent recompute."""
    async def _run():
        cmd = ["macro", "derive"]
        if args.get("series"):
            cmd.extend(["--series", args["series"]])
        if args.get("min_stocks"):
            cmd.extend(["--min-stocks", str(args["min_stocks"])])
        if args.get("full"):
            cmd.append("--full")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _observe(args: Dict[str, Any]) -> Dict[str, Any]:
    """Record a system observation (advisory ledger, #144)."""
    async def _run():
        cmd = ["observe", args["observation"], "--category", args["category"]]
        if args.get("observer"):
            cmd.extend(["--observer", args["observer"]])
        if args.get("suggested_action"):
            cmd.extend(["--suggested-action", args["suggested_action"]])
        if args.get("evidence"):
            cmd.extend(["--evidence", args["evidence"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _observations_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """The open-observations queue — read-only."""
    async def _run():
        cmd = ["observations", "list"]
        if args.get("state"):
            cmd.extend(["--state", args["state"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _observations_review(args: Dict[str, Any]) -> Dict[str, Any]:
    """Human-directed review of one observation."""
    async def _run():
        verb = {"acknowledged": "ack", "adopted": "adopt",
                "rejected": "reject"}.get(args["state"])
        if verb is None:
            raise ValueError(f"unknown state {args['state']!r}")
        cmd = ["observations", verb, "--id", str(args["observation_id"])]
        if args.get("reviewer"):
            cmd.extend(["--reviewer", args["reviewer"]])
        if args.get("reason"):
            cmd.extend(["--reason", args["reason"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """All universe definitions — read-only."""
    async def _run():
        return await GefionExecutor().run("universe", "list")
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_show(args: Dict[str, Any]) -> Dict[str, Any]:
    """One universe in full — read-only."""
    async def _run():
        return await GefionExecutor().run("universe", "show", args["name"])
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_members(args: Dict[str, Any]) -> Dict[str, Any]:
    """Member symbols as of a date — read-only."""
    async def _run():
        cmd = ["universe", "members"]
        if args.get("name"):
            cmd.append(args["name"])
        if args.get("as_of"):
            cmd.extend(["--as-of", args["as_of"]])
        if args.get("limit"):
            cmd.extend(["--limit", str(args["limit"])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_explain(args: Dict[str, Any]) -> Dict[str, Any]:
    """Why is/isn't a symbol in the universe — read-only."""
    async def _run():
        cmd = ["universe", "explain", args["symbol"]]
        if args.get("universe"):
            cmd.extend(["--universe", args["universe"]])
        if args.get("as_of"):
            cmd.extend(["--as-of", args["as_of"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_refresh(args: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile membership intervals — mutating, guarded."""
    async def _run():
        cmd = ["universe", "refresh"]
        if args.get("name"):
            cmd.append(args["name"])
        if args.get("force"):
            cmd.append("--force")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_define(args: Dict[str, Any]) -> Dict[str, Any]:
    """Owner-gated definition create/update — mutating."""
    import tempfile

    async def _run():
        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                         delete=False) as f:
            f.write(args["rules_yaml"])
            rules_path = f.name
        try:
            cmd = ["universe", "define", args["name"],
                   "--rules-file", rules_path]
            if args.get("description"):
                cmd.extend(["--description", args["description"]])
            if args.get("default"):
                cmd.append("--default")
            return await GefionExecutor().run(*cmd)
        finally:
            os.unlink(rules_path)
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Deletion door — dry-run unless confirm."""
    async def _run():
        cmd = ["universe", "delete", args["name"]]
        if args.get("confirm"):
            cmd.append("--confirm")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """YAML export — read-only."""
    async def _run():
        return await GefionExecutor().run("universe", "export")
    return await _execute_with_health_check(['postgres'], _run)


async def _universe_import(args: Dict[str, Any]) -> Dict[str, Any]:
    """Owner-gated YAML import — validates before writing."""
    import tempfile

    async def _run():
        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                         delete=False) as f:
            f.write(args["yaml_text"])
            yaml_path = f.name
        try:
            cmd = ["universe", "import", yaml_path]
            if args.get("dry_run"):
                cmd.append("--dry-run")
            return await GefionExecutor().run(*cmd)
        finally:
            os.unlink(yaml_path)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_candidate_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """Candidate queue (spec 014) — read-only."""
    async def _run():
        cmd = ["macro", "candidate", "list"]
        if args.get("state"):
            cmd.extend(["--state", args["state"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_candidate_show(args: Dict[str, Any]) -> Dict[str, Any]:
    """Review packet for one candidate — read-only (optional dry-run rerun)."""
    async def _run():
        cmd = ["macro", "candidate", "show", "--id", str(args["candidate_id"])]
        if args.get("rerun_dry_run"):
            cmd.append("--rerun-dry-run")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_candidate_approve(args: Dict[str, Any]) -> Dict[str, Any]:
    """Human-directed approval — promotes into the production roster."""
    async def _run():
        cmd = ["macro", "candidate", "approve", "--id", str(args["candidate_id"])]
        if args.get("approver"):
            cmd.extend(["--approver", args["approver"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_candidate_reject(args: Dict[str, Any]) -> Dict[str, Any]:
    """Human-directed terminal rejection (reason required, audit retained)."""
    async def _run():
        cmd = ["macro", "candidate", "reject", "--id", str(args["candidate_id"]),
               "--reason", args["reason"]]
        if args.get("approver"):
            cmd.extend(["--approver", args["approver"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_register_composite(args: Dict[str, Any]) -> Dict[str, Any]:
    """Owner-authored composite registration — refusals surface verbatim."""
    async def _run():
        cmd = ["macro", "register-composite", "--name", args["name"],
               "--series", args["series"], "--body-file", args["body_file"]]
        if args.get("description"):
            cmd.extend(["--description", args["description"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_propose(args: Dict[str, Any]) -> Dict[str, Any]:
    """Explicit candidate generation — queues for review, never executes."""
    async def _run():
        cmd = ["macro", "propose", "--principle", args["principle"]]
        if args.get("design"):
            cmd.extend(["--design", args["design"]])
        if args.get("kind"):
            cmd.extend(["--kind", args["kind"]])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a regime definition (dry-run unless confirm=true)."""
    async def _run():
        cmd = ["regime", "delete", args["name"]]
        if args.get("confirm"):
            cmd.append("--confirm")
        if args.get("force"):
            cmd.append("--force")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a discovery run (dry-run unless confirm=true; admitted runs refuse)."""
    async def _run():
        cmd = ["regime", "discover", "delete", args["run"]]
        if args.get("confirm"):
            cmd.append("--confirm")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_spa(args: Dict[str, Any]) -> Dict[str, Any]:
    """Selection-aware SPA re-verdict of a discovery run (append-only record)."""
    async def _run():
        cmd = ["regime", "discover", "spa", args["run"]]
        if args.get("iterations"):
            cmd.extend(["--iterations", str(args["iterations"])])
        if args.get("seed") is not None:
            cmd.extend(["--seed", str(args["seed"])])
        if args.get("level") is not None:
            cmd.extend(["--level", str(args["level"])])
        if args.get("block_length") is not None:
            cmd.extend(["--block-length", str(args["block_length"])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_diagnostics(args: Dict[str, Any]) -> Dict[str, Any]:
    """Diagnostics ledger of a discovery run (sample-dependent vs structural)."""
    async def _run():
        cmd = ["regime", "discover", "diagnostics", args["run"]]
        if args.get("kind") == "sample_dependent":
            cmd.append("--sample-dependent")
        elif args.get("kind") == "structural":
            cmd.append("--structural")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_grades(args: Dict[str, Any]) -> Dict[str, Any]:
    """Trust grades: forward folds; descriptive rows flagged."""
    async def _run():
        cmd = ["regime", "discover", "grades"]
        if args.get("candidate"):
            cmd.append(str(args["candidate"]))
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_register(args: Dict[str, Any]) -> Dict[str, Any]:
    """Re-declare an admitted edge's grading grid (mutating; locked once evidence exists)."""
    async def _run():
        return await GefionExecutor().run(
            "regime", "discover", "register", str(args["candidate"]),
            "--fold-length-days", str(args["fold_length_days"]))
    return await _execute_with_health_check(['postgres'], _run)


async def _regime_discover_grade_fold(args: Dict[str, Any]) -> Dict[str, Any]:
    """Re-test an admitted edge on a forward fold (mutating)."""
    async def _run():
        return await GefionExecutor().run(
            "regime", "discover", "grade-fold", str(args["candidate"]),
            "--fold", str(args["fold"]))
    return await _execute_with_health_check(['postgres'], _run)


async def _entity_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Registry-driven entity deletion (dry-run unless confirm=true)."""
    async def _run():
        cmd = ["data", "entity-delete", args["entity_table"], str(args["key"])]
        if args.get("confirm"):
            cmd.append("--confirm")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_ingest(args: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a macro series + materialize its feature (mutating)."""
    async def _run():
        if args.get("refresh_all"):
            return await GefionExecutor().run("macro", "ingest", "--all")
        cmd = ["macro", "ingest", "--name", args["name"]]
        for opt in ("provider", "kind", "cadence"):
            if args.get(opt):
                cmd.extend([f"--{opt}", str(args[opt])])
        if args.get("full"):
            cmd.append("--full")
        if args.get("include_flagged"):
            cmd.append("--include-flagged")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _macro_seed_sectors(args: Dict[str, Any]) -> Dict[str, Any]:
    """Seed sector-signal market bodies (spec 013, mutating)."""
    cmd = ["macro", "seed-sectors"]
    if args.get("sectors"):
        cmd.extend(["--sectors", str(args["sectors"])])
    if args.get("min_members") is not None:
        cmd.extend(["--min-members", str(args["min_members"])])
    if args.get("body_floor") is not None:
        cmd.extend(["--body-floor", str(args["body_floor"])])
    return await executor.run(*cmd)


async def _macro_seed_industries(args: Dict[str, Any]) -> Dict[str, Any]:
    """Seed industry-signal market bodies (016, universe-gated census)."""
    cmd = ["macro", "seed-industries"]
    if args.get("industries"):
        cmd.extend(["--industries", str(args["industries"])])
    if args.get("min_members") is not None:
        cmd.extend(["--min-members", str(args["min_members"])])
    if args.get("body_floor") is not None:
        cmd.extend(["--body-floor", str(args["body_floor"])])
    return await executor.run(*cmd)


async def _macro_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List the macro-series catalog (read-only)."""
    async def _run():
        return await GefionExecutor().run("macro", "list")
    return await _execute_with_health_check(['postgres'], _run)


async def _quality_findings(args: Dict[str, Any]) -> Dict[str, Any]:
    """List data-quality findings (read-only)."""
    async def _run():
        cmd = ["quality", "findings"]
        for opt in ("metric", "symbol", "entity-table", "entity-id", "verdict",
                    "since", "limit"):
            key = opt.replace("-", "_")
            if args.get(key) is not None:
                cmd.extend([f"--{opt}", str(args[key])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _quality_catalog(args: Dict[str, Any]) -> Dict[str, Any]:
    """Show the validation catalog (read-only)."""
    async def _run():
        return await GefionExecutor().run("quality", "catalog")
    return await _execute_with_health_check(['postgres'], _run)


async def _quality_backfill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate stored history and record findings (mutating — ledger only)."""
    async def _run():
        cmd = ["quality", "backfill"]
        if args.get("entity_table"):
            cmd.extend(["--entity-table", str(args["entity_table"])])
        if args.get("metric"):
            cmd.extend(["--metric", str(args["metric"])])
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _quality_normalize_taxonomy(args: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize stored sector/industry taxonomy (mutating with apply=true)."""
    async def _run():
        cmd = ["quality", "normalize-taxonomy"]
        if args.get("apply"):
            cmd.append("--apply")
        return await GefionExecutor().run(*cmd)
    return await _execute_with_health_check(['postgres'], _run)


async def _quality_resolve(args: Dict[str, Any]) -> Dict[str, Any]:
    """Supersede a finding (mutating)."""
    async def _run():
        return await GefionExecutor().run(
            "quality", "resolve", str(args["finding_id"]),
            "--reason", str(args["reason"]))
    return await _execute_with_health_check(['postgres'], _run)


# ============================================================================
# Main Entry Point
# ============================================================================

async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
