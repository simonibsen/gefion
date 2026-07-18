"""Composite market functions — macro-of-macro (014 US2, T018).

TDD: written FIRST. A composite is a market function whose declared inputs
are named macro series ({"series": [...]}): per date it receives that date's
stored values and returns one value or a gap. Same sandbox, same honesty
rules as 011; cycles refuse at the door; derive orders composites after
their inputs.
"""
import os
from datetime import date

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()

    def _cleanup(cur):
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_qmc_%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_qmc_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qmc_%'")
        cur.execute("DELETE FROM macro_series_values WHERE series_id IN "
                    "(SELECT id FROM macro_series WHERE name LIKE 'qmc_%')")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'qmc_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _seed_series(conn, name, values):
    """values: {date: float}"""
    from gefion.macro import catalog
    sid = catalog.ensure_series(conn, name=name, provider="derived",
                                kind="derived", cadence="daily")
    with conn.cursor() as cur:
        for d, v in values.items():
            cur.execute("INSERT INTO macro_series_values (series_id, date, value) "
                        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (sid, d, v))
    return sid


D1, D2, D3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)

SUM_BODY = ("def compute(row):\n"
            "    return row['qmc_a'] + row['qmc_b']\n")


class TestCompositeExecutor:
    def _fn(self, body=SUM_BODY, series=("qmc_a", "qmc_b"), name="qmc_sum"):
        return {"id": 999999, "name": name, "function_body": body,
                "inputs": {"series": list(series)}, "enabled": True}

    def test_values_from_exactly_that_dates_inputs(self, conn):
        from gefion.features.dispatcher import run_composite_function
        _seed_series(conn, "qmc_a", {D1: 1.0, D2: 2.0})
        _seed_series(conn, "qmc_b", {D1: 10.0, D2: 20.0})
        result = run_composite_function(conn, self._fn())
        assert result["values"] == [(D1, 11.0), (D2, 22.0)]
        assert result["gaps"] == 0

    def test_missing_input_date_is_gap_never_imputed(self, conn):
        from gefion.features.dispatcher import run_composite_function
        _seed_series(conn, "qmc_a", {D1: 1.0, D2: 2.0, D3: 3.0})
        _seed_series(conn, "qmc_b", {D1: 10.0, D3: 30.0})   # D2 missing
        result = run_composite_function(conn, self._fn())
        assert result["values"] == [(D1, 11.0), (D3, 33.0)]
        assert result["gaps"] == 1

    def test_none_and_nan_are_gaps(self, conn):
        from gefion.features.dispatcher import run_composite_function
        _seed_series(conn, "qmc_a", {D1: 1.0, D2: 2.0})
        _seed_series(conn, "qmc_b", {D1: 1.0, D2: 2.0})
        body = ("def compute(row):\n"
                "    if row['qmc_a'] < 2:\n"
                "        return None\n"
                "    return float('nan')\n")
        result = run_composite_function(conn, self._fn(body=body))
        assert result["values"] == []
        assert result["gaps"] == 2

    def test_wrong_shape_and_raise_are_errors(self, conn):
        from gefion.features.dispatcher import (MarketFunctionError,
                                                run_composite_function)
        _seed_series(conn, "qmc_a", {D1: 1.0})
        _seed_series(conn, "qmc_b", {D1: 1.0})
        with pytest.raises(MarketFunctionError, match="float"):
            run_composite_function(
                conn, self._fn(body="def compute(row):\n    return 'hi'\n"))
        with pytest.raises(MarketFunctionError, match="boom"):
            run_composite_function(
                conn, self._fn(body="def compute(row):\n    raise ValueError('boom')\n"))

    def test_start_filter_is_incremental(self, conn):
        from gefion.features.dispatcher import run_composite_function
        _seed_series(conn, "qmc_a", {D1: 1.0, D2: 2.0, D3: 3.0})
        _seed_series(conn, "qmc_b", {D1: 1.0, D2: 2.0, D3: 3.0})
        result = run_composite_function(conn, self._fn(), start=D2)
        assert result["values"] == [(D3, 6.0)]


