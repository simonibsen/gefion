"""Unified predictions table helper — JSONB packing/unpacking for insert and query."""
from datetime import date
from typing import Any, Dict, List, Optional

from psycopg import sql
from psycopg.types.json import Json

from gefion.observability import create_span, set_attributes


def insert_prediction(
    cur,
    model_id: int,
    data_id: int,
    prediction_date: date,
    horizon_days: int,
    prediction_type: str,
    values_dict: Dict[str, Any],
    metadata_dict: Optional[Dict[str, Any]] = None,
    run_id: Optional[int] = None,
) -> None:
    """Insert or upsert a single prediction row with JSONB packing."""
    with create_span("db.predictions.insert", prediction_type=prediction_type, model_id=model_id):
        cur.execute(
            """
            INSERT INTO predictions
                (model_id, data_id, prediction_date, horizon_days,
                 prediction_type, prediction_values, metadata, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_id, data_id, prediction_date, horizon_days, prediction_type)
            DO UPDATE SET
                prediction_values = EXCLUDED.prediction_values,
                metadata = EXCLUDED.metadata,
                run_id = EXCLUDED.run_id,
                created_at = NOW()
            """,
            (
                model_id, data_id, prediction_date, horizon_days,
                prediction_type, Json(values_dict), Json(metadata_dict or {}), run_id,
            ),
        )


def insert_quantile_prediction(
    cur,
    model_id: int,
    data_id: int,
    prediction_date: date,
    horizon_days: int,
    q10: float,
    q50: float,
    q90: float,
    model_version: Optional[str] = None,
    features_snapshot: Optional[Dict] = None,
    run_id: Optional[int] = None,
) -> None:
    """Insert a quantile prediction, packing q10/q50/q90 into JSONB."""
    with create_span("db.predictions.insert_quantile", model_id=model_id, data_id=data_id):
        values = {"q10": q10, "q50": q50, "q90": q90}
        metadata = {}
        if model_version is not None:
            metadata["model_version"] = model_version
        if features_snapshot is not None:
            metadata["features_snapshot"] = features_snapshot
        insert_prediction(
            cur, model_id, data_id, prediction_date, horizon_days,
            "quantile", values, metadata, run_id,
        )


def insert_trend_prediction(
    cur,
    model_id: int,
    data_id: int,
    prediction_date: date,
    horizon_days: int,
    predicted_class: str,
    class_probs: Dict[str, float],
    entropy: float,
    margin: float,
    weak_threshold: Optional[float] = None,
    strong_threshold: Optional[float] = None,
    run_id: Optional[int] = None,
) -> None:
    """Insert a trend class prediction, packing probabilities into JSONB."""
    with create_span("db.predictions.insert_trend", model_id=model_id, data_id=data_id):
        values = {
            "predicted_class": predicted_class,
            "entropy": entropy,
            "margin": margin,
            **class_probs,
        }
        metadata = {}
        if weak_threshold is not None:
            metadata["weak_threshold"] = weak_threshold
        if strong_threshold is not None:
            metadata["strong_threshold"] = strong_threshold
        insert_prediction(
            cur, model_id, data_id, prediction_date, horizon_days,
            "trend_class", values, metadata, run_id,
        )


def query_predictions(
    cur,
    prediction_type: Optional[str] = None,
    model_id: Optional[int] = None,
    data_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    horizon_days: Optional[int] = None,
    limit: Optional[int] = None,
    order_desc: bool = True,
) -> List[Dict[str, Any]]:
    """Query predictions with optional filters, unpacking JSONB into flat dicts.

    Returns list of dicts with common fields (model_id, data_id, prediction_date,
    horizon_days, prediction_type, run_id, created_at) plus type-specific fields
    extracted from prediction_values and metadata JSONB columns.
    """
    with create_span("db.predictions.query", prediction_type=prediction_type) as span:
        conditions = []
        params: list = []

        if prediction_type is not None:
            conditions.append("prediction_type = %s")
            params.append(prediction_type)
        if model_id is not None:
            conditions.append("model_id = %s")
            params.append(model_id)
        if data_id is not None:
            conditions.append("data_id = %s")
            params.append(data_id)
        if date_from is not None:
            conditions.append("prediction_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conditions.append("prediction_date <= %s")
            params.append(date_to)
        if horizon_days is not None:
            conditions.append("horizon_days = %s")
            params.append(horizon_days)

        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        order = "DESC" if order_desc else "ASC"
        limit_clause = f"LIMIT {limit}" if limit else ""

        cur.execute(
            f"""
            SELECT model_id, data_id, prediction_date, horizon_days,
                   prediction_type, prediction_values, metadata,
                   run_id, created_at
            FROM predictions
            WHERE {where_clause}
            ORDER BY prediction_date {order}, model_id, data_id
            {limit_clause}
            """,
            params,
        )

        rows = []
        for (
            m_id, d_id, p_date, h_days,
            p_type, p_values, meta,
            r_id, created,
        ) in cur.fetchall():
            row: Dict[str, Any] = {
                "model_id": m_id,
                "data_id": d_id,
                "prediction_date": p_date,
                "horizon_days": h_days,
                "prediction_type": p_type,
                "run_id": r_id,
                "created_at": created,
            }
            # Unpack JSONB values into flat dict
            if p_values:
                row.update(p_values)
            if meta:
                row.update(meta)
            rows.append(row)

        set_attributes(span, result_count=len(rows))
        return rows
