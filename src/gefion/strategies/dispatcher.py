"""
Strategy dispatcher for loading strategies from the database.

Uses strategy_registry (implementations) and strategy_configs (parameterized instances).
Strategy implementations remain as Python classes - DB just stores the registry.
"""
from __future__ import annotations

import importlib
import warnings
from typing import Any, Dict, List, Optional, Type

import psycopg
from psycopg.types.json import Jsonb

from gefion.observability import create_span, set_attributes


# Built-in strategies with their module paths and class names
BUILTIN_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "momentum": {
        "module_path": "gefion.strategies.momentum",
        "class_name": "MomentumStrategy",
        "description": "Momentum-based strategy that buys top performers",
        "default_params": {"lookback_days": 20, "top_n": 10, "rebalance_days": 5},
        "tags": ["momentum", "trend-following"],
    },
    "mean_reversion": {
        "module_path": "gefion.strategies.mean_reversion",
        "class_name": "MeanReversionStrategy",
        "description": "Mean reversion using RSI indicator",
        "default_params": {"rsi_oversold": 30, "rsi_overbought": 70, "rsi_period": 14},
        "tags": ["mean-reversion", "rsi"],
    },
    "ma_crossover": {
        "module_path": "gefion.strategies.ma_crossover",
        "class_name": "MovingAverageCrossoverStrategy",
        "description": "Moving average crossover signals",
        "default_params": {"fast_period": 50, "slow_period": 200},
        "tags": ["trend-following", "moving-average"],
    },
    "cross_sectional_decile": {
        "module_path": "gefion.strategies.cross_sectional",
        "class_name": "CrossSectionalDecileStrategy",
        "default_params": {"signal_period": 14, "decile": 0.10,
                           "rebalance_days": 20, "allocation_pct": 0.90},
        "description": "Long top / short bottom decile by cross-sectional "
                       "stochastic-K rank; whole book closed each rebalance; "
                       "optionally regime-gated (flat out of state)",
        "tags": ["cross-sectional", "long-short", "regime-gated"],
    },
    "breakout": {
        "module_path": "gefion.strategies.breakout",
        "class_name": "BreakoutStrategy",
        "description": "Breakout trading with volume confirmation",
        "default_params": {"lookback_days": 20, "volume_threshold": 1.5},
        "tags": ["breakout", "volume"],
    },
    "pairs_trading": {
        "module_path": "gefion.strategies.pairs_trading",
        "class_name": "PairsTradingStrategy",
        "description": "Statistical arbitrage on correlated pairs",
        "default_params": {"entry_zscore": 2.0, "exit_zscore": 0.5},
        "tags": ["pairs", "statistical-arbitrage"],
    },
    "rsi_divergence": {
        "module_path": "gefion.strategies.rsi_divergence",
        "class_name": "RSIDivergenceStrategy",
        "description": "RSI divergence detection strategy",
        "default_params": {"rsi_period": 14, "divergence_lookback": 10},
        "tags": ["rsi", "divergence"],
    },
    "volatility_contraction": {
        "module_path": "gefion.strategies.volatility_contraction",
        "class_name": "VolatilityContractionStrategy",
        "description": "Volatility squeeze and expansion strategy",
        "default_params": {"bb_period": 20, "bb_std_dev": 2.0, "squeeze_threshold": 0.05},
        "tags": ["volatility", "bollinger-bands"],
    },
    "ml_signal": {
        "module_path": "gefion.strategies.ml_signal",
        "class_name": "MLSignalStrategy",
        "description": "ML-based strategy using quantile or classifier predictions",
        "default_params": {
            "model_name": "quantile",
            "model_version": "latest",
            "horizon_days": 7,
            "prediction_type": "quantile",
            "return_threshold": 0.02,
            "max_positions": 10,
        },
        "tags": ["ml", "quantile-regression", "classifier", "predictions"],
    },
    "ml_filter": {
        "module_path": "gefion.strategies.ml_filter",
        "class_name": "MLFilterStrategy",
        "description": "Hybrid strategy: filters base strategy signals through ML predictions",
        "default_params": {
            "model_name": "quantile",
            "model_version": "latest",
            "horizon_days": 7,
            "filter_mode": "confirm",
            "prediction_type": "quantile",
            "min_q50": 0.0,
        },
        "tags": ["ml", "hybrid", "filter", "wrapper"],
    },
}


