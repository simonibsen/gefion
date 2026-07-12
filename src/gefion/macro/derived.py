"""Derived macro series orchestration (spec 011, epic #114).

Market-level function bodies live IN THE DATABASE (feature_functions,
scope='market') and execute through the standard sandboxed dispatcher —
one per date over the stock cross-section. This module only orchestrates:
seed (create-if-absent), wire (series row + feature definition), stream,
store. The database body is the source of truth; `reseed_function` is the
single explicit path that overwrites it from the repo seeds.

Honesty: thin days (< min_stocks) never reach a body (gap, not garbage);
a failing body writes NOTHING (write-on-success, isolated per function);
recomputation is idempotent and incremental.
"""
import json
from typing import Any, Dict, Optional

from gefion.observability import create_span, set_attributes

from . import catalog
from .market_bodies import SEED_BODIES


class MacroDeriveError(ValueError):
    """Unknown derived series or unusable configuration."""


def ensure_macro_function(conn, name: str, description: str) -> None:
    """Register an ingest-side macro function name (e.g. macro_value) so the
    registry knows every function name in use (janitor safety)."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions
                   (name, version, status, enabled, description, language,
                    function_body, scope)
               VALUES (%s, 'v1', 'active', TRUE, %s, 'python',
                       '# materialized by gefion.macro — not dispatched',
                       'stock')
               ON CONFLICT DO NOTHING""",
            (name, description))


def _seed_function(conn, name: str) -> None:
    """Plant the seed body if absent. NEVER overwrites (DB wins)."""
    spec = SEED_BODIES[name]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions
                   (name, version, status, enabled, description, language,
                    function_body, inputs, scope)
               VALUES (%s, 'v1', 'active', TRUE, %s, 'python', %s, %s, 'market')
               ON CONFLICT DO NOTHING""",
            (name, spec["description"], spec["body"],
             json.dumps(spec["inputs"])))


def reseed_function(conn, name: str) -> None:
    """EXPLICITLY overwrite one DB body from the repo seed — the loud
    recovery path for a mangled body. Never called implicitly."""
    if name not in SEED_BODIES:
        raise MacroDeriveError(
            f"unknown derived series {name!r} — available: "
            f"{sorted(SEED_BODIES)}")
    spec = SEED_BODIES[name]
    with create_span("macro.derived.reseed", series=name):
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE feature_functions
                   SET function_body = %s, inputs = %s, scope = 'market',
                       enabled = TRUE
                   WHERE name = %s""",
                (spec["body"], json.dumps(spec["inputs"]), name))
            if cur.rowcount == 0:
                _seed_function(conn, name)
        conn.commit()


def _get_market_function(conn, name: str) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, function_body, inputs, enabled
               FROM feature_functions WHERE name = %s AND scope = 'market'""",
            (name,))
        r = cur.fetchone()
    if r is None:
        return None
    inputs = r[3]
    if isinstance(inputs, str):
        inputs = json.loads(inputs)
    return {"id": r[0], "name": r[1], "function_body": r[2],
            "inputs": inputs, "enabled": r[4]}


def derive_series(conn, name: str, min_stocks: int = 100,
                  full: bool = False) -> int:
    """Compute one derived series via its DATABASE body. Returns new rows
    written (0 when current). Raises MarketFunctionError on body failure
    (nothing written for it), MacroDeriveError on unknown series, and
    returns -1 for a disabled function (caller reports the skip)."""
    from gefion.db.ingest import ensure_feature_definitions

    if name not in SEED_BODIES and _get_market_function(conn, name) is None:
        raise MacroDeriveError(
            f"unknown derived series {name!r} — available: "
            f"{sorted(SEED_BODIES)}")

    with create_span("macro.derived.derive", series=name,
                     min_stocks=min_stocks) as span:
        if name in SEED_BODIES:
            _seed_function(conn, name)
        fn = _get_market_function(conn, name)
        if fn is None:
            raise MacroDeriveError(
                f"{name!r} exists but is not a market-scope function")
        if not fn["enabled"]:
            set_attributes(span, skipped_disabled=True)
            return -1

        series_id = catalog.ensure_series(
            conn, name=name, provider="derived", kind="derived",
            cadence="daily",
            description=SEED_BODIES.get(name, {}).get("description"))
        feature_name = f"macro_{name}"
        ids = ensure_feature_definitions(conn, [{
            "name": feature_name, "function_name": name,
            "params": None, "source_table": "stock_ohlcv",
            "source_column": "close", "store_table": "computed_features",
            "store_column": "value", "store_type": "double precision",
            "active": True, "entity_table": "macro_series",
        }])
        feature_id = ids[feature_name]
        with conn.cursor() as cur:
            # migrate pre-011 wiring (function_name was 'macro_derived')
            cur.execute("UPDATE feature_definitions SET function_name = %s "
                        "WHERE name = %s AND function_name <> %s",
                        (name, feature_name, name))
            if full:
                start = None
            else:
                cur.execute("SELECT max(date) FROM computed_features "
                            "WHERE feature_id = %s AND data_id = %s",
                            (feature_id, series_id))
                start = cur.fetchone()[0]

        from gefion.features.dispatcher import run_market_function
        result = run_market_function(conn, fn, start=start,
                                     min_stocks=min_stocks)

        written = 0
        if result["values"]:
            # incremental: append-only (DO NOTHING). --full: a deliberate
            # recompute of history — the (possibly edited) body's output
            # REPLACES stored values (that is what "recompute" means).
            conflict = ("DO UPDATE SET value = EXCLUDED.value" if full
                        else "DO NOTHING")
            with conn.cursor() as cur:
                cur.executemany(
                    f"""INSERT INTO computed_features
                           (data_id, date, feature_id, value)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (data_id, feature_id, date) {conflict}""",
                    [(series_id, d, feature_id, v)
                     for d, v in result["values"]],
                )
                written = cur.rowcount
        conn.commit()
        set_attributes(span, rows_written=written, gaps=result["gaps"])
        return written
