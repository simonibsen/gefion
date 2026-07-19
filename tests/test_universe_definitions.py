"""Universe definitions (015, US1/US2 foundations).

TDD: written FIRST. A universe is a named, rule-defined subset of the stock
population — the entity-space sibling of a regime definition. Rules are
generic attribute/operator/value predicates validated against a declared
attribute registry; matching an exclude rule excludes. Definitions carry a
content fingerprint (canonical-JSON sha256) so results can record exactly
which population they were measured on.
"""
import os

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


def _cleanup(c):
    with c.cursor() as cur:
        cur.execute("DELETE FROM universe_definitions WHERE name LIKE 'qut_%'")
        # test DB only: reset the seeded default so seed tests are order-free
        cur.execute("DELETE FROM universe_definitions WHERE name = 'modeling_default'")


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_universe_definitions_table(c)
    schema.create_universe_exclusions_table(c)
    _cleanup(c)
    yield c
    _cleanup(c)
    c.close()


RULE_SHELLS = {"name": "no-shells", "attribute": "industry", "op": "eq",
               "value": "SHELL COMPANIES", "reason": "cash boxes"}
RULE_ETFS = {"name": "no-etfs", "attribute": "asset_type", "op": "eq",
             "value": "ETF", "reason": "funds, not companies"}


# --- schema (owner-approved DDL 2026-07-19) ---------------------------------

class TestSchema:
    def test_tables_exist_with_expected_columns(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'universe_definitions'")
            defs = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'universe_exclusions'")
            excl = {r[0] for r in cur.fetchall()}
        assert {"id", "name", "description", "rules", "pins", "fingerprint",
                "is_default", "enabled", "created_at",
                "updated_at"}.issubset(defs)
        assert {"id", "universe_id", "data_id", "rule_name", "excluded_from",
                "excluded_to", "refreshed_at"}.issubset(excl)

    def test_creators_idempotent(self, conn):
        schema.create_universe_definitions_table(conn)
        schema.create_universe_exclusions_table(conn)

    def test_at_most_one_default(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe_definitions (name, rules, fingerprint, is_default) "
                "VALUES ('qut_d1', '[]', 'f1', TRUE)")
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO universe_definitions (name, rules, fingerprint, is_default) "
                    "VALUES ('qut_d2', '[]', 'f2', TRUE)")


# --- fingerprint (pure, no DB) ----------------------------------------------

class TestFingerprint:
    def test_stable_under_key_and_list_order(self):
        from gefion.universe.definitions import compute_fingerprint
        a = compute_fingerprint([RULE_SHELLS, RULE_ETFS], [])
        b = compute_fingerprint(
            [dict(reversed(list(RULE_ETFS.items()))), RULE_SHELLS], [])
        assert a == b
        assert a.startswith("sha256:")

    def test_changes_iff_rules_or_pins_change(self):
        from gefion.universe.definitions import compute_fingerprint
        base = compute_fingerprint([RULE_SHELLS], [])
        assert compute_fingerprint([RULE_SHELLS], []) == base
        assert compute_fingerprint([RULE_ETFS], []) != base
        assert compute_fingerprint(
            [RULE_SHELLS],
            [{"symbol": "QUTX", "action": "include", "reason": "r"}]) != base


# --- definition CRUD + validation -------------------------------------------

