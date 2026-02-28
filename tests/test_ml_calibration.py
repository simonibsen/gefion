"""Tests for ML model calibration via conformal prediction shifts.

TDD: These tests are written BEFORE the calibration module implementation.
"""
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


class TestComputeCalibrationShifts:
    """Tests for computing conformal calibration shifts."""

    def test_compute_calibration_shifts_perfect(self):
        """Shifts should be ~0 when predictions are perfectly calibrated."""
        from g2.ml.calibration import compute_calibration_shifts

        np.random.seed(42)
        n = 1000
        # Perfect predictions: actuals drawn so that quantiles match
        actuals = pd.Series(np.random.randn(n))
        predictions = pd.DataFrame({
            "q10": np.full(n, np.quantile(actuals, 0.1)),
            "q50": np.full(n, np.quantile(actuals, 0.5)),
            "q90": np.full(n, np.quantile(actuals, 0.9)),
        })

        shifts = compute_calibration_shifts(predictions, actuals)

        assert "q10" in shifts
        assert "q50" in shifts
        assert "q90" in shifts
        # Shifts should be near 0 for well-calibrated predictions
        for key in ["q10", "q50", "q90"]:
            assert abs(shifts[key]) < 0.15, f"Shift for {key} too large: {shifts[key]}"

    def test_compute_calibration_shifts_biased(self):
        """Positive shifts for a model that consistently underpredicts."""
        from g2.ml.calibration import compute_calibration_shifts

        np.random.seed(42)
        n = 500
        actuals = pd.Series(np.random.randn(n) + 1.0)  # Actual values centered at +1
        predictions = pd.DataFrame({
            "q10": np.full(n, -0.5),  # Predictions centered too low
            "q50": np.full(n, 0.0),
            "q90": np.full(n, 0.5),
        })

        shifts = compute_calibration_shifts(predictions, actuals)

        # All shifts should be positive (need to shift predictions upward)
        assert shifts["q10"] > 0, f"q10 shift should be positive: {shifts['q10']}"
        assert shifts["q50"] > 0, f"q50 shift should be positive: {shifts['q50']}"
        assert shifts["q90"] > 0, f"q90 shift should be positive: {shifts['q90']}"

    def test_compute_calibration_shifts_per_quantile(self):
        """Different quantiles should get different shift values."""
        from g2.ml.calibration import compute_calibration_shifts

        np.random.seed(42)
        n = 500
        actuals = pd.Series(np.random.randn(n))
        # Deliberately off-target predictions
        predictions = pd.DataFrame({
            "q10": np.full(n, -2.0),  # Way too low
            "q50": np.full(n, 0.5),   # Slightly high
            "q90": np.full(n, 1.0),   # Slightly low
        })

        shifts = compute_calibration_shifts(predictions, actuals)

        # Shifts should differ across quantiles
        assert shifts["q10"] != shifts["q50"] or shifts["q50"] != shifts["q90"], \
            "All shifts are identical; expected per-quantile differences"


class TestApplyCalibrationShifts:
    """Tests for applying calibration shifts to predictions."""

    def test_apply_calibration_shifts(self):
        """Predictions should be adjusted by the shift amounts."""
        from g2.ml.calibration import apply_calibration_shifts

        predictions = pd.DataFrame({
            "q10": [0.1, 0.2],
            "q50": [0.5, 0.6],
            "q90": [0.9, 1.0],
        })
        shifts = {"q10": -0.05, "q50": 0.02, "q90": 0.01}

        result = apply_calibration_shifts(predictions, shifts)

        np.testing.assert_allclose(result["q10"].values, [0.05, 0.15], atol=1e-10)
        np.testing.assert_allclose(result["q50"].values, [0.52, 0.62], atol=1e-10)
        np.testing.assert_allclose(result["q90"].values, [0.91, 1.01], atol=1e-10)

    def test_apply_calibration_preserves_ordering(self):
        """After shifts, q10 <= q50 <= q90 must still hold."""
        from g2.ml.calibration import apply_calibration_shifts

        predictions = pd.DataFrame({
            "q10": [0.10, 0.20],
            "q50": [0.11, 0.21],  # Very close to q10
            "q90": [0.12, 0.22],  # Very close to q50
        })
        # Shift q10 up and q90 down — could cause ordering violation
        shifts = {"q10": 0.05, "q50": 0.0, "q90": -0.05}

        result = apply_calibration_shifts(predictions, shifts)

        # Ordering must be enforced
        assert (result["q10"] <= result["q50"]).all(), "q10 > q50 after calibration"
        assert (result["q50"] <= result["q90"]).all(), "q50 > q90 after calibration"


