"""Dry-run tests for generated market-function candidates (014 T006/T025).

TDD: written FIRST. The dry-run is the only sanctioned execution of a
candidate: the sandbox over deterministic SEEDED SYNTHETIC inputs — never
stored data (evaluation against real history IS execution, which the gate
forbids). Its record is what the reviewer sees; a violation blocks approval.
"""
import pytest

from gefion.macro import candidates


class TestSyntheticCrossSection:
    def test_deterministic_for_same_seed(self):
        a = candidates.synthetic_cross_section(seed=42)
        b = candidates.synthetic_cross_section(seed=42)
        assert a == b
        assert a != candidates.synthetic_cross_section(seed=7)

    def test_shape_matches_market_contract(self):
        days = candidates.synthetic_cross_section(seed=42)
        assert len(days) >= 3                      # several dates
        first_date, rows = days[0]
        assert len(rows) >= 20                     # a plausible cross-section
        assert {"symbol", "close", "high", "low", "volume", "sector"} <= set(rows[0])

    def test_declared_features_present_in_rows(self):
        days = candidates.synthetic_cross_section(
            seed=42, feature_names=["indicator_rsi_14"])
        _, rows = days[0]
        assert all("indicator_rsi_14" in r for r in rows)


class TestDryRun:
    def test_ok_body_yields_sample_values(self):
        body = "def compute(rows):\n    return float(len(rows))"
        rec = candidates.dry_run_candidate(body, kind="cross_section", inputs={})
        assert rec["ok"] is True
        assert rec["error"] is None
        assert rec["seed"] == 42
        assert len(rec["sample"]) >= 3
        assert all(isinstance(s["value"], float) for s in rec["sample"])
        assert rec["ran_at"]

    def test_gap_returning_body_is_ok(self):
        body = "def compute(rows):\n    return None"
        rec = candidates.dry_run_candidate(body, kind="cross_section", inputs={})
        assert rec["ok"] is True
        assert all(s["value"] is None for s in rec["sample"])

    def test_sandbox_violation_fails_the_dry_run(self):
        body = "import os\ndef compute(rows):\n    return 1.0"
        rec = candidates.dry_run_candidate(body, kind="cross_section", inputs={})
        assert rec["ok"] is False
        assert rec["error"]

    def test_wrong_shape_fails_the_dry_run(self):
        body = "def compute(rows):\n    return 'high'"
        rec = candidates.dry_run_candidate(body, kind="cross_section", inputs={})
        assert rec["ok"] is False
        assert "float" in rec["error"]

    def test_missing_compute_fails_the_dry_run(self):
        rec = candidates.dry_run_candidate("x = 1", kind="cross_section", inputs={})
        assert rec["ok"] is False
        assert "compute" in rec["error"]

    def test_raising_body_fails_the_dry_run(self):
        body = "def compute(rows):\n    raise ValueError('boom')"
        rec = candidates.dry_run_candidate(body, kind="cross_section", inputs={})
        assert rec["ok"] is False
        assert "boom" in rec["error"]


# --- T025 (US3): composite-kind dry-run --------------------------------------------

class TestCompositeDryRun:
    def test_seeded_series_rows_deterministic(self):
        a = candidates.synthetic_series_rows(seed=42, series_names=["vix", "b"])
        b = candidates.synthetic_series_rows(seed=42, series_names=["vix", "b"])
        assert a == b
        d, row = a[0]
        assert set(row) == {"vix", "b"}

    def test_composite_dry_run_executes_over_seeded_values(self):
        body = ("def compute(row):\n"
                "    return row['vix'] + row['breadth']\n")
        rec = candidates.dry_run_candidate(
            body, kind="composite", inputs={"series": ["vix", "breadth"]})
        assert rec["ok"] is True
        assert len(rec["sample"]) >= 3
        assert all(isinstance(s["value"], float) for s in rec["sample"])

    def test_composite_dry_run_fails_on_wrong_contract(self):
        # a cross-section-contract body run as composite raises inside
        body = "def compute(rows):\n    return float(len(rows))\n"
        rec = candidates.dry_run_candidate(
            body, kind="composite", inputs={"series": ["vix"]})
        # len(dict) is legal python — but a body indexing missing keys fails
        body2 = "def compute(row):\n    return row['not_declared']\n"
        rec2 = candidates.dry_run_candidate(
            body2, kind="composite", inputs={"series": ["vix"]})
        assert rec2["ok"] is False