class TestDefinitions:
    def test_define_get_list_roundtrip(self, conn):
        from gefion.universe.definitions import (define_universe, get_universe,
                                                 list_universes)
        row = define_universe(conn, "qut_main", description="test",
                              rules=[RULE_SHELLS, RULE_ETFS])
        assert row["fingerprint"].startswith("sha256:")
        got = get_universe(conn, "qut_main")
        assert got["description"] == "test"
        assert {r["name"] for r in got["rules"]} == {"no-shells", "no-etfs"}
        assert "qut_main" in [u["name"] for u in list_universes(conn)]

    def test_update_recomputes_fingerprint(self, conn):
        from gefion.universe.definitions import define_universe
        f1 = define_universe(conn, "qut_up", rules=[RULE_SHELLS])["fingerprint"]
        f2 = define_universe(conn, "qut_up",
                             rules=[RULE_SHELLS, RULE_ETFS])["fingerprint"]
        assert f1 != f2

    def test_unknown_attribute_refused_naming_valid(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        bad = dict(RULE_SHELLS, attribute="favorite_color")
        with pytest.raises(UniverseValidationError) as exc:
            define_universe(conn, "qut_bad", rules=[bad])
        assert "industry" in str(exc.value)  # names valid attributes

    def test_unknown_op_refused_naming_valid(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        bad = dict(RULE_SHELLS, op="sounds_like")
        with pytest.raises(UniverseValidationError) as exc:
            define_universe(conn, "qut_bad", rules=[bad])
        assert "eq" in str(exc.value)

    def test_numeric_op_on_categorical_attribute_refused(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        bad = {"name": "n", "attribute": "industry", "op": "lt",
               "value": 3, "reason": "r"}
        with pytest.raises(UniverseValidationError):
            define_universe(conn, "qut_bad", rules=[bad])

    def test_missing_reason_refused(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        bad = {k: v for k, v in RULE_SHELLS.items() if k != "reason"}
        with pytest.raises(UniverseValidationError):
            define_universe(conn, "qut_bad", rules=[bad])

    def test_duplicate_rule_names_refused(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        with pytest.raises(UniverseValidationError):
            define_universe(conn, "qut_bad",
                            rules=[RULE_SHELLS, dict(RULE_ETFS, name="no-shells")])

    def test_reserved_name_all_refused(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        with pytest.raises(UniverseValidationError):
            define_universe(conn, "all", rules=[RULE_SHELLS])

    def test_pin_validation(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe)
        with pytest.raises(UniverseValidationError):
            define_universe(conn, "qut_bad", rules=[],
                            pins=[{"symbol": "QUTX", "action": "obliterate",
                                   "reason": "r"}])
        with pytest.raises(UniverseValidationError):
            define_universe(conn, "qut_bad", rules=[],
                            pins=[{"symbol": "QUTX", "action": "exclude"}])

    def test_enable_disable_and_default_protection(self, conn):
        from gefion.universe.definitions import (UniverseValidationError,
                                                 define_universe, get_universe,
                                                 set_enabled)
        define_universe(conn, "qut_tog", rules=[RULE_SHELLS])
        set_enabled(conn, "qut_tog", False)
        assert get_universe(conn, "qut_tog")["enabled"] is False
        define_universe(conn, "qut_def", rules=[RULE_SHELLS], is_default=True)
        try:
            with pytest.raises(UniverseValidationError):
                set_enabled(conn, "qut_def", False)
        finally:
            with conn.cursor() as cur:  # don't leave a qut_ default behind
                cur.execute("DELETE FROM universe_definitions WHERE name = 'qut_def'")


# --- default seed (db-init reference data) ----------------------------------

class TestSeed:
    def test_seed_default_universe_idempotent(self, conn):
        from gefion.universe.definitions import (get_universe,
                                                 seed_default_universe)
        seed_default_universe(conn)
        seed_default_universe(conn)  # idempotent
        u = get_universe(conn, "modeling_default")
        assert u is not None and u["is_default"] and u["enabled"]
        rules = {r["name"]: r for r in u["rules"]}
        assert rules["no-shell-companies"]["value"] == "SHELL COMPANIES"
        assert rules["no-etfs"]["value"] == "ETF"

    def test_seed_respects_existing_owner_edits(self, conn):
        """Re-seeding must never clobber an owner-edited default universe."""
        from gefion.universe.definitions import (define_universe, get_universe,
                                                 seed_default_universe)
        seed_default_universe(conn)
        edited = get_universe(conn, "modeling_default")
        new_rules = edited["rules"] + [dict(RULE_SHELLS, name="qut-extra")]
        define_universe(conn, "modeling_default", rules=new_rules)
        seed_default_universe(conn)
        after = get_universe(conn, "modeling_default")
        assert any(r["name"] == "qut-extra" for r in after["rules"])