def load_strategy_class(
    conn: psycopg.Connection,
    strategy_name: str,
) -> Optional[Type]:
    """
    Load a strategy class by name from the registry.

    Args:
        conn: Database connection
        strategy_name: Name of the strategy in registry

    Returns:
        Strategy class or None if not found/disabled
    """
    with create_span("strategies.dispatcher.load_strategy_class", strategy_name=strategy_name) as span:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT module_path, class_name
                FROM strategy_registry
                WHERE enabled = TRUE AND name = %s;
                """,
                (strategy_name,),
            )
            row = cur.fetchone()

        if not row:
            set_attributes(span, found=False)
            return None

        module_path, class_name = row
        set_attributes(span, found=True, module_path=module_path)
        return _load_from_module(module_path, class_name)


def _load_from_module(module_path: str, class_name: str) -> Optional[Type]:
    """Load a strategy class from a Python module."""
    try:
        module = importlib.import_module(module_path)
        strategy_class = getattr(module, class_name, None)
        return strategy_class
    except (ImportError, AttributeError) as exc:
        warnings.warn(f"Failed to import {module_path}.{class_name}: {exc}")
        return None


def get_strategy_registry(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """
    List all enabled strategies in the registry.

    Args:
        conn: Database connection

    Returns:
        List of strategy registry entries
    """
    with create_span("strategies.dispatcher.get_strategy_registry") as span:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, module_path, class_name, default_params,
                       param_schema, description, tags
                FROM strategy_registry
                WHERE enabled = TRUE
                ORDER BY name;
                """
            )
            rows = cur.fetchall()

        result = [
            {
                'name': row[0],
                'module_path': row[1],
                'class_name': row[2],
                'default_params': row[3] or {},
                'param_schema': row[4],
                'description': row[5],
                'tags': row[6] or [],
            }
            for row in rows
        ]
        set_attributes(span, result_count=len(result))
        return result


