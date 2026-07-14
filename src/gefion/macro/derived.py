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


def ensure_materialized_function(conn, name: str, description: str,
                                 materialized_by: str = "gefion.macro") -> None:
    """Register a marker row for a function whose values are written by its
    own pipeline (macro ingest/derive, ml predict) so the registry knows
    every function name in use (janitor safety). scope='materialized' keeps
    it out of BOTH dispatch paths — the per-stock sweep and `derive`."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions
                   (name, version, status, enabled, description, language,
                    function_body, scope)
               VALUES (%s, 'v1', 'active', TRUE, %s, 'python',
                       %s, 'materialized')
               ON CONFLICT DO NOTHING""",
            (name, description,
             f"# materialized by {materialized_by} — not dispatched"))


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


def all_derived_series(conn) -> list:
    """Every derivable series name: repo SEED_BODIES plus every
    scope='market' function in the DATABASE (spec 013, R4) — the DB is the
    source of truth, so 'all' can never silently exclude a seeded series
    (sector, model, future). Disabled functions are INCLUDED so the derive
    skip path reports them — a kill switch should be visible, not silent."""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions WHERE scope = 'market'")
        db_names = {r[0] for r in cur.fetchall()}
    return sorted(set(SEED_BODIES) | db_names)


def seed_sector_functions(conn, sectors: Optional[list] = None,
                          min_members: int = 100,
                          body_floor: int = 30) -> Dict[str, Any]:
    """Seed generated sector bodies create-if-absent (spec 013).

    Census from stocks.sector (asset_type='Stock', NULL excluded). Sectors
    under `min_members` members are skipped-and-reported; `sectors`
    restricts to named ones and unknown names refuse listing the census;
    two sectors mapping to one slug refuse loudly (never merged). The DB
    body wins on re-run — this door never overwrites an operator edit.
    """
    from .market_bodies import sector_signal_bodies, sector_slug

    with create_span("macro.derived.seed_sectors",
                     min_members=min_members) as span:
        with conn.cursor() as cur:
            cur.execute("SELECT sector, count(*) FROM stocks "
                        "WHERE asset_type = 'Stock' AND sector IS NOT NULL "
                        "GROUP BY sector ORDER BY sector")
            census = {r[0]: r[1] for r in cur.fetchall()}
        if sectors is not None:
            unknown = [x for x in sectors if x not in census]
            if unknown:
                raise MacroDeriveError(
                    f"unknown sector(s) {unknown} — census: "
                    f"{sorted(census)}")
            census = {k: v for k, v in census.items() if k in sectors}
        slugs: Dict[str, str] = {}
        for sector in census:
            slug = sector_slug(sector)
            if slug in slugs:
                raise MacroDeriveError(
                    f"sectors {slugs[slug]!r} and {sector!r} both slug to "
                    f"{slug!r} — refusing to merge distinct sectors")
            slugs[slug] = sector
        seeded, existing, skipped_thin = [], [], {}
        with conn.cursor() as cur:
            for sector, members in sorted(census.items()):
                if members < min_members:
                    skipped_thin[sector] = members
                    continue
                for name, spec in sector_signal_bodies(
                        sector, min_members=body_floor).items():
                    cur.execute(
                        """INSERT INTO feature_functions
                               (name, version, status, enabled, description,
                                language, function_body, inputs, scope)
                           VALUES (%s, 'v1', 'active', TRUE, %s, 'python',
                                   %s, %s, 'market')
                           ON CONFLICT DO NOTHING""",
                        (name, spec["description"], spec["body"],
                         json.dumps(spec["inputs"])))
                    (seeded if cur.rowcount else existing).append(name)
        conn.commit()
        set_attributes(span, seeded=len(seeded), existing=len(existing),
                       skipped=len(skipped_thin))
        return {"seeded": seeded, "existing": existing,
                "skipped_thin": skipped_thin,
                "census": census}


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
