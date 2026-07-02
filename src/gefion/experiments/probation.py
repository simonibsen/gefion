"""Probation and auto-demotion of promoted experiment artifacts (FR-027).

Promotion (cycle FDR survivors, or `experiment apply`) opens a probation
window by stamping experiments.probation_until. While the window is open,
run_probation_checks() measures the applied model's realized quantile loss
against the experiment's recorded score and demotes on measurable
degradation: demoted_at is stamped, the promoted feature function is set
to 'demoted', and its feature definition is deactivated.

Demotion requires evidence: no applied model, too few realized outcomes,
or a non-comparable objective all skip rather than demote.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from gefion.observability import create_span, set_attributes

from gefion.experiments.production import _db_conn

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.25  # relative degradation vs the experiment's score
MIN_SAMPLES = 30          # realized outcomes required before demotion

_QUANTILES = (0.1, 0.5, 0.9)


def pinball_loss(actual: float, predicted: float, quantile: float) -> float:
    """Pinball (quantile) loss for a single prediction."""
    error = actual - predicted
    return quantile * error if error >= 0 else (quantile - 1) * error


def is_degraded(realized_loss: Optional[float], baseline_loss: Optional[float],
                n_samples: int, tolerance: float = DEFAULT_TOLERANCE,
                min_samples: int = MIN_SAMPLES) -> bool:
    """Whether realized performance is measurably worse than the baseline.

    Conservative by design: missing data on either side, or too few
    samples, is never treated as degradation.
    """
    if realized_loss is None or baseline_loss is None or baseline_loss <= 0:
        return False
    if n_samples < min_samples:
        return False
    return realized_loss > baseline_loss * (1 + tolerance)


def _realized_quantile_loss(conn, model_name: str, model_version: str,
                            since: Optional[str]) -> Tuple[Optional[float], int]:
    """Mean pinball loss of a model's realized predictions since a date.

    Returns (loss, n_samples); (None, 0) when nothing has materialized yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.prediction_values, po.actual_return
            FROM predictions p
            JOIN ml_models m ON p.model_id = m.id
            JOIN prediction_outcomes po
                ON po.data_id = p.data_id
                AND po.prediction_date = p.prediction_date
                AND po.horizon_days = p.horizon_days
            WHERE m.name = %s AND m.version = %s
                AND p.prediction_type = 'quantile'
                AND po.actual_return IS NOT NULL
                AND (%s::date IS NULL OR p.prediction_date >= %s::date)
            """,
            (model_name, model_version, since, since),
        )
        rows = cur.fetchall()

    losses: List[float] = []
    for values, actual in rows:
        actual = float(actual)
        for q in _QUANTILES:
            predicted = (values or {}).get(f"q{int(q * 100)}")
            if predicted is not None:
                losses.append(pinball_loss(actual, float(predicted), q))
    if not losses:
        return None, 0
    return sum(losses) / len(losses), len(rows)


def demote_experiment(experiment_id: int, reason: str,
                      db_url: Optional[str] = None) -> bool:
    """Demote a promoted experiment artifact. Returns False if already demoted.

    Reverses promotion: feature function status -> 'demoted', feature
    definition deactivated, demoted_at stamped, reason recorded under
    results.probation.
    """
    from psycopg.types.json import Json

    with create_span("experiments.probation.demote", experiment_id=experiment_id) as span:
        with _db_conn(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE experiments
                    SET demoted_at = NOW(),
                        results = jsonb_set(
                            COALESCE(results, '{}'::jsonb), '{probation}', %s::jsonb)
                    WHERE id = %s AND demoted_at IS NULL
                    """,
                    (Json({"status": "demoted", "reason": reason,
                           "checked_at": datetime.now(timezone.utc).isoformat()}),
                     experiment_id),
                )
                if cur.rowcount == 0:
                    set_attributes(span, already_demoted=True)
                    return False

                cur.execute(
                    "SELECT config->'feature_config'->>'function_name' FROM experiments WHERE id = %s",
                    (experiment_id,),
                )
                row = cur.fetchone()
                fn_name = row[0] if row else None
                if fn_name:
                    cur.execute(
                        """
                        UPDATE feature_functions
                        SET status = 'demoted', updated_at = NOW()
                        WHERE name = %s AND status = 'active'
                        """,
                        (f"exp_{fn_name}",),
                    )
                    cur.execute(
                        "UPDATE feature_definitions SET active = FALSE WHERE name = %s",
                        (f"exp_{fn_name}",),
                    )
        logger.warning(f"Demoted experiment #{experiment_id}: {reason}")
        set_attributes(span, demoted=True)
        return True


