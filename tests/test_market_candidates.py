"""Candidate store + gate tests for generated market features (014).

TDD: written FIRST. The gate invariant is structural: candidates live in
market_function_candidates, never in feature_functions, so pending/rejected
generated code has no execution path. These tests cover the owner-approved
schema (T001), the store primitives (T004), and the review gate + atomic
promotion (T007).
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
    schema.create_market_function_candidates_table(c)

    def _cleanup(cur):
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'mfc_test_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mfc_test_%'")
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'mfc_test_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


# --- T001: owner-approved schema (DDL approved 2026-07-18) -------------------------

class TestCandidateSchema:
    def test_table_exists_with_expected_columns(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'market_function_candidates'"
            )
            cols = {r[0] for r in cur.fetchall()}
        expected = {
            "id", "name", "version", "kind", "function_body", "inputs",
            "description", "origin", "principle_id", "generator", "dry_run",
            "review_state", "reviewed_by", "reviewed_at", "review_reason",
            "promoted_function_id", "created_at",
        }
        assert expected.issubset(cols), f"missing columns: {expected - cols}"

    def test_creator_is_idempotent(self, conn):
        schema.create_market_function_candidates_table(conn)
        schema.create_market_function_candidates_table(conn)

    def test_kind_and_state_constrained(self, conn):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO market_function_candidates "
                    "(name, kind, function_body, origin) "
                    "VALUES ('mfc_test_bad', 'per_stock', 'def compute(rows): pass', 'template')"
                )
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO market_function_candidates "
                    "(name, kind, function_body, origin, review_state) "
                    "VALUES ('mfc_test_bad', 'cross_section', 'x', 'template', 'maybe')"
                )

    def test_name_version_unique(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO market_function_candidates "
                "(name, version, kind, function_body, origin) "
                "VALUES ('mfc_test_uq', 1, 'cross_section', 'x', 'template')"
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO market_function_candidates "
                    "(name, version, kind, function_body, origin) "
                    "VALUES ('mfc_test_uq', 1, 'cross_section', 'y', 'template')"
                )

    def test_schema_sql_carries_the_table(self):
        """Two-file rule: schema.sql is the canonical DDL."""
        from pathlib import Path
        import gefion
        root = Path(gefion.__file__).parent.parent.parent
        assert "market_function_candidates" in (root / "sql" / "schema.sql").read_text()
        migrations = list((root / "sql" / "migrations").glob("*market_function_candidates*"))
        assert migrations, "migration file missing (two-file rule)"


# --- T004: store primitives --------------------------------------------------------

class TestCandidateStore:
    def _create(self, conn, **kw):
        from gefion.macro import candidates
        defaults = dict(
            name="mfc_test_breadth", kind="cross_section",
            function_body="def compute(rows):\n    return float(len(rows))",
            inputs={}, origin="template", principle_id="p-test",
            generator="test", description="test candidate")
        defaults.update(kw)
        return candidates.create_candidate(conn, **defaults)

    def test_create_records_provenance_and_pending_state(self, conn):
        cid = self._create(conn)
        from gefion.macro import candidates
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "pending"
        assert c["origin"] == "template"
        assert c["principle_id"] == "p-test"
        assert c["generator"] == "test"
        assert c["version"] == 1
        assert c["created_at"] is not None

    def test_same_name_bumps_version_never_overwrites(self, conn):
        from gefion.macro import candidates
        cid1 = self._create(conn)
        cid2 = self._create(conn, function_body="def compute(rows):\n    return 1.0")
        c1, c2 = candidates.get_candidate(conn, cid1), candidates.get_candidate(conn, cid2)
        assert (c1["version"], c2["version"]) == (1, 2)
        assert c1["function_body"] != c2["function_body"]  # both retained

    def test_list_filters_by_state_newest_first(self, conn):
        from gefion.macro import candidates
        self._create(conn, name="mfc_test_a")
        self._create(conn, name="mfc_test_b")
        pending = candidates.list_candidates(conn, state="pending")
        names = [c["name"] for c in pending]
        assert names.index("mfc_test_b") < names.index("mfc_test_a")
        assert candidates.list_candidates(conn, state="rejected") == []

    def test_record_dry_run_stores_jsonb(self, conn):
        from gefion.macro import candidates
        cid = self._create(conn)
        candidates.record_dry_run(conn, cid, {"ok": True, "sample": [], "error": None,
                                              "seed": 42, "ran_at": "2026-07-18T00:00:00"})
        c = candidates.get_candidate(conn, cid)
        assert c["dry_run"]["ok"] is True and c["dry_run"]["seed"] == 42


# --- T007: the gate — approve/reject/promote + SC-1401 -----------------------------

class TestReviewGate:
    def _pending(self, conn, name="mfc_test_gate", ok=True, **kw):
        from gefion.macro import candidates
        cid = candidates.create_candidate(
            conn, name=name, kind="cross_section",
            function_body="def compute(rows):\n    return float(len(rows))",
            origin="template", principle_id="p-test", generator="test", **kw)
        candidates.record_dry_run(conn, cid, {
            "ok": ok, "sample": [], "error": None if ok else "sandbox refusal",
            "seed": 42, "ran_at": "2026-07-18T00:00:00"})
        return cid

    def test_approve_refuses_failed_dry_run(self, conn):
        from gefion.macro import candidates
        cid = self._pending(conn, ok=False)
        with pytest.raises(ValueError, match="dry-run"):
            candidates.approve_candidate(conn, cid, approver="simon")
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_approve_refuses_missing_dry_run(self, conn):
        from gefion.macro import candidates
        cid = candidates.create_candidate(
            conn, name="mfc_test_nodry", kind="cross_section",
            function_body="def compute(rows):\n    return 1.0", origin="template")
        with pytest.raises(ValueError, match="dry-run"):
            candidates.approve_candidate(conn, cid, approver="simon")

    def test_approve_promotes_atomically(self, conn):
        from gefion.macro import candidates
        cid = self._pending(conn, name="mfc_test_promote")
        fid = candidates.approve_candidate(conn, cid, approver="simon")
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "approved"
        assert c["reviewed_by"] == "simon" and c["reviewed_at"] is not None
        assert c["promoted_function_id"] == fid
        with conn.cursor() as cur:
            cur.execute("SELECT scope, status, enabled FROM feature_functions "
                        "WHERE id = %s", (fid,))
            scope, status, enabled = cur.fetchone()
            assert (scope, status, enabled) == ("market", "active", True)
            # zero orphans: the paired macro-home definition exists
            cur.execute("SELECT entity_table, function_name FROM feature_definitions "
                        "WHERE name = %s", ("macro_mfc_test_promote",))
            row = cur.fetchone()
            assert row == ("macro_series", "mfc_test_promote")

    def test_approve_refuses_name_collision(self, conn):
        from gefion.macro import candidates
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feature_functions (name, version, status, enabled, "
                "language, function_body, scope) VALUES "
                "('mfc_test_taken', 'v1', 'active', TRUE, 'python', 'x', 'market')")
        cid = self._pending(conn, name="mfc_test_taken")
        with pytest.raises(ValueError, match="mfc_test_taken"):
            candidates.approve_candidate(conn, cid, approver="simon")
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_reject_requires_reason_and_is_terminal(self, conn):
        from gefion.macro import candidates
        cid = self._pending(conn, name="mfc_test_reject")
        with pytest.raises(ValueError, match="reason"):
            candidates.reject_candidate(conn, cid, approver="simon", reason="")
        candidates.reject_candidate(conn, cid, approver="simon",
                                    reason="duplicates breadth_sma200")
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "rejected"
        assert c["review_reason"] == "duplicates breadth_sma200"
        with pytest.raises(ValueError, match="pending"):
            candidates.approve_candidate(conn, cid, approver="simon")
        with pytest.raises(ValueError, match="pending"):
            candidates.reject_candidate(conn, cid, approver="simon", reason="again")

    def test_candidates_cannot_derive_sc1401(self, conn):
        """SC-1401: pending AND rejected candidates yield zero stored values
        through derive — incremental and full recompute both refuse, naming
        the gate."""
        from gefion.macro import candidates, derived
        cid = self._pending(conn, name="mfc_test_locked")
        for full in (False, True):
            with pytest.raises(derived.MacroDeriveError, match="candidate"):
                derived.derive_series(conn, "mfc_test_locked", full=full)
        candidates.reject_candidate(conn, cid, approver="simon", reason="no")
        with pytest.raises(derived.MacroDeriveError, match="candidate"):
            derived.derive_series(conn, "mfc_test_locked")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_definitions "
                        "WHERE name = 'macro_mfc_test_locked'")
            assert cur.fetchone()[0] == 0