class TestCalibrationPersistence:
    """Tests for saving and loading calibration artifacts."""

    def test_save_calibration_json(self):
        """Should write correct JSON structure to artifact directory."""
        from g2.ml.calibration import save_calibration

        shifts = {"q10": -0.023, "q50": 0.015, "q90": 0.008}
        metadata = {
            "calibration_period": {"start_date": "2025-06-01", "end_date": "2025-12-31"},
            "num_samples": 1250,
            "before_metrics": {"q10_calibration": 2.4, "q50_calibration": 26.2},
            "after_metrics": {"q10_calibration": 9.8, "q50_calibration": 49.1},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "model_h7"
            artifact_path.mkdir()
            save_calibration(shifts, artifact_path, metadata)

            cal_file = artifact_path / "calibration.json"
            assert cal_file.exists(), "calibration.json not created"

            with open(cal_file) as f:
                data = json.load(f)

            assert data["shifts"] == shifts
            assert data["calibration_period"]["start_date"] == "2025-06-01"
            assert data["num_samples"] == 1250
            assert "calibrated_at" in data

    def test_load_calibration_json(self):
        """Should read and parse calibration.json correctly."""
        from g2.ml.calibration import save_calibration, load_calibration

        shifts = {"q10": -0.01, "q50": 0.02, "q90": 0.03}
        metadata = {"num_samples": 100}

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir)
            save_calibration(shifts, artifact_path, metadata)

            loaded = load_calibration(artifact_path)

            assert loaded is not None
            assert loaded["shifts"] == shifts
            assert loaded["num_samples"] == 100

    def test_load_calibration_missing_returns_none(self):
        """Should return None gracefully when no calibration.json exists."""
        from g2.ml.calibration import load_calibration

        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_calibration(Path(tmpdir))
            assert result is None


class TestCalibrationReport:
    """Tests for calibration report generation."""

    def test_calibration_report(self):
        """Should produce a formatted before/after report."""
        from g2.ml.calibration import generate_calibration_report

        before_metrics = {"q10_calibration": 2.4, "q50_calibration": 26.2, "q90_calibration": 85.7}
        after_metrics = {"q10_calibration": 9.8, "q50_calibration": 49.1, "q90_calibration": 89.5}
        shifts_by_horizon = {
            7: {"shifts": {"q10": -0.02, "q50": 0.01, "q90": 0.005},
                "before": before_metrics, "after": after_metrics},
        }

        report = generate_calibration_report("test_model", shifts_by_horizon)

        assert isinstance(report, str)
        assert "test_model" in report
        assert "Before" in report or "before" in report.lower()
        assert "After" in report or "after" in report.lower()
        # Should show calibration improvement
        assert "2.4" in report or "q10" in report.lower()


class TestPinballLossScorer:
    """Tests for the sklearn-compatible pinball loss scorer."""

    def test_pinball_loss_scorer(self):
        """Scorer should be compatible with sklearn cross_val_score."""
        from g2.ml.calibration import create_pinball_loss_scorer
        from sklearn.linear_model import QuantileRegressor
        from sklearn.model_selection import cross_val_score

        np.random.seed(42)
        X = np.random.randn(100, 3)
        y = X[:, 0] * 2 + np.random.randn(100) * 0.5

        scorer = create_pinball_loss_scorer(0.5)
        model = QuantileRegressor(quantile=0.5, alpha=0.1, solver="highs")

        # Should work with cross_val_score without error
        scores = cross_val_score(model, X, y, cv=3, scoring=scorer)

        assert len(scores) == 3
        # Scores should be negative (sklearn convention for loss-based scorers)
        assert all(s <= 0 for s in scores), f"Expected negative scores, got {scores}"
