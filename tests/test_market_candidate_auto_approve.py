"""Rung-1 autonomy: auto-approve template-origin candidates (#142).

TDD: written FIRST. Rung 1 of the graduation ladder relaxes the spec-014
gate for the lowest-risk class only — deterministic, repo-reviewed template
bodies (origin='template') that pass their dry-run auto-promote through the
SOLE door (approve_candidate), attributed to reviewed_by='policy:template-auto'.

Everything else stays gated:
  - LLM-generated bodies (origin='claude') still require a human.
  - A failed or missing dry-run can NEVER promote (refusal invariant).
  - Flipping the flag off returns to everything-gated with no schema change.

The flag is a single env switch (GEFION_TEMPLATE_AUTO_APPROVE), fail-closed
OFF by default so merging this change does not silently open the gate.
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


# Columns the promote path (approve_candidate) writes into feature_definitions.
# test_query_caching / test_composite_indexes drop feature_definitions CASCADE
# and recreate a stripped-down local schema (id, name, function_name only),
# never restoring it — so a promote running afterward on the shared test DB hits
# a non-canonical table. We restore the columns NON-destructively (ADD COLUMN IF
# NOT EXISTS) rather than dropping: feature_definitions is the parent of the
# computed_features hypertable's FK, so a CASCADE drop would contaminate later
# tests in suite order.
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
    # Heal every table the auto-approve promote touches. The canonical creators
    # are idempotent; feature_definitions may exist in a stripped-down form left
    # by an earlier test, so top up the columns the promote needs (non-destructive
    # — see _PROMOTE_COLUMNS). Then close the process-global pool so CycleRunner
    # opens FRESH connections: CI's failure was a pooled connection carrying a
    # prepared-statement plan pinned to a since-recreated relation's OID
    # ("could not open relation with OID …"); fresh connections re-plan cleanly.
    from gefion.db import pool as db_pool
    schema.create_feature_definitions_table(c)
    schema.create_feature_functions_table(c)
    schema.create_macro_series_tables(c)
    schema.create_market_function_candidates_table(c)
    with c.cursor() as cur:
        for col, coltype in _PROMOTE_COLUMNS:
            cur.execute(
                f"ALTER TABLE feature_definitions "
                f"ADD COLUMN IF NOT EXISTS {col} {coltype}")
    db_pool.close_pool()

    def _cleanup(cur):
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_mfc_auto_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mfc_auto_%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'mfc_auto_%'")
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'mfc_auto_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()
    db_pool.close_pool()


def _pending(conn, name, origin="template", ok=True):
    """A pending candidate with a recorded dry-run (passing unless ok=False)."""
    from gefion.macro import candidates
    cid = candidates.create_candidate(
        conn, name=name, kind="cross_section",
        function_body="def compute(rows):\n    return float(len(rows))",
        origin=origin, principle_id="p-auto", generator="cycle_runner")
    candidates.record_dry_run(conn, cid, {
        "ok": ok, "sample": [], "error": None if ok else "sandbox refusal",
        "seed": 42, "ran_at": "2026-07-31T00:00:00"})
    return cid


# --- the flag: single revocation switch, fail-closed default -----------------------

class TestPolicyFlag:
    def test_default_is_off(self, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        assert candidates.template_auto_approval_enabled() is False

    def test_truthy_spellings_enable(self, monkeypatch):
        from gefion.macro import candidates
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", val)
            assert candidates.template_auto_approval_enabled() is True, val

    def test_falsy_spellings_stay_gated(self, monkeypatch):
        from gefion.macro import candidates
        for val in ("0", "false", "no", "", "off"):
            monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", val)
            assert candidates.template_auto_approval_enabled() is False, val


# --- rung-1 policy at the sole door ------------------------------------------------

class TestTemplateAutoApprove:
    def test_template_pass_flag_on_auto_promotes(self, conn, monkeypatch):
        """(1) template origin + passing dry-run + flag ON → auto-promoted,
        attributed to the policy, real feature_functions row created."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_auto_ok", origin="template", ok=True)
        fid = candidates.maybe_auto_approve(conn, cid)
        assert fid is not None
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "approved"
        assert c["reviewed_by"] == "policy:template-auto"
        assert c["reviewed_at"] is not None
        assert c["promoted_function_id"] == fid
        with conn.cursor() as cur:
            cur.execute("SELECT scope, status, enabled FROM feature_functions "
                        "WHERE id = %s", (fid,))
            assert cur.fetchone() == ("market", "active", True)

    def test_audit_trail_is_exactly_the_policy_string(self, conn, monkeypatch):
        """(5) reviewed_by names the policy exactly — never NULL, never a
        human, never blank."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_auto_audit", origin="template", ok=True)
        candidates.maybe_auto_approve(conn, cid)
        with conn.cursor() as cur:
            cur.execute("SELECT reviewed_by FROM market_function_candidates "
                        "WHERE id = %s", (cid,))
            (reviewed_by,) = cur.fetchone()
        assert reviewed_by == "policy:template-auto"
        assert reviewed_by is not None

    def test_claude_origin_stays_gated(self, conn, monkeypatch):
        """(2) LLM-generated body + passing dry-run + flag ON → NOT
        auto-promoted; stays pending for a human."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_auto_claude", origin="claude", ok=True)
        assert candidates.maybe_auto_approve(conn, cid) is None
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "pending"
        assert c["reviewed_by"] is None
        assert c["promoted_function_id"] is None

    def test_failed_dry_run_never_promotes(self, conn, monkeypatch):
        """(3) refusal invariant: template origin + FAILED dry-run + flag ON
        → never promoted, stays pending."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_auto_faildry", origin="template", ok=False)
        assert candidates.maybe_auto_approve(conn, cid) is None
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "pending"
        assert c["reviewed_by"] is None

    def test_missing_dry_run_never_promotes(self, conn, monkeypatch):
        """(3) refusal invariant: template origin + MISSING dry-run + flag ON
        → never promoted."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = candidates.create_candidate(
            conn, name="mfc_auto_nodry", kind="cross_section",
            function_body="def compute(rows):\n    return 1.0",
            origin="template", principle_id="p-auto", generator="cycle_runner")
        assert candidates.maybe_auto_approve(conn, cid) is None
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_flag_off_nothing_auto_promotes(self, conn, monkeypatch):
        """(4) revocation: flag OFF → even a template + passing dry-run stays
        gated. Single switch back to everything-gated, no schema change."""
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        cid = _pending(conn, "mfc_auto_off", origin="template", ok=True)
        assert candidates.maybe_auto_approve(conn, cid) is None
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "pending"
        assert c["reviewed_by"] is None

    def test_non_pending_is_ignored(self, conn, monkeypatch):
        """An already-approved candidate is not re-approved (idempotent, no
        second promotion)."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        cid = _pending(conn, "mfc_auto_twice", origin="template", ok=True)
        fid = candidates.maybe_auto_approve(conn, cid)
        assert fid is not None
        assert candidates.maybe_auto_approve(conn, cid) is None


# --- the generation chokepoint invokes the policy ----------------------------------

class TestProposeInvokesPolicy:
    def test_propose_auto_approves_template_when_on(self, conn, monkeypatch):
        """The one chokepoint (propose_market_candidate) routes qualifying
        template candidates through the policy door when the flag is on."""
        from gefion.experiments.cycle_runner import CycleRunner
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")

        runner = CycleRunner(schema.test_db_url())
        # Force the template path (no Claude body) with a deterministic body.
        monkeypatch.setattr(
            "gefion.experiments.cycle_runner._generate_market_body_claude",
            lambda *a, **k: None)
        monkeypatch.setattr(
            "gefion.experiments.cycle_runner._generate_market_body_template",
            lambda pid: "def compute(rows):\n    return float(len(rows))")

        cid = runner.propose_market_candidate("mfc-auto-chokepoint", "design")
        assert cid is not None
        c = candidates.get_candidate(conn, cid)
        assert c["origin"] == "template"
        assert c["review_state"] == "approved"
        assert c["reviewed_by"] == "policy:template-auto"