class TestCompositeRegistration:
    def test_register_and_derive_roundtrip(self, conn):
        from gefion.macro import composites, derived
        _seed_series(conn, "qmc_a", {D1: 1.0, D2: 2.0})
        _seed_series(conn, "qmc_b", {D1: 10.0, D2: 20.0})
        composites.register_composite(conn, "qmc_sum", ["qmc_a", "qmc_b"],
                                      SUM_BODY)
        written = derived.derive_series(conn, "qmc_sum")
        assert written == 2
        # idempotent: rerun writes nothing new
        assert derived.derive_series(conn, "qmc_sum") == 0
        # incremental: a new input date derives exactly one new value
        _seed_series(conn, "qmc_a", {D3: 3.0})
        _seed_series(conn, "qmc_b", {D3: 30.0})
        assert derived.derive_series(conn, "qmc_sum") == 1
        # full recompute replaces history
        assert derived.derive_series(conn, "qmc_sum", full=True) >= 3

    def test_unknown_series_refuses_naming_it(self, conn):
        from gefion.macro import composites
        with pytest.raises(ValueError, match="qmc_missing"):
            composites.register_composite(conn, "qmc_bad", ["qmc_missing"],
                                          "def compute(row):\n    return 1.0\n")

    def test_empty_series_refuses(self, conn):
        from gefion.macro import composites
        with pytest.raises(ValueError, match="series"):
            composites.register_composite(conn, "qmc_bad", [],
                                          "def compute(row):\n    return 1.0\n")

    def test_name_collision_refuses(self, conn):
        from gefion.macro import composites
        _seed_series(conn, "qmc_a", {D1: 1.0})
        composites.register_composite(conn, "qmc_dup", ["qmc_a"],
                                      "def compute(row):\n    return row['qmc_a']\n")
        with pytest.raises(ValueError, match="qmc_dup"):
            composites.register_composite(conn, "qmc_dup", ["qmc_a"],
                                          "def compute(row):\n    return 0.0\n")

    def test_cycle_refusal_including_transitive(self, conn):
        from gefion.macro import composites
        _seed_series(conn, "qmc_a", {D1: 1.0})
        # qmc_x consumes qmc_a; qmc_y consumes qmc_x
        composites.register_composite(conn, "qmc_x", ["qmc_a"],
                                      "def compute(row):\n    return row['qmc_a']\n")
        _seed_series(conn, "qmc_x", {D1: 1.0})
        composites.register_composite(conn, "qmc_y", ["qmc_x"],
                                      "def compute(row):\n    return row['qmc_x']\n")
        _seed_series(conn, "qmc_y", {D1: 1.0})
        # direct self-cycle
        with pytest.raises(ValueError, match="cycle"):
            composites.register_composite(conn, "qmc_self", ["qmc_self"],
                                          "def compute(row):\n    return 0.0\n")
        # transitive: qmc_a produced by nothing, but qmc_z -> qmc_y -> qmc_x
        # is fine; a NEW composite that qmc_x transitively feeds and that
        # feeds qmc_x again must refuse. Simulate by attempting to register
        # a composite named qmc_a (which qmc_x consumes) that consumes qmc_y.
        with pytest.raises(ValueError, match="cycle"):
            composites.register_composite(conn, "qmc_a", ["qmc_y"],
                                          "def compute(row):\n    return 0.0\n",
                                          allow_existing_series=True)

    def test_disabled_input_producer_is_reported_skip(self, conn):
        from gefion.macro import composites, derived
        _seed_series(conn, "qmc_a", {D1: 1.0})
        _seed_series(conn, "qmc_b", {D1: 1.0})
        composites.register_composite(conn, "qmc_up", ["qmc_a"],
                                      "def compute(row):\n    return row['qmc_a']\n")
        _seed_series(conn, "qmc_up", {D1: 1.0})
        composites.register_composite(conn, "qmc_down", ["qmc_up", "qmc_b"],
                                      SUM_BODY.replace("qmc_a", "qmc_up")
                                              .replace("qmc_b", "qmc_b"))
        with conn.cursor() as cur:
            cur.execute("UPDATE feature_functions SET enabled = FALSE "
                        "WHERE name = 'qmc_up'")
        # derive of the downstream composite: reported skip, not silence
        assert derived.derive_series(conn, "qmc_down") == -1


class TestDeriveOrdering:
    def test_composites_ordered_after_inputs(self, conn):
        from gefion.macro import composites
        _seed_series(conn, "qmc_a", {D1: 1.0})
        composites.register_composite(conn, "qmc_mid", ["qmc_a"],
                                      "def compute(row):\n    return row['qmc_a']\n")
        _seed_series(conn, "qmc_mid", {D1: 1.0})
        composites.register_composite(conn, "qmc_top", ["qmc_mid"],
                                      "def compute(row):\n    return row['qmc_mid']\n")
        names = ["qmc_top", "qmc_mid", "breadth_sma200"]
        ordered = composites.order_for_derive(conn, names)
        assert ordered.index("breadth_sma200") < ordered.index("qmc_mid")
        assert ordered.index("qmc_mid") < ordered.index("qmc_top")
