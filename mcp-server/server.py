#!/usr/bin/env python3
"""
G2 MCP Server - Natural language interface to g2 ML platform.

Provides MCP tools for:
- ML workflow (dataset build, train, predict, evaluate)
- Database queries (predictions, model performance)
- Feature management
- Data ingestion
"""

import asyncio
import json
import subprocess
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


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


# Initialize server
app = Server("g2-mcp-server")
executor = G2Executor()


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
                "Fetches latest OHLCV data and computes technical indicators. "
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
            description="List all registered feature definitions with metadata.",
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

async def _ml_dataset_build(args: Dict[str, Any]) -> Dict[str, Any]:
    """Build ML dataset."""
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
