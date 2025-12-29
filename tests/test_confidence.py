"""Tests for confidence metrics computation.

TDD: These tests are written FIRST, before implementation.
"""
import math
import pytest
import numpy as np


class TestQuantileConfidence:
    """Tests for compute_quantile_confidence function."""

    def test_narrow_iqr_gives_high_confidence(self):
        """Narrow IQR relative to historical = high confidence."""
        from g2.ml.confidence import compute_quantile_confidence

        # IQR = 0.05, historical median = 0.10 (narrow = high confidence)
        confidence = compute_quantile_confidence(
            q10=-0.025, q50=0.05, q90=0.025,
            historical_iqr_median=0.10
        )

        assert confidence > 0.5  # Higher than baseline neutral

    def test_wide_iqr_gives_low_confidence(self):
        """Wide IQR relative to historical = low confidence."""
        from g2.ml.confidence import compute_quantile_confidence

        # IQR = 0.20, historical median = 0.10 (wide = low confidence)
        confidence = compute_quantile_confidence(
            q10=-0.10, q50=0.0, q90=0.10,
            historical_iqr_median=0.10
        )

        assert confidence < 0.5  # Lower confidence

    def test_returns_neutral_for_zero_historical(self):
        """Returns 0.5 when no historical reference."""
        from g2.ml.confidence import compute_quantile_confidence

        confidence = compute_quantile_confidence(
            q10=-0.05, q50=0.0, q90=0.05,
            historical_iqr_median=0.0
        )

        assert confidence == 0.5

    def test_confidence_bounded_zero_to_one(self):
        """Confidence should always be in [0, 1] range."""
        from g2.ml.confidence import compute_quantile_confidence

        # Test various inputs
        for iqr in [0.01, 0.05, 0.10, 0.50, 1.0]:
            confidence = compute_quantile_confidence(
                q10=-iqr/2, q50=0.0, q90=iqr/2,
                historical_iqr_median=0.10
            )
            assert 0.0 <= confidence <= 1.0


class TestClassifierConfidence:
    """Tests for compute_classifier_confidence function."""

    def test_high_probability_single_class_gives_high_confidence(self):
        """90% probability in one class = high confidence."""
        from g2.ml.confidence import compute_classifier_confidence

        probs = {
            'strong_up': 0.90,
            'weak_up': 0.05,
            'flat': 0.02,
            'weak_down': 0.02,
            'strong_down': 0.01
        }

        result = compute_classifier_confidence(probs)

        assert result['confidence'] > 0.7
        assert result['margin'] == pytest.approx(0.85)  # 0.90 - 0.05

    def test_uniform_distribution_gives_low_confidence(self):
        """Uniform 20% each class = low confidence."""
        from g2.ml.confidence import compute_classifier_confidence

        probs = {
            'strong_up': 0.20,
            'weak_up': 0.20,
            'flat': 0.20,
            'weak_down': 0.20,
            'strong_down': 0.20
        }

        result = compute_classifier_confidence(probs)

        assert result['confidence'] < 0.2
        assert result['margin'] == pytest.approx(0.0)  # All equal

    def test_returns_entropy(self):
        """Returns Shannon entropy of distribution."""
        from g2.ml.confidence import compute_classifier_confidence

        # Uniform distribution has max entropy = log(5) ≈ 1.61
        probs = {
            'strong_up': 0.20,
            'weak_up': 0.20,
            'flat': 0.20,
            'weak_down': 0.20,
            'strong_down': 0.20
        }

        result = compute_classifier_confidence(probs)

        assert result['entropy'] == pytest.approx(math.log(5), rel=0.01)

    def test_returns_margin_between_top_two(self):
        """Returns difference between top-2 class probabilities."""
        from g2.ml.confidence import compute_classifier_confidence

        probs = {
            'strong_up': 0.50,
            'weak_up': 0.30,
            'flat': 0.10,
            'weak_down': 0.07,
            'strong_down': 0.03
        }

        result = compute_classifier_confidence(probs)

        assert result['margin'] == pytest.approx(0.20)  # 0.50 - 0.30

    def test_handles_zero_probabilities(self):
        """Handles zero probabilities without error."""
        from g2.ml.confidence import compute_classifier_confidence

        probs = {
            'strong_up': 1.0,
            'weak_up': 0.0,
            'flat': 0.0,
            'weak_down': 0.0,
            'strong_down': 0.0
        }

        result = compute_classifier_confidence(probs)

        assert result['confidence'] > 0.9
        assert result['margin'] == pytest.approx(1.0)


class TestEnsembleDisagreement:
    """Tests for compute_ensemble_disagreement function."""

    def test_identical_predictions_give_full_agreement(self):
        """All models predicting same value = full agreement."""
        from g2.ml.confidence import compute_ensemble_disagreement

        predictions = [0.05, 0.05, 0.05]

        result = compute_ensemble_disagreement(predictions)

        assert result['std'] == pytest.approx(0.0, abs=1e-10)
        assert result['range'] == pytest.approx(0.0, abs=1e-10)
        assert result['agreement_score'] == pytest.approx(1.0, rel=1e-5)

    def test_divergent_predictions_give_low_agreement(self):
        """Models with very different predictions = low agreement."""
        from g2.ml.confidence import compute_ensemble_disagreement

        predictions = [-0.10, 0.0, 0.10]  # Spread of 0.20

        result = compute_ensemble_disagreement(predictions)

        assert result['std'] > 0.05
        assert result['range'] == pytest.approx(0.20)
        assert result['agreement_score'] < 0.5

    def test_single_model_returns_full_agreement(self):
        """Single model = full agreement by default."""
        from g2.ml.confidence import compute_ensemble_disagreement

        predictions = [0.05]

        result = compute_ensemble_disagreement(predictions)

        assert result['std'] == 0.0
        assert result['agreement_score'] == 1.0

    def test_empty_predictions_returns_full_agreement(self):
        """Empty predictions = full agreement by default."""
        from g2.ml.confidence import compute_ensemble_disagreement

        predictions = []

        result = compute_ensemble_disagreement(predictions)

        assert result['std'] == 0.0
        assert result['agreement_score'] == 1.0

    def test_agreement_score_bounded(self):
        """Agreement score should always be in [0, 1] range."""
        from g2.ml.confidence import compute_ensemble_disagreement

        # Test with varying levels of disagreement
        for spread in [0.01, 0.05, 0.10, 0.50]:
            predictions = [-spread, 0, spread]
            result = compute_ensemble_disagreement(predictions)
            assert 0.0 <= result['agreement_score'] <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