def get_strategy_config(
    conn: psycopg.Connection,
    config_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Load a strategy configuration by name.

    Args:
        conn: Database connection
        config_name: Name of the strategy config

    Returns:
        Dict with config details or None if not found
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.strategy_name, c.params, c.description, c.active,
                   r.module_path, r.class_name, r.default_params
            FROM strategy_configs c
            JOIN strategy_registry r ON c.strategy_name = r.name
            WHERE c.name = %s AND c.active = TRUE AND r.enabled = TRUE;
            """,
            (config_name,),
        )
        row = cur.fetchone()

    if not row:
        return None

    # Merge default_params with config params (config overrides defaults)
    default_params = row[8] or {}
    config_params = row[3] or {}
    merged_params = {**default_params, **config_params}

    return {
        'id': row[0],
        'name': row[1],
        'strategy_name': row[2],
        'params': merged_params,
        'description': row[4],
        'active': row[5],
        'module_path': row[6],
        'class_name': row[7],
    }


def get_strategy_configs(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """
    List all active strategy configurations.

    Args:
        conn: Database connection

    Returns:
        List of strategy config dicts
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.strategy_name, c.params, c.description,
                   r.default_params
            FROM strategy_configs c
            JOIN strategy_registry r ON c.strategy_name = r.name
            WHERE c.active = TRUE AND r.enabled = TRUE
            ORDER BY c.name;
            """
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        default_params = row[5] or {}
        config_params = row[3] or {}
        merged_params = {**default_params, **config_params}

        results.append({
            'id': row[0],
            'name': row[1],
            'strategy_name': row[2],
            'params': merged_params,
            'description': row[4],
        })

    return results


def instantiate_strategy(
    conn: psycopg.Connection,
    config_name: str,
    param_overrides: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Instantiate a strategy from a configuration.

    Loads the strategy class from registry, then instantiates
    it with merged parameters (defaults + config + overrides).

    Args:
        conn: Database connection
        config_name: Name of the strategy config
        param_overrides: Optional parameter overrides

    Returns:
        Strategy instance or None if not found
    """
    with create_span("strategies.dispatcher.instantiate_strategy", config_name=config_name) as span:
        config = get_strategy_config(conn, config_name)
        if not config:
            set_attributes(span, found=False)
            return None

        # Load strategy class
        strategy_class = _load_from_module(config['module_path'], config['class_name'])
        if not strategy_class:
            set_attributes(span, found=True, class_loaded=False)
            return None

        # Merge params (config params already include defaults)
        params = {**config['params']}
        if param_overrides:
            params.update(param_overrides)

        # Instantiate
        try:
            set_attributes(span, found=True, class_loaded=True, strategy_name=config['strategy_name'])
            return strategy_class(**params)
        except Exception as exc:
            warnings.warn(f"Failed to instantiate strategy '{config_name}': {exc}")
            return None


def create_strategy_config(
    conn: psycopg.Connection,
    name: str,
    strategy_name: str,
    params: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
) -> int:
    """
    Create a new strategy configuration.

    Args:
        conn: Database connection
        name: Unique name for the config
        strategy_name: Name of strategy in registry
        params: Configuration parameters
        description: Optional description

    Returns:
        ID of created config

    Raises:
        ValueError: If strategy_name not in registry
    """
    # Verify strategy exists
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM strategy_registry WHERE name = %s AND enabled = TRUE;",
            (strategy_name,),
        )
        if not cur.fetchone():
            raise ValueError(f"Strategy '{strategy_name}' not found in registry")

    # Insert config
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategy_configs (name, strategy_name, params, description)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """,
            (name, strategy_name, Jsonb(params or {}), description),
        )
        config_id = cur.fetchone()[0]

    conn.commit()
    return config_id


def seed_builtin_strategies(conn: psycopg.Connection) -> int:
    """
    Seed the database with built-in strategies.

    Populates strategy_registry with built-in Python implementations
    and creates a default config for each.

    This is idempotent - running multiple times won't create duplicates.

    Args:
        conn: Database connection

    Returns:
        Number of strategies seeded
    """
    with create_span("strategies.dispatcher.seed_builtin_strategies") as span:
        with conn.cursor() as cur:
            # Seed strategy_registry
            for name, info in BUILTIN_STRATEGIES.items():
                cur.execute(
                    """
                    INSERT INTO strategy_registry
                        (name, module_path, class_name, default_params, description, tags, enabled)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, true)
                    ON CONFLICT (name) DO UPDATE SET
                        module_path = EXCLUDED.module_path,
                        class_name = EXCLUDED.class_name,
                        default_params = EXCLUDED.default_params,
                        description = EXCLUDED.description,
                        tags = EXCLUDED.tags;
                    """,
                    (
                        name,
                        info['module_path'],
                        info['class_name'],
                        Jsonb(info.get('default_params', {})),
                        info['description'],
                        info.get('tags', []),
                    ),
                )

            # Seed default configs (one per strategy with default params)
            for name, info in BUILTIN_STRATEGIES.items():
                cur.execute(
                    """
                    INSERT INTO strategy_configs
                        (name, strategy_name, params, description, active)
                    VALUES
                        (%s, %s, '{}', %s, true)
                    ON CONFLICT (name) DO NOTHING;
                    """,
                    (name, name, f"Default {info['description']}"),
                )

        conn.commit()
        set_attributes(span, strategy_count=len(BUILTIN_STRATEGIES))
        return len(BUILTIN_STRATEGIES)


# Backwards compatibility - keep old function names as aliases
def load_strategy_function(conn: psycopg.Connection, function_name: str) -> Optional[Type]:
    """Alias for load_strategy_class (backwards compatibility)."""
    return load_strategy_class(conn, function_name)


def load_strategy_definition(conn: psycopg.Connection, definition_name: str) -> Optional[Dict[str, Any]]:
    """Alias for get_strategy_config (backwards compatibility)."""
    config = get_strategy_config(conn, definition_name)
    if config:
        # Map to old field names
        return {
            'name': config['name'],
            'function_name': config['strategy_name'],
            'params': config['params'],
            'description': config['description'],
        }
    return None


def get_available_strategies(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Alias for get_strategy_configs (backwards compatibility)."""
    return get_strategy_configs(conn)
