#!/usr/bin/env python3
"""
G2 MCP Server - Natural language interface to g2 ML platform.

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
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path to import g2 modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Import g2 health check module
try:
    from g2 import health
except ImportError:
    # Fallback if g2 module not in path
    health = None


class G2Executor:
    """Execute g2 CLI commands and return JSON results."""

    def __init__(self, db_url: Optional[str] = None, api_key: Optional[str] = None):
        self.env = {}
        if db_url:
            self.env['DATABASE_URL'] = db_url
        if api_key:
            self.env['ALPHAVANTAGE_API_KEY'] = api_key

    async def run(self, *args: str) -> Dict[str, Any]:
        """Run g2 command with --json flag and return parsed output."""
        cmd = ['g2'] + list(args) + ['--json']

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
            "message": f"{service} health check unavailable (g2.health module not found)",
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
app = Server("g2-mcp-server")
executor = G2Executor()
health_cache = HealthCheckCache(ttl_seconds=60)


# ============================================================================
# ML Workflow Tools
# ============================================================================

@app.list_tools()
async def list_tools() -> List[Tool]:
    """List all available MCP tools."""
    return [
        # ML Workflow
        Tool(
            name="ml_dataset_build",
            description=(
                "Build ML training dataset with features and labels. "
                "Creates manifest, exports CSVs (prices.csv, features.csv, labels.csv). "
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
                    "out_dir": {"type": "string", "description": "Output directory for CSVs", "default": "datasets"},
                    "export": {"type": "boolean", "description": "Export CSVs", "default": True},
                },
                "required": ["name", "version"],
            },
        ),

        Tool(
            name="ml_train",
            description=(
                "Train quantile regression models for multi-horizon prediction. "
                "Trains q10/q50/q90 models for each horizon. "
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
                        "description": "Algorithm: quantile_regression (sklearn), xgboost, or lightgbm",
                        "default": "quantile_regression",
                        "enum": ["quantile_regression", "xgboost", "lightgbm"]
                    },
                    "out_dir": {"type": "string", "description": "Output directory for model artifacts", "default": "models"},
                },
                "required": ["dataset_name", "dataset_version", "model_name", "model_version"],
            },
        ),

        Tool(
            name="ml_predict",
            description=(
                "Generate predictions for symbols on a specific date. "
                "Fetches features from database, loads model artifacts, generates q10/q50/q90 predictions. "
                "Stores results in quantile_predictions table."
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

        # Database Query Tools
        Tool(
            name="query_predictions",
            description=(
                "Query stored predictions from database. "
                "Returns predictions with symbol, date, horizon, and quantile values (q10/q50/q90)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Filter by symbol (e.g., AAPL)"},
                    "model_name": {"type": "string", "description": "Filter by model name"},
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
                "market-relative features (percentile ranks, z-scores). "
                "Use --local for local computation (faster, no API limits)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "exchange": {"type": "string", "description": "Exchange name (e.g., NASDAQ)", "default": "NASDAQ"},
                    "timeframe": {"type": "string", "description": "Timeframe: auto, compact, or full", "default": "auto"},
                    "local": {"type": "boolean", "description": "Use local computation for features", "default": True},
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
                    "service_name": {"type": "string", "description": "Service name tag to filter by", "default": "g2"},
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
                    "service_name": {"type": "string", "description": "Service name to search for", "default": "g2"},
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
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> List[TextContent]:
    """Handle tool invocations."""

    try:
        if name == "ml_dataset_build":
            result = await _ml_dataset_build(arguments)
        elif name == "ml_train":
            result = await _ml_train(arguments)
        elif name == "ml_predict":
            result = await _ml_predict(arguments)
        elif name == "ml_eval":
            result = await _ml_eval(arguments)
        elif name == "ml_train_classifier":
            result = await _ml_train_classifier(arguments)
        elif name == "ml_predict_classifier":
            result = await _ml_predict_classifier(arguments)
        elif name == "query_predictions":
            result = await _query_predictions(arguments)
        elif name == "query_model_performance":
            result = await _query_model_performance(arguments)
        elif name == "data_update":
            result = await _data_update(arguments)
        elif name == "features_list":
            result = await _features_list(arguments)
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
        else:
            result = {"success": False, "error": f"Unknown tool: {name}"}

        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]

    except Exception as e:
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
        if args.get('out_dir'):
            cmd.extend(['--out-dir', args['out_dir']])
        if args.get('export', True):
            cmd.append('--export')

        return await executor.run(*cmd)

    # ML operations require PostgreSQL
    return await _execute_with_health_check(['postgres'], _build)


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


async def _query_predictions(args: Dict[str, Any]) -> Dict[str, Any]:
    """Query predictions from database using SQL."""
    # Build SQL query
    where_clauses = []
    if args.get('symbol'):
        where_clauses.append(f"s.symbol = '{args['symbol']}'")
    if args.get('model_name'):
        where_clauses.append(f"m.name = '{args['model_name']}'")
    if args.get('start_date'):
        where_clauses.append(f"qp.prediction_date >= '{args['start_date']}'")
    if args.get('end_date'):
        where_clauses.append(f"qp.prediction_date <= '{args['end_date']}'")
    if args.get('horizon'):
        where_clauses.append(f"qp.horizon_days = {args['horizon']}")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    limit = args.get('limit', 100)

    sql = f"""
        SELECT
            s.symbol,
            qp.prediction_date,
            qp.horizon_days,
            qp.q10,
            qp.q50,
            qp.q90,
            (qp.q90 - qp.q10) as iqr,
            m.name as model_name,
            m.version as model_version
        FROM quantile_predictions qp
        JOIN stocks s ON qp.data_id = s.id
        JOIN ml_models m ON qp.model_id = m.id
        WHERE {where_sql}
        ORDER BY qp.prediction_date DESC, s.symbol, qp.horizon_days
        LIMIT {limit}
    """

    # Execute via psql (g2 doesn't have a direct SQL query command)
    import os
    db_url = os.environ.get('DATABASE_URL', 'postgresql://g2:g2pass@localhost:5432/g2')

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
            if len(parts) >= 8:
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
    db_url = os.environ.get('DATABASE_URL', 'postgresql://g2:g2pass@localhost:5432/g2')

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
    if args.get('local', True):
        cmd.append('--local')
    if args.get('limit'):
        cmd.extend(['--limit', str(args['limit'])])

    return await executor.run(*cmd)


async def _features_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List feature definitions."""
    return await executor.run('features-list')


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
    db_url = os.environ.get('DATABASE_URL', 'postgresql://g2:g2pass@localhost:5432/g2')

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
    """Check recent traces using g2 span-check command (backend-agnostic)."""
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
    service_name = args.get('service_name', 'g2')
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
        app_span_count = sum(1 for s in spans if 'g2.observability' in s.get('scope', ''))
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
