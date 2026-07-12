"""Derived macro series: facts about the universe's SHAPE (breadth,
dispersion) computed from our own cross-section — the information families
no single-stock indicator carries.

Follows the macro mold exactly (see ingest.materialize_feature): each series
is a `macro_series` catalog row plus a feature definition with
`entity_table='macro_series'`, values in computed_features keyed by the
series id — so a derived series becomes a discovery atom with ZERO DDL,
like macro_vix.

Honesty: a day whose cross-section is thinner than `min_stocks` gets NO
value (an honest gap, never a garbage number); recomputation is idempotent
and incremental (ON CONFLICT DO NOTHING from the last computed date).
"""
from typing import Any, Dict, Optional

from gefion.observability import create_span, set_attributes

from . import catalog

# Each entry: description + the per-date SQL producing (date, value) rows.
# %(min_stocks)s and %(start)s are bound parameters; the universe is
# asset_type='Stock' only (ETFs/blank excluded — they dilute breadth).
DERIVED_SERIES: Dict[str, Dict[str, Any]] = {
    "breadth_sma200": {
        "description": ("Breadth: % of Stock universe closing above its own "
                        "200-day SMA (participation)"),
        "sql": """
            SELECT o.date, 100.0 * AVG((o.close > cf.value)::int)::float
            FROM stock_ohlcv o
            JOIN stocks s ON s.id = o.data_id AND s.asset_type = 'Stock'
            JOIN computed_features cf
              ON cf.data_id = o.data_id AND cf.date = o.date
             AND cf.feature_id = (SELECT id FROM feature_definitions
                                  WHERE name = 'indicator_sma_200')
            WHERE o.close > 0 AND cf.value > 0 AND o.date > %(start)s
            GROUP BY o.date
            HAVING COUNT(*) >= %(min_stocks)s
        """,
    },
    "dispersion_20": {
        "description": ("Dispersion: cross-sectional std of 20-day returns "
                        "(when stocks move together, selection can't matter)"),
        "sql": """
            WITH r AS (
                SELECT o.date, o.data_id,
                       o.close / NULLIF(LAG(o.close, 20) OVER (
                           PARTITION BY o.data_id ORDER BY o.date), 0) - 1 AS ret
                FROM stock_ohlcv o
                JOIN stocks s ON s.id = o.data_id AND s.asset_type = 'Stock'
                WHERE o.close > 0
            )
            SELECT date, STDDEV_POP(ret)::float
            FROM r
            WHERE ret IS NOT NULL AND date > %(start)s
            GROUP BY date
            HAVING COUNT(*) >= %(min_stocks)s
        """,
    },
}


class MacroDeriveError(ValueError):
    """Unknown derived series or unusable inputs."""


def ensure_macro_function(conn, name: str, description: str) -> None:
    """Register the macro function NAME in the function registry.

    Macro features are materialized by gefion.macro code paths, not the
    feature-function dispatcher — but the registry must still know every
    function name in use, or feat-def-validate/fix (the janitor) would
    treat macro features as orphans and deactivate them."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions
                   (name, version, status, enabled, description, language,
                    function_body)
               VALUES (%s, 'v1', 'active', TRUE, %s, 'python',
                       '# materialized by gefion.macro — not dispatched')
               ON CONFLICT DO NOTHING""",
            (name, description))


def derive_series(conn, name: str, min_stocks: int = 100,
                  full: bool = False) -> int:
    """Compute one derived macro series and upsert it into the feature store.

    Returns the number of NEW rows written (0 when already current —
    idempotent and incremental). `full` recomputes from the beginning
    (values are pure functions of the cross-section, so this is safe)."""
    from gefion.db.ingest import ensure_feature_definitions

    spec = DERIVED_SERIES.get(name)
    if spec is None:
        raise MacroDeriveError(
            f"unknown derived series {name!r} — available: "
            f"{sorted(DERIVED_SERIES)}")

    with create_span("macro.derived.derive", series=name,
                     min_stocks=min_stocks) as span:
        ensure_macro_function(
            conn, "macro_derived",
            "Derived macro series (breadth/dispersion) — see gefion.macro.derived")
        series_id = catalog.ensure_series(
            conn, name=name, provider="derived", kind="derived",
            cadence="daily", description=spec["description"])
        feature_name = f"macro_{name}"
        ids = ensure_feature_definitions(conn, [{
            "name": feature_name, "function_name": "macro_derived",
            "params": None, "source_table": "stock_ohlcv",
            "source_column": "close", "store_table": "computed_features",
            "store_column": "value", "store_type": "double precision",
            "active": True, "entity_table": "macro_series",
        }])
        feature_id = ids[feature_name]

        with conn.cursor() as cur:
            if full:
                start = None
            else:
                cur.execute("SELECT max(date) FROM computed_features "
                            "WHERE feature_id = %s AND data_id = %s",
                            (feature_id, series_id))
                start = cur.fetchone()[0]
            cur.execute(
                f"""INSERT INTO computed_features (data_id, date, feature_id, value)
                    SELECT %(series_id)s, q.date, %(feature_id)s, q.value
                    FROM ({spec['sql']}) AS q(date, value)
                    ON CONFLICT DO NOTHING""",
                {"series_id": series_id, "feature_id": feature_id,
                 "min_stocks": min_stocks,
                 "start": start or __import__("datetime").date(1900, 1, 1)},
            )
            written = cur.rowcount
        conn.commit()
        set_attributes(span, rows_written=written)
        return written
