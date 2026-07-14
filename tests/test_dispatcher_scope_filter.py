"""Per-stock sweep must skip non-stock-scope functions (#120 follow-on).

TDD: written FIRST. The per-stock dispatcher sweep selected every active
feature_definition regardless of scope, so all market-scope functions (18 on
prod) plus the ingest-side marker rows (macro_value, model_prediction) were
attempted — and failed — once per symbol per pass: ~124k futile attempts per
nightly pass, each paying a source-data fetch first, plus ~20k warnings of
log spam. Three scopes now exist:

- 'stock'        — dispatched per symbol (the only sweep-eligible scope)
- 'market'       — dispatched per date over the cross-section (spec 011)
- 'materialized' — never dispatched; values are written by their own
                   pipeline (macro ingest/derive, ml predict). The registry
                   row exists so every function name in use is known.
"""
import datetime as dt
import os
import warnings

import psycopg
import pytest

from gefion.db import schema

D = dt.date


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


def _cleanup(cur):
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE '%qsf%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE '%qsf%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qsf%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'QSF%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QSF%'")


@pytest.fixture
def world():
    """One stock with 10 days of bars, plus one function per scope."""
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) "
                    "VALUES ('QSF1', 'Stock') RETURNING id")
        sid = cur.fetchone()[0]
        base = D(2024, 1, 1)
        for i in range(10):
            close = 100.0 + i
            cur.execute(
                """INSERT INTO stock_ohlcv (data_id, date, open, high, low,
                   close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)
                   ON CONFLICT DO NOTHING""",
                (sid, base + dt.timedelta(days=i), close, close, close, close))

        # market-scope function + its macro_ definition (spec 011 shape)
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, scope)
               VALUES ('qsf_market_fn', 'v1', 'active', TRUE, 'python',
                       'def market(rows): return 1.0', 'market')""")
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name,
                   entity_table, source_table, source_column, active)
               VALUES ('macro_qsf_market', 'qsf_market_fn', 'macro_series',
                       'stock_ohlcv', 'close', TRUE)""")

        # materialized marker + macro-series definition (macro_value shape)
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, scope)
               VALUES ('qsf_marker', 'v1', 'active', TRUE, 'python',
                       '# materialized by gefion.macro — not dispatched',
                       'materialized')""")
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name,
                   entity_table, source_table, source_column, active)
               VALUES ('macro_qsf_vix', 'qsf_marker', 'macro_series',
                       'macro_series_values', 'value', TRUE)""")

        # materialized marker + per-STOCK definition (model_prediction shape:
        # entity_table='stocks', values written by ml predict, never dispatched)
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, scope)
               VALUES ('qsf_pred_marker', 'v1', 'active', TRUE, 'python',
                       '# materialized by gefion.ml — not dispatched',
                       'materialized')""")
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name,
                   entity_table, source_table, source_column, active)
               VALUES ('pred_qsf_q50', 'qsf_pred_marker', 'stocks',
                       'predictions', NULL, TRUE)""")

        # honest stock-scope DB function — must still be swept
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, scope)
               VALUES ('qsf_stock_fn', 'v1', 'active', TRUE, 'python',
                       'def compute(rows, specs):\n'
                       '    return [{"date": r["date"], '
                       '"qsf_close_copy": float(r["close"])} for r in rows]',
                       'stock')""")
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name, params,
                   entity_table, source_table, source_column, store_table,
                   store_column, active)
               VALUES ('qsf_close_copy', 'qsf_stock_fn', '{}'::jsonb,
                       'stocks', 'stock_ohlcv', 'close', 'computed_features',
                       'value', TRUE)""")
    yield c, sid
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def test_schema_allows_materialized_scope(world):
    conn, _ = world
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, scope)
               VALUES ('qsf_scope_probe', 'v1', 'active', TRUE, 'python',
                       '# marker', 'materialized')""")
        cur.execute("SELECT scope FROM feature_functions "
                    "WHERE name = 'qsf_scope_probe'")
        assert cur.fetchone()[0] == "materialized"


def test_stock_sweep_skips_market_and_materialized(world):
    from gefion.features.dispatcher import compute_features
    conn, sid = world
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = compute_features(conn, sid, incremental=False)
    # non-stock scopes never enter the sweep — no attempts, no errors
    assert "qsf_market_fn" not in result
    assert "qsf_marker" not in result
    assert "qsf_pred_marker" not in result
    qsf_warnings = [str(w.message) for w in caught if "qsf" in str(w.message)]
    assert qsf_warnings == []
    # the stock-scope function still computes
    assert result["qsf_stock_fn"]["inserted"] == 10
    assert result["qsf_stock_fn"]["errors"] == []


def test_ensure_materialized_function_registers_marker(world):
    from gefion.macro.derived import ensure_materialized_function
    conn, _ = world
    ensure_materialized_function(conn, "qsf_ensured", "test marker")
    ensure_materialized_function(conn, "qsf_ensured", "test marker")  # idempotent
    with conn.cursor() as cur:
        cur.execute("SELECT scope, count(*) OVER () FROM feature_functions "
                    "WHERE name = 'qsf_ensured'")
        scope, n = cur.fetchone()
    assert scope == "materialized"
    assert n == 1


def test_migration_file_retags_prod_markers():
    """The migration must retag the two prod ghost rows and register the
    model_prediction marker (prod has pred_* definitions with no function
    row, which the sweep attempted and errored per symbol)."""
    import pathlib
    migrations = pathlib.Path(__file__).parent.parent / "sql" / "migrations"
    files = sorted(migrations.glob("*materialized*.sql"))
    assert files, "no materialized-scope migration found in sql/migrations"
    text = files[-1].read_text()
    for needle in ("macro_value", "macro_derived", "model_prediction",
                   "materialized"):
        assert needle in text, f"migration does not handle {needle}"
