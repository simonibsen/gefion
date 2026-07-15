"""Prediction-derived signals (spec 012): stored predictions become
per-stock features and market-level series, through existing molds only.

Per-stock feature names carry the FULL model identity
(`pred_q50_h30__<model>_<version>`) so two vintages can never silently mix
in a hunt. The market bodies (median outlook, median confidence width) are
seeded create-if-absent into feature_functions with scope='market' — after
seeding, the DATABASE body is the source of truth (the 011 rule), and
`macro derive` computes them like any other derived series.
"""
import json
from typing import Any, Dict, List

from gefion.observability import create_span, set_attributes

QUANTILES = ("q10", "q50", "q90")


def _next_month(d):
    import datetime as _dt
    return (d.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)


class SignalMaterializeError(ValueError):
    """Unknown model, missing vintage cutoff, or no predictions to expose."""


def pred_feature_name(quantile: str, horizon: int,
                      model_name: str, model_version: str) -> str:
    return f"pred_{quantile}_h{horizon}__{model_name}_{model_version}"


def _load_vintage_model(conn, model_name: str, model_version: str) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, hyperparams->>'training_cutoff' FROM ml_models "
            "WHERE name = %s AND version = %s", (model_name, model_version))
        row = cur.fetchone()
    if row is None:
        raise SignalMaterializeError(
            f"model {model_name}:{model_version} not found — train it first "
            f"(`gefion ml train`)")
    if not row[1]:
        raise SignalMaterializeError(
            f"model {model_name}:{model_version} has no recorded training "
            f"cutoff — signals require a VINTAGE model (rebuild the dataset "
            f"with --end-date and retrain)")
    return {"id": row[0], "cutoff": row[1]}


def materialize_prediction_features(conn, model_name: str,
                                    model_version: str,
                                    full: bool = False) -> Dict[str, Any]:
    """Expose stored quantile predictions as per-stock computed features,
    one feature per (quantile, horizon), idempotent and incremental (the
    computed_features primary key dedups; the month scan resumes at the
    last materialized month unless full=True). Then seed the market bodies.

    Returns {"features": {name: new_rows}, "horizons": [...], "cutoff": ...,
    "market_functions": [...]}.
    """
    from gefion.db.ingest import ensure_feature_definitions

    with create_span("ml.signal_features.materialize", model=model_name,
                     version=model_version) as span:
        from gefion.macro.derived import ensure_materialized_function
        ensure_materialized_function(
            conn, "model_prediction",
            "vintage-model prediction quantiles (spec 012)",
            materialized_by="gefion.ml")
        model = _load_vintage_model(conn, model_name, model_version)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT horizon_days FROM predictions "
                "WHERE model_id = %s AND prediction_type = 'quantile' "
                "ORDER BY horizon_days", (model["id"],))
            horizons = [r[0] for r in cur.fetchall()]
        if not horizons:
            raise SignalMaterializeError(
                f"model {model_name}:{model_version} has no stored "
                f"predictions — run `gefion ml predict-backfill` first")

        written: Dict[str, int] = {}
        for horizon in horizons:
            for q in QUANTILES:
                feat = pred_feature_name(q, horizon, model_name, model_version)
                ids = ensure_feature_definitions(conn, [{
                    "name": feat, "function_name": "model_prediction",
                    "params": {"model_name": model_name,
                               "model_version": model_version,
                               "training_cutoff": model["cutoff"],
                               "quantile": q, "horizon_days": horizon},
                    "source_table": "predictions", "source_column": None,
                    "store_table": "computed_features", "store_column": "value",
                    "store_type": "double precision", "active": True,
                    "entity_table": "stocks",
                }])
                # Batched by month: one statement over the full span
                # decompresses years of prediction chunks in a single
                # transaction and can OOM the server. Each batch commits
                # alone; a crash mid-way resumes idempotently (PK dedup).
                written[feat] = 0
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT date_trunc('month', min(prediction_date)), "
                        "       max(prediction_date) FROM predictions "
                        "WHERE model_id = %s", (model["id"],))
                    lo, hi = cur.fetchone()
                    if not full:
                        # Incremental (nightly): resume at the month of the
                        # last materialized row — rescanning all of history
                        # was an hour-plus nightly tail (#120). Sound because
                        # predict-backfill appends strictly forward; the
                        # partial month redoes cheaply (PK dedup). full=True
                        # is the deliberate whole-history rescan.
                        cur.execute(
                            "SELECT date_trunc('month', max(date)) "
                            "FROM computed_features WHERE feature_id = %s",
                            (ids[feat],))
                        row = cur.fetchone()
                        if row and row[0] is not None and lo is not None:
                            lo = max(lo, row[0])
                batch = lo
                while batch is not None and batch.date() <= hi:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO computed_features
                                   (data_id, date, feature_id, value)
                               SELECT p.data_id, p.prediction_date, %(fid)s,
                                      (p.prediction_values->>%(q)s)::double precision
                               FROM predictions p
                               WHERE p.model_id = %(mid)s
                                 AND p.horizon_days = %(h)s
                                 AND p.prediction_type = 'quantile'
                                 AND p.prediction_date >= %(batch)s
                                 AND p.prediction_date
                                     < %(batch)s + interval '1 month'
                                 AND p.prediction_values->>%(q)s IS NOT NULL
                               ON CONFLICT (data_id, feature_id, date)
                               DO NOTHING""",
                            {"fid": ids[feat], "q": q, "mid": model["id"],
                             "h": horizon, "batch": batch})
                        written[feat] += cur.rowcount
                    conn.commit()
                    batch = _next_month(batch)
        seeded = seed_model_market_bodies(
            conn, model_name, model_version, horizons[0], model["cutoff"])
        conn.commit()
        set_attributes(span, horizons=len(horizons),
                       new_rows=sum(written.values()))
        return {"features": written, "horizons": horizons,
                "cutoff": model["cutoff"], "market_functions": seeded}


def seed_model_market_bodies(conn, model_name: str, model_version: str,
                             horizon: int, cutoff: str) -> List[str]:
    """Plant the model-signal market bodies create-if-absent (DB wins after
    that, exactly like the repo SEED_BODIES). Returns the function names."""
    from gefion.macro.market_bodies import model_signal_bodies

    bodies = model_signal_bodies(model_name, model_version, horizon, cutoff)
    with conn.cursor() as cur:
        for name, spec in bodies.items():
            cur.execute(
                """INSERT INTO feature_functions
                       (name, version, status, enabled, description, language,
                        function_body, inputs, scope)
                   VALUES (%s, 'v1', 'active', TRUE, %s, 'python', %s, %s,
                           'market')
                   ON CONFLICT DO NOTHING""",
                (name, spec["description"], spec["body"],
                 json.dumps(spec["inputs"])))
    return sorted(bodies)
