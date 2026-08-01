"""Rung-3 autonomy: full auto-promotion + review-after digest (#142).

TDD: written FIRST. Rung 3 is the top of the graduation ladder: EVERY
dry-run-passing candidate auto-promotes through the SOLE door regardless of
origin or generator, attributed reviewed_by='policy:full-auto'. The candidate
ledger becomes a retrospective control — so each full-auto promotion also
emits ONE system_observation (#144 machinery), a standing digest of what the
open gate admitted, idempotent per candidate.

Everything else still holds:
  - a failed or missing dry-run can NEVER promote (refusal invariant);
  - flipping GEFION_FULL_AUTONOMY off returns to the lower rungs / gated with
    no schema change. Fail-closed OFF by default.

Precedence at the door: full-auto ⊃ earned ⊃ template-auto ⊃ everything-gated.
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


_PROMOTE_COLUMNS = (
    ("params", "JSONB"),
    ("source_table", "TEXT"),
    ("source_column", "TEXT"),
    ("store_table", "TEXT"),
    ("store_column", "TEXT"),
    ("store_type", "TEXT"),
    ("active", "BOOLEAN DEFAULT TRUE"),
    ("entity_table", "TEXT NOT NULL DEFAULT 'stocks'"),
)


@pytest.fixture
def conn():
    c = _conn()
    from gefion.db import pool as db_pool
    schema.create_feature_definitions_table(c)
    schema.create_feature_functions_table(c)
    schema.create_macro_series_tables(c)
    schema.create_market_function_candidates_table(c)
    schema.create_system_observations_table(c)
    with c.cursor() as cur:
        for col, coltype in _PROMOTE_COLUMNS:
            cur.execute(
                f"ALTER TABLE feature_definitions "
                f"ADD COLUMN IF NOT EXISTS {col} {coltype}")
    db_pool.close_pool()

    def _cleanup(cur):
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_mfc_full_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mfc_full_%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'mfc_full_%'")
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'mfc_full_%'")
        cur.execute("DELETE FROM system_observations "
                    "WHERE observer = 'full_auto_gate' "
                    "AND evidence->>'name' LIKE 'mfc_full_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()
    db_pool.close_pool()


_BODY = "def compute(rows):\n    return float(len(rows))"


def _pending(conn, name, origin="claude", ok=True, generator="cycle_runner"):
    from gefion.macro import candidates
    cid = candidates.create_candidate(
        conn, name=name, kind="cross_section", function_body=_BODY,
        origin=origin, principle_id="p-full", generator=generator)
    candidates.record_dry_run(conn, cid, {
        "ok": ok, "sample": [], "error": None if ok else "sandbox refusal",
        "seed": 42, "ran_at": "2026-07-31T00:00:00"})
    return cid


# --- the switch: fail-closed default -----------------------------------------------

class TestFullFlag:
    def test_default_is_off(self, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        assert candidates.full_autonomy_enabled() is False

    def test_truthy_spellings_enable(self, monkeypatch):
        from gefion.macro import candidates
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv("GEFION_FULL_AUTONOMY", val)
            assert candidates.full_autonomy_enabled() is True, val

    def test_falsy_spellings_stay_gated(self, monkeypatch):
        from gefion.macro import candidates
        for val in ("0", "false", "no", "", "off"):
            monkeypatch.setenv("GEFION_FULL_AUTONOMY", val)
            assert candidates.full_autonomy_enabled() is False, val


# --- rung-3 policy at the sole door ------------------------------------------------

class TestFullAutoApprove:
    def test_any_origin_auto_promotes(self, conn, monkeypatch):
        """Flag ON → even a claude-origin candidate from an untrusted generator
        auto-promotes, attributed policy:full-auto."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_FULL_AUTONOMY", "1")
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        monkeypatch.delenv("GEFION_EARNED_AUTONOMY", raising=False)
        cid = _pending(conn, "mfc_full_any", origin="claude",
                       generator="mfc_full_brandnew")
        fid = candidates.maybe_auto_approve(conn, cid)
        assert fid is not None
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "approved"
        assert c["reviewed_by"] == "policy:full-auto"
        assert c["promoted_function_id"] == fid

    def test_failed_dry_run_never_promotes(self, conn, monkeypatch):
        """Refusal invariant under rung 3: FAILED dry-run → never promoted."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_FULL_AUTONOMY", "1")
        cid = _pending(conn, "mfc_full_faildry", origin="template", ok=False)
        assert candidates.maybe_auto_approve(conn, cid) is None
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_missing_dry_run_never_promotes(self, conn, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_FULL_AUTONOMY", "1")
        cid = candidates.create_candidate(
            conn, name="mfc_full_nodry", kind="cross_section",
            function_body=_BODY, origin="template", principle_id="p-full",
            generator="cycle_runner")
        assert candidates.maybe_auto_approve(conn, cid) is None
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_flag_off_stays_gated(self, conn, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        monkeypatch.delenv("GEFION_EARNED_AUTONOMY", raising=False)
        cid = _pending(conn, "mfc_full_off", origin="claude")
        assert candidates.maybe_auto_approve(conn, cid) is None
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"


# --- the review-after digest (reuses #144 system_observations) ---------------------

class TestFullAutoDigest:
    def test_digest_observation_emitted(self, conn, monkeypatch):
        """A full-auto promotion records a standing digest observation naming
        what the open gate admitted."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_FULL_AUTONOMY", "1")
        cid = _pending(conn, "mfc_full_digest", origin="claude")
        candidates.maybe_auto_approve(conn, cid)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT observer, category, evidence->>'candidate_id',
                          evidence->>'name', evidence->>'reviewed_by'
                   FROM system_observations
                   WHERE observer = 'full_auto_gate'
                     AND evidence->>'name' = 'mfc_full_digest'""")
            row = cur.fetchone()
        assert row is not None
        observer, category, ev_cid, ev_name, ev_reviewed_by = row
        assert observer == "full_auto_gate"
        assert category in candidates_categories()
        assert ev_cid == str(cid)
        assert ev_name == "mfc_full_digest"
        assert ev_reviewed_by == "policy:full-auto"

    def test_digest_is_idempotent(self, conn, monkeypatch):
        """Re-recording the digest for the same candidate never duplicates."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_FULL_AUTONOMY", "1")
        cid = _pending(conn, "mfc_full_idem", origin="claude")
        candidates.maybe_auto_approve(conn, cid)
        # A second explicit digest call for the same candidate is a no-op.
        candidates.record_full_auto_digest(
            conn, candidates.get_candidate(conn, cid))
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM system_observations
                   WHERE observer = 'full_auto_gate'
                     AND evidence->>'name' = 'mfc_full_idem'""")
            assert cur.fetchone()[0] == 1

    def test_no_digest_when_not_full_auto(self, conn, monkeypatch):
        """A template/rung-1 promotion does NOT emit a full-auto digest."""
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_full_tmpl", origin="template")
        candidates.maybe_auto_approve(conn, cid)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM system_observations
                   WHERE observer = 'full_auto_gate'
                     AND evidence->>'name' = 'mfc_full_tmpl'""")
            assert cur.fetchone()[0] == 0


# --- precedence: full-auto ⊃ earned ⊃ template-auto --------------------------------

class TestPrecedence:
    def test_full_wins_over_lower_rungs(self, conn, monkeypatch):
        """All three flags on, a template candidate from an UNtrusted generator
        → attributed to the highest rung (policy:full-auto)."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_FULL_AUTONOMY", "1")
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_full_prec", origin="template",
                       generator="mfc_full_untrusted")
        candidates.maybe_auto_approve(conn, cid)
        assert candidates.get_candidate(conn, cid)["reviewed_by"] == \
            "policy:full-auto"


def candidates_categories():
    from gefion import observations
    return observations.CATEGORIES