def _mark_probation_passed(conn, experiment_id: int, detail: Dict[str, Any]) -> None:
    from psycopg.types.json import Json

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE experiments
            SET results = jsonb_set(COALESCE(results, '{}'::jsonb), '{probation}', %s::jsonb)
            WHERE id = %s
            """,
            (Json({"status": "passed",
                   "checked_at": datetime.now(timezone.utc).isoformat(), **detail}),
             experiment_id),
        )


def run_probation_checks(db_url: Optional[str] = None,
                         tolerance: float = DEFAULT_TOLERANCE,
                         min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """Evaluate every experiment on probation. Idempotent.

    Returns a summary: {"checked": n, "demoted": [...], "passed": [...],
    "monitoring": [...], "skipped": [...]} where each entry carries
    experiment_id and the measured evidence.
    """
    with create_span("experiments.probation.check") as span:
        with _db_conn(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, best_score, objective_metric,
                           results->'applied'->>'model_name',
                           results->'applied'->>'model_version',
                           (results->'applied'->>'applied_at')::date,
                           probation_until <= NOW() AS window_expired
                    FROM experiments
                    WHERE probation_until IS NOT NULL
                        AND demoted_at IS NULL
                        AND (results->'probation'->>'status') IS DISTINCT FROM 'passed'
                    ORDER BY id
                    """
                )
                candidates = cur.fetchall()

            summary: Dict[str, Any] = {
                "checked": len(candidates),
                "demoted": [], "passed": [], "monitoring": [], "skipped": [],
            }

            for (exp_id, name, best_score, objective, model_name, model_version,
                 applied_at, window_expired) in candidates:
                entry: Dict[str, Any] = {"experiment_id": exp_id, "name": name}

                if not model_name:
                    entry["reason"] = "no applied model to measure"
                    summary["skipped"].append(entry)
                    continue
                if objective and "loss" not in str(objective).lower():
                    entry["reason"] = (
                        f"objective '{objective}' not comparable to realized quantile loss"
                    )
                    summary["skipped"].append(entry)
                    continue

                realized, n = _realized_quantile_loss(
                    conn, model_name, model_version, str(applied_at) if applied_at else None
                )
                baseline = float(best_score) if best_score is not None else None
                entry.update({"realized_loss": realized, "baseline_loss": baseline,
                              "n_samples": n})

                if is_degraded(realized, baseline, n, tolerance, min_samples):
                    reason = (
                        f"realized quantile loss {realized:.5f} exceeds "
                        f"experiment score {baseline:.5f} by more than "
                        f"{tolerance:.0%} over {n} outcomes"
                    )
                    if demote_experiment(exp_id, reason, db_url=db_url):
                        entry["reason"] = reason
                        summary["demoted"].append(entry)
                    continue

                if window_expired:
                    _mark_probation_passed(conn, exp_id, {
                        "realized_loss": realized, "n_samples": n,
                    })
                    summary["passed"].append(entry)
                else:
                    summary["monitoring"].append(entry)

        set_attributes(span, checked=summary["checked"],
                       demoted=len(summary["demoted"]),
                       passed=len(summary["passed"]))
        return summary
