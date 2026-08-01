"""Rung-2 autonomy: earned per-generator auto-approval (#142).

TDD: written FIRST. Rung 2 of the graduation ladder grants a *generator*
autonomy AFTER it has accrued trust: N human approvals from that generator
with ZERO subsequent demotions/disables of its promoted series. Once earned,
the generator's dry-run-passing candidates auto-promote through the SOLE door
(approve_candidate), attributed reviewed_by='policy:earned:<generator>'.

Trust is a pure function of audit history (no new state):
  - human approvals per generator are counted from the candidate ledger;
  - a demotion/disable of ANY of the generator's promoted series flips the
    derived condition back to gated (revocation is automatic).

Everything else stays gated:
  - a generator below the threshold requires a human;
  - a failed or missing dry-run can NEVER promote (refusal invariant);
  - flipping GEFION_EARNED_AUTONOMY off returns to everything-gated, no schema
    change. Fail-closed OFF by default so merging this does not open the gate.
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


# Same non-destructive heal as the rung-1 fixture: other tests drop/strip
# feature_definitions; restore the columns the promote path writes.
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
    with c.cursor() as cur:
        for col, coltype in _PROMOTE_COLUMNS:
            cur.execute(
                f"ALTER TABLE feature_definitions "
                f"ADD COLUMN IF NOT EXISTS {col} {coltype}")
    db_pool.close_pool()

    def _cleanup(cur):
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_mfc_earn_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mfc_earn_%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'mfc_earn_%'")
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'mfc_earn_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()
    db_pool.close_pool()


_BODY = "def compute(rows):\n    return float(len(rows))"


def _pending(conn, name, generator, origin="claude", ok=True):
    """A pending candidate from `generator` with a recorded dry-run."""
    from gefion.macro import candidates
    cid = candidates.create_candidate(
        conn, name=name, kind="cross_section", function_body=_BODY,
        origin=origin, principle_id="p-earn", generator=generator)
    candidates.record_dry_run(conn, cid, {
        "ok": ok, "sample": [], "error": None if ok else "sandbox refusal",
        "seed": 42, "ran_at": "2026-07-31T00:00:00"})
    return cid


def _human_approve(conn, name, generator, approver="alice"):
    """Create a candidate from `generator` and HUMAN-approve it. Returns fid."""
    from gefion.macro import candidates
    cid = _pending(conn, name, generator, ok=True)
    return candidates.approve_candidate(conn, cid, approver=approver)


def _accrue(conn, generator, n, prefix):
    """N human approvals from `generator`. Returns the promoted fids."""
    return [_human_approve(conn, f"{prefix}_{i}", generator) for i in range(n)]


# --- the switch + threshold: fail-closed default -----------------------------------

class TestEarnedFlag:
    def test_default_is_off(self, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_EARNED_AUTONOMY", raising=False)
        assert candidates.earned_autonomy_enabled() is False

    def test_truthy_spellings_enable(self, monkeypatch):
        from gefion.macro import candidates
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv("GEFION_EARNED_AUTONOMY", val)
            assert candidates.earned_autonomy_enabled() is True, val

    def test_falsy_spellings_stay_gated(self, monkeypatch):
        from gefion.macro import candidates
        for val in ("0", "false", "no", "", "off"):
            monkeypatch.setenv("GEFION_EARNED_AUTONOMY", val)
            assert candidates.earned_autonomy_enabled() is False, val

    def test_threshold_default(self, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_EARNED_AUTONOMY_N", raising=False)
        assert candidates.earned_autonomy_threshold() == \
            candidates.DEFAULT_EARNED_AUTONOMY_N

    def test_threshold_configurable(self, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY_N", "5")
        assert candidates.earned_autonomy_threshold() == 5

    def test_threshold_bad_value_falls_back(self, monkeypatch):
        from gefion.macro import candidates
        for bad in ("0", "-2", "abc"):
            monkeypatch.setenv("GEFION_EARNED_AUTONOMY_N", bad)
            assert candidates.earned_autonomy_threshold() == \
                candidates.DEFAULT_EARNED_AUTONOMY_N, bad


# --- the derivation: trust as a pure function of audit history ----------------------

class TestGeneratorAutonomyEarned:
    def test_below_threshold_not_earned(self, conn):
        from gefion.macro import candidates
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genA", n - 1, "mfc_earn_below")
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genA") is False

    def test_at_threshold_zero_demotions_earned(self, conn):
        from gefion.macro import candidates
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genB", n, "mfc_earn_at")
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genB") is True

    def test_policy_approvals_do_not_count(self, conn, monkeypatch):
        """Only HUMAN approvals accrue trust; policy approvals never do."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        for i in range(n):
            cid = _pending(conn, f"mfc_earn_pol_{i}", "mfc_earn_genC",
                           origin="template")
            candidates.maybe_auto_approve(conn, cid)  # policy:template-auto
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genC") is False

    def test_demotion_revokes(self, conn):
        """A promoted series set to 'demoted' flips the generator back to gated."""
        from gefion.macro import candidates
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        fids = _accrue(conn, "mfc_earn_genD", n, "mfc_earn_dem")
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genD") is True
        with conn.cursor() as cur:
            cur.execute("UPDATE feature_functions SET status = 'demoted' "
                        "WHERE id = %s", (fids[0],))
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genD") is False

    def test_disable_revokes(self, conn):
        """A promoted series disabled (enabled=FALSE) revokes autonomy too."""
        from gefion.macro import candidates
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        fids = _accrue(conn, "mfc_earn_genE", n, "mfc_earn_dis")
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genE") is True
        with conn.cursor() as cur:
            cur.execute("UPDATE feature_functions SET enabled = FALSE "
                        "WHERE id = %s", (fids[0],))
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genE") is False

    def test_trust_is_per_generator(self, conn):
        from gefion.macro import candidates
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genF", n, "mfc_earn_f")
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_genF") is True
        assert candidates.generator_autonomy_earned(conn, "mfc_earn_other") is False

    def test_empty_generator_never_earned(self, conn):
        from gefion.macro import candidates
        assert candidates.generator_autonomy_earned(conn, None) is False
        assert candidates.generator_autonomy_earned(conn, "") is False


