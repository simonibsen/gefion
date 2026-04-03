"""Tests for FDR (False Discovery Rate) control module.

TDD: These tests are written FIRST, before implementation.
Tests the Benjamini-Hochberg procedure and holdout p-value computation.
"""
import pytest


class TestApplyFdr:
    """Tests for the apply_fdr Benjamini-Hochberg procedure."""

    def test_all_significant_pvalues_survive(self):
        """All very small p-values should survive FDR correction."""
        from gefion.experiments.statistical import apply_fdr

        p_values = [0.001, 0.002, 0.003, 0.004, 0.005]
        mask = apply_fdr(p_values, fdr_rate=0.10)

        assert len(mask) == len(p_values)
        assert all(mask), "All highly significant p-values should survive"

    def test_all_nonsignificant_pvalues_rejected(self):
        """All large p-values should be rejected at a strict rate."""
        from gefion.experiments.statistical import apply_fdr

        p_values = [0.50, 0.60, 0.70, 0.80, 0.90]
        mask = apply_fdr(p_values, fdr_rate=0.01)

        assert len(mask) == len(p_values)
        assert not any(mask), "No non-significant p-values should survive at strict rate"

    def test_mixed_pvalues_only_small_survive(self):
        """Only genuinely small p-values should survive in a mixed set."""
        from gefion.experiments.statistical import apply_fdr

        # Two small p-values and three large ones
        p_values = [0.001, 0.005, 0.30, 0.60, 0.90]
        mask = apply_fdr(p_values, fdr_rate=0.10)

        assert len(mask) == len(p_values)
        # The two small p-values should survive
        assert mask[0] is True, "p=0.001 should survive"
        assert mask[1] is True, "p=0.005 should survive"
        # The large p-values should not survive
        assert mask[2] is False, "p=0.30 should not survive"
        assert mask[3] is False, "p=0.60 should not survive"
        assert mask[4] is False, "p=0.90 should not survive"

    def test_empty_list_returns_empty(self):
        """Empty input should return empty output."""
        from gefion.experiments.statistical import apply_fdr

        mask = apply_fdr([], fdr_rate=0.10)
        assert mask == []

    def test_single_experiment_significant(self):
        """Single significant p-value should survive."""
        from gefion.experiments.statistical import apply_fdr

        mask = apply_fdr([0.01], fdr_rate=0.10)
        assert mask == [True]

    def test_single_experiment_nonsignificant(self):
        """Single non-significant p-value should be rejected."""
        from gefion.experiments.statistical import apply_fdr

        mask = apply_fdr([0.50], fdr_rate=0.10)
        assert mask == [False]

    def test_configurable_rate_strict(self):
        """Stricter FDR rate should reject more hypotheses."""
        from gefion.experiments.statistical import apply_fdr

        p_values = [0.01, 0.04, 0.08, 0.15, 0.50]

        mask_strict = apply_fdr(p_values, fdr_rate=0.05)
        mask_lenient = apply_fdr(p_values, fdr_rate=0.20)

        strict_count = sum(mask_strict)
        lenient_count = sum(mask_lenient)
        assert lenient_count >= strict_count, (
            "Lenient FDR rate should allow at least as many discoveries as strict rate"
        )

    def test_configurable_rate_lenient_promotes_more(self):
        """A lenient rate of 0.20 should promote borderline p-values that 0.05 rejects."""
        from gefion.experiments.statistical import apply_fdr

        # p=0.08 is borderline: rejected at 0.05, possibly accepted at 0.20
        p_values = [0.001, 0.08, 0.50]

        mask_strict = apply_fdr(p_values, fdr_rate=0.05)
        mask_lenient = apply_fdr(p_values, fdr_rate=0.20)

        # At 0.05, the borderline one should be rejected
        assert mask_strict[1] is False, "p=0.08 should not survive at fdr_rate=0.05"
        # At 0.20, the borderline one should survive
        assert mask_lenient[1] is True, "p=0.08 should survive at fdr_rate=0.20"

    def test_random_uniform_pvalues_limited_promotions(self):
        """Under null (uniform p-values), FDR at 10% on 20 tests should promote at most ~2."""
        import random

        from gefion.experiments.statistical import apply_fdr

        random.seed(42)
        p_values = [random.uniform(0, 1) for _ in range(20)]
        mask = apply_fdr(p_values, fdr_rate=0.10)

        promoted = sum(mask)
        assert promoted <= 4, (
            f"Expected at most ~2 promotions from uniform p-values, got {promoted}"
        )


class TestComputeHoldoutPvalue:
    """Tests for compute_holdout_pvalue paired t-test."""

    def test_clearly_different_distributions(self):
        """Clearly separated distributions should produce a small p-value."""
        from gefion.experiments.statistical import compute_holdout_pvalue

        baseline = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.02, 0.98, 1.01, 0.99]
        experimental = [2.0, 2.1, 1.9, 2.0, 2.05, 1.95, 2.02, 1.98, 2.01, 1.99]

        p = compute_holdout_pvalue(baseline, experimental)
        assert isinstance(p, float)
        assert p < 0.01, f"Expected very small p-value for different distributions, got {p}"

    def test_identical_distributions_large_pvalue(self):
        """Identical distributions should produce a large p-value (>0.05)."""
        from gefion.experiments.statistical import compute_holdout_pvalue

        scores = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.02, 0.98, 1.01, 0.99]

        p = compute_holdout_pvalue(scores, scores)
        assert isinstance(p, float)
        assert p > 0.05, f"Expected large p-value for identical distributions, got {p}"

    def test_empty_lists_raises_valueerror(self):
        """Empty input lists should raise ValueError."""
        from gefion.experiments.statistical import compute_holdout_pvalue

        with pytest.raises(ValueError):
            compute_holdout_pvalue([], [])

    def test_pvalue_bounded_zero_to_one(self):
        """P-value should always be between 0 and 1."""
        from gefion.experiments.statistical import compute_holdout_pvalue

        baseline = [1.0, 2.0, 3.0, 4.0, 5.0]
        experimental = [1.5, 2.5, 3.5, 4.5, 5.5]

        p = compute_holdout_pvalue(baseline, experimental)
        assert 0.0 <= p <= 1.0, f"P-value should be in [0, 1], got {p}"
