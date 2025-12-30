"""Tests for calibration metrics in evaluation.py.

TDD: These tests are written FIRST, before implementation.
"""
import numpy as np
import pytest


class TestExpectedCalibrationError:
    """Tests for compute_expected_calibration_error function."""

    def test_perfect_calibration_gives_zero_ece(self):
        """Perfectly calibrated predictions have ECE = 0."""
        from g2.ml.evaluation import compute_expected_calibration_error

        # Predictions where accuracy matches confidence
        # All at 80% confidence, 80% actually correct
        predicted_probs = np.array([0.8, 0.8, 0.8, 0.8, 0.8])
        actual_outcomes = np.array([1, 1, 1, 1, 0])  # 80% correct

        ece, _ = compute_expected_calibration_error(predicted_probs, actual_outcomes)

        assert ece < 0.05  # Near zero with some tolerance for binning

    def test_overconfident_predictions_give_positive_ece(self):
        """Overconfident predictions have positive ECE."""
        from g2.ml.evaluation import compute_expected_calibration_error

        # High confidence but low accuracy
        predicted_probs = np.array([0.9, 0.9, 0.9, 0.9, 0.9])
        actual_outcomes = np.array([1, 0, 0, 0, 0])  # Only 20% correct

        ece, _ = compute_expected_calibration_error(predicted_probs, actual_outcomes)

        assert ece > 0.5  # Large calibration error

    def test_underconfident_predictions_give_positive_ece(self):
        """Underconfident predictions have positive ECE."""
        from g2.ml.evaluation import compute_expected_calibration_error

        # Low confidence but high accuracy
        predicted_probs = np.array([0.3, 0.3, 0.3, 0.3, 0.3])
        actual_outcomes = np.array([1, 1, 1, 1, 0])  # 80% correct

        ece, _ = compute_expected_calibration_error(predicted_probs, actual_outcomes)

        assert ece > 0.3  # Calibration error

    def test_returns_bin_details(self):
        """Returns detailed bin information."""
        from g2.ml.evaluation import compute_expected_calibration_error

        predicted_probs = np.array([0.1, 0.3, 0.5, 0.7, 0.9])
        actual_outcomes = np.array([0, 0, 1, 1, 1])

        ece, bin_details = compute_expected_calibration_error(predicted_probs, actual_outcomes, n_bins=5)

        assert len(bin_details) > 0
        for bin_info in bin_details:
            assert "bin" in bin_info
            assert "count" in bin_info
            assert "avg_confidence" in bin_info
            assert "accuracy" in bin_info
            assert "calibration_error" in bin_info

    def test_ece_bounded_zero_to_one(self):
        """ECE should always be in [0, 1] range."""
        from g2.ml.evaluation import compute_expected_calibration_error

        # Test various distributions
        for _ in range(10):
            probs = np.random.rand(100)
            outcomes = np.random.randint(0, 2, 100)
            ece, _ = compute_expected_calibration_error(probs, outcomes)
            assert 0.0 <= ece <= 1.0

    def test_handles_empty_bins(self):
        """Handles cases where some bins are empty."""
        from g2.ml.evaluation import compute_expected_calibration_error

        # All predictions clustered in one range
        predicted_probs = np.array([0.5, 0.51, 0.49, 0.52, 0.48])
        actual_outcomes = np.array([1, 1, 0, 1, 0])

        ece, bin_details = compute_expected_calibration_error(predicted_probs, actual_outcomes, n_bins=10)

        # Should not raise, ECE should be valid
        assert 0.0 <= ece <= 1.0


class TestAccuracyByConfidence:
    """Tests for calculate_accuracy_by_confidence function."""

    def test_returns_decile_buckets(self):
        """Returns accuracy metrics by confidence decile."""
        import pandas as pd
        from g2.ml.evaluation import calculate_accuracy_by_confidence

        # Create predictions with varying confidence
        predictions = pd.DataFrame({
            "predicted": [1, 1, 0, 0, 1, 1, 0, 0, 1, 1],
            "confidence": [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05]
        })
        actuals = pd.Series([1, 1, 0, 1, 0, 0, 0, 1, 0, 1])

        results = calculate_accuracy_by_confidence(
            predictions, actuals, "confidence", n_buckets=5
        )

        assert len(results) > 0
        for decile, metrics in results.items():
            assert "count" in metrics
            assert "accuracy" in metrics
            assert "avg_confidence" in metrics

    def test_higher_confidence_should_have_higher_accuracy_ideally(self):
        """High confidence buckets should ideally have higher accuracy."""
        import pandas as pd
        from g2.ml.evaluation import calculate_accuracy_by_confidence

        # Well-calibrated model: confidence matches accuracy
        n = 100
        predictions = pd.DataFrame({
            "predicted": [1] * n,
            "confidence": np.linspace(0.1, 0.9, n)
        })
        # Actuals are correct when confidence > random threshold
        actuals = pd.Series([
            1 if predictions.iloc[i]["confidence"] > np.random.rand() else 0
            for i in range(n)
        ])

        results = calculate_accuracy_by_confidence(
            predictions, actuals, "confidence", n_buckets=5
        )

        # Just verify it runs and returns data
        assert len(results) > 0

    def test_accuracy_bounded_zero_to_one(self):
        """Accuracy values should be in [0, 1] range."""
        import pandas as pd
        from g2.ml.evaluation import calculate_accuracy_by_confidence

        predictions = pd.DataFrame({
            "predicted": np.random.randint(0, 2, 50),
            "confidence": np.random.rand(50)
        })
        actuals = pd.Series(np.random.randint(0, 2, 50))

        results = calculate_accuracy_by_confidence(
            predictions, actuals, "confidence", n_buckets=5
        )

        for decile, metrics in results.items():
            assert 0.0 <= metrics["accuracy"] <= 1.0
            assert 0.0 <= metrics["avg_confidence"] <= 1.0


class TestReliabilityDiagramData:
    """Tests for get_reliability_diagram_data function."""

    def test_returns_plot_data(self):
        """Returns data suitable for reliability diagram plotting."""
        from g2.ml.evaluation import get_reliability_diagram_data

        predicted_probs = np.array([0.1, 0.3, 0.5, 0.7, 0.9, 0.15, 0.35, 0.55, 0.75, 0.85])
        actual_outcomes = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0])

        data = get_reliability_diagram_data(predicted_probs, actual_outcomes, n_bins=5)

        assert "mean_predicted_probs" in data
        assert "fraction_positive" in data
        assert "bin_counts" in data
        assert len(data["mean_predicted_probs"]) == len(data["fraction_positive"])

    def test_perfect_calibration_lies_on_diagonal(self):
        """Perfect calibration: mean_predicted ≈ fraction_positive."""
        from g2.ml.evaluation import get_reliability_diagram_data

        # Perfectly calibrated
        n = 1000
        predicted_probs = np.random.rand(n)
        actual_outcomes = (np.random.rand(n) < predicted_probs).astype(int)

        data = get_reliability_diagram_data(predicted_probs, actual_outcomes, n_bins=10)

        # Check that mean predicted and fraction positive are close
        for mp, fp in zip(data["mean_predicted_probs"], data["fraction_positive"]):
            assert abs(mp - fp) < 0.2  # Allow some variance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