# --- rung-2 policy at the sole door ------------------------------------------------

class TestEarnedAutoApprove:
    def test_earned_generator_auto_promotes(self, conn, monkeypatch):
        """Flag ON + earned generator + passing dry-run → auto-promoted,
        attributed policy:earned:<generator> (even a claude-origin body)."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genG", n, "mfc_earn_seed")

        cid = _pending(conn, "mfc_earn_new", "mfc_earn_genG", origin="claude")
        fid = candidates.maybe_auto_approve(conn, cid)
        assert fid is not None
        c = candidates.get_candidate(conn, cid)
        assert c["review_state"] == "approved"
        assert c["reviewed_by"] == "policy:earned:mfc_earn_genG"
        assert c["promoted_function_id"] == fid

    def test_flag_off_stays_gated(self, conn, monkeypatch):
        from gefion.macro import candidates
        monkeypatch.delenv("GEFION_EARNED_AUTONOMY", raising=False)
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genH", n, "mfc_earn_hs")
        cid = _pending(conn, "mfc_earn_hoff", "mfc_earn_genH")
        assert candidates.maybe_auto_approve(conn, cid) is None
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_ungated_generator_stays_gated(self, conn, monkeypatch):
        """Flag ON but generator below threshold → still gated."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        cid = _pending(conn, "mfc_earn_young", "mfc_earn_genI")
        assert candidates.maybe_auto_approve(conn, cid) is None

    def test_failed_dry_run_never_promotes(self, conn, monkeypatch):
        """Refusal invariant under rung 2: earned generator + FAILED dry-run
        → never promoted."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genJ", n, "mfc_earn_js")
        cid = _pending(conn, "mfc_earn_jfail", "mfc_earn_genJ", ok=False)
        assert candidates.maybe_auto_approve(conn, cid) is None
        assert candidates.get_candidate(conn, cid)["review_state"] == "pending"

    def test_demotion_revokes_at_the_door(self, conn, monkeypatch):
        """End-to-end revocation: an earned generator whose series is then
        disabled no longer auto-promotes."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        monkeypatch.delenv("GEFION_TEMPLATE_AUTO_APPROVE", raising=False)
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        fids = _accrue(conn, "mfc_earn_genK", n, "mfc_earn_ks")

        cid1 = _pending(conn, "mfc_earn_k1", "mfc_earn_genK")
        assert candidates.maybe_auto_approve(conn, cid1) is not None  # earned

        with conn.cursor() as cur:
            cur.execute("UPDATE feature_functions SET status = 'demoted' "
                        "WHERE id = %s", (fids[0],))

        cid2 = _pending(conn, "mfc_earn_k2", "mfc_earn_genK")
        assert candidates.maybe_auto_approve(conn, cid2) is None  # revoked
        assert candidates.get_candidate(conn, cid2)["review_state"] == "pending"


# --- precedence: earned ⊃ template-auto --------------------------------------------

class TestPrecedence:
    def test_earned_wins_over_template(self, conn, monkeypatch):
        """Both flags on, template-origin candidate from an earned generator →
        attributed to the higher rung (policy:earned)."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        n = candidates.DEFAULT_EARNED_AUTONOMY_N
        _accrue(conn, "mfc_earn_genL", n, "mfc_earn_ls")
        cid = _pending(conn, "mfc_earn_lt", "mfc_earn_genL", origin="template")
        candidates.maybe_auto_approve(conn, cid)
        assert candidates.get_candidate(conn, cid)["reviewed_by"] == \
            "policy:earned:mfc_earn_genL"

    def test_template_still_applies_for_ungated_generator(self, conn, monkeypatch):
        """Both flags on, template-origin from a NON-earned generator → falls
        through to rung 1 (policy:template-auto)."""
        from gefion.macro import candidates
        monkeypatch.setenv("GEFION_EARNED_AUTONOMY", "1")
        monkeypatch.setenv("GEFION_TEMPLATE_AUTO_APPROVE", "1")
        monkeypatch.delenv("GEFION_FULL_AUTONOMY", raising=False)
        cid = _pending(conn, "mfc_earn_mt", "mfc_earn_genM", origin="template")
        candidates.maybe_auto_approve(conn, cid)
        assert candidates.get_candidate(conn, cid)["reviewed_by"] == \
            "policy:template-auto"
