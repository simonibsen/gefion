"""Per-model ML artifact deletion (#76 audit door).

Deletes one model and its OWNED artifact family in dependency order:
materialized signal features (values → definitions → marker functions),
predictions, prediction outcomes, performance rows, then the model row.
Training runs and datasets are reusable INPUTS — never deleted here.
Active models are a --force gate. Audit ledgers (discovery
pre-registrations naming the model) are reported, never mutated.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from gefion.observability import create_span, set_attributes


def _model_row(conn, name: str, version: str) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, version, algorithm, active, created_at "
            "FROM ml_models WHERE name = %s AND version = %s",
            (name, version))
        r = cur.fetchone()
    if r is None:
        raise ValueError(f"no model {name!r} version {version!r}")
    return {"id": r[0], "name": r[1], "version": r[2], "algorithm": r[3],
            "active": r[4], "created_at": str(r[5])}


def _signal_feature_names(conn, name: str, version: str) -> list:
    """Materialized per-stock signal features derived from this model's
    predictions (spec 012 naming: pred_*__<name>_<version>)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name FROM feature_definitions "
            "WHERE name LIKE %s ORDER BY name",
            (f"pred\\_%\\_\\_{name}\\_{version}",))
        return [r[0] for r in cur.fetchall()]


def plan_model_delete(conn, name: str, version: str) -> Dict[str, Any]:
    """Dry-run: the full blast radius, changing nothing."""
    with create_span("ml.deletion.plan", model=f"{name}:{version}") as span:
        model = _model_row(conn, name, version)
        counts = {}
        with conn.cursor() as cur:
            for key, table in (("predictions", "predictions"),
                               ("prediction_outcomes", "prediction_outcomes"),
                               ("model_performance", "model_performance")):
                cur.execute(f"SELECT count(*) FROM {table} WHERE model_id = %s",
                            (model["id"],))
                counts[key] = cur.fetchone()[0]
        signals = _signal_feature_names(conn, name, version)
        plan = {"model": model, "active": model["active"],
                "materialized_signals": signals, **counts}
        set_attributes(span, **{k: v for k, v in counts.items()},
                       n_signals=len(signals))
        return plan


def execute_model_delete(conn, name: str, version: str,
                         force: bool = False) -> Dict[str, Any]:
    """Delete the model and its owned artifacts, dependency order. An
    active model refuses without force (it is the production model)."""
    with create_span("ml.deletion.execute", model=f"{name}:{version}",
                     force=force) as span:
        model = _model_row(conn, name, version)
        if model["active"] and not force:
            raise ValueError(
                f"model {name}:{version} is active (production) — pass "
                "--force to delete it anyway")
        signals = _signal_feature_names(conn, name, version)
        deleted: Dict[str, int] = {}
        with conn.transaction():
            with conn.cursor() as cur:
                if signals:
                    cur.execute(
                        """DELETE FROM computed_features WHERE feature_id IN
                           (SELECT id FROM feature_definitions
                            WHERE name = ANY(%s))""", (signals,))
                    deleted["signal_values"] = cur.rowcount
                    cur.execute("DELETE FROM feature_definitions "
                                "WHERE name = ANY(%s)", (signals,))
                    cur.execute("DELETE FROM feature_functions "
                                "WHERE name = ANY(%s)", (signals,))
                for key, table in (("predictions", "predictions"),
                                   ("prediction_outcomes", "prediction_outcomes"),
                                   ("model_performance", "model_performance")):
                    cur.execute(f"DELETE FROM {table} WHERE model_id = %s",
                                (model["id"],))
                    deleted[key] = cur.rowcount
                cur.execute("DELETE FROM ml_models WHERE id = %s",
                            (model["id"],))
        deleted["materialized_signals"] = len(signals)
        set_attributes(span, **{k: v for k, v in deleted.items()})
        return deleted
