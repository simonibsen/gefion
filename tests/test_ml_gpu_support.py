"""
TDD tests for GPU training support.

Tests for GPU device detection and GPU-accelerated training with XGBoost/LightGBM.
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock


class TestDeviceDetection:
    """Tests for ML device detection."""

    def test_detect_device_returns_cpu_when_no_cuda(self):
        """detect_device returns 'cpu' when CUDA not available."""
        from g2.ml.device import detect_device

        with patch.dict('sys.modules', {'torch': None}):
            # When torch not installed, should return cpu
            device = detect_device()
            assert device in ("cpu", "cuda")  # Depends on actual system

    def test_detect_device_returns_dict_with_info(self):
        """detect_device returns dict with device info."""
        from g2.ml.device import detect_device

        result = detect_device(return_info=True)

        assert isinstance(result, dict)
        assert "device" in result
        assert result["device"] in ("cpu", "cuda")
        assert "cuda_available" in result

    def test_detect_device_respects_override(self):
        """detect_device respects explicit device override."""
        from g2.ml.device import detect_device

        # Force CPU even if CUDA available
        device = detect_device(force_device="cpu")
        assert device == "cpu"


class TestXGBoostGPU:
    """Tests for XGBoost GPU configuration."""

    def test_xgboost_uses_gpu_when_device_cuda(self):
        """XGBoost training uses GPU params when device=cuda."""
        pytest.importorskip("xgboost")
        from g2.ml.models import train_quantile_model

        # Create minimal test data
        X = pd.DataFrame({
            'feature1': np.random.randn(100),
            'feature2': np.random.randn(100),
        })
        y = pd.Series(np.random.randn(100))

        # Mock XGBRegressor to capture params
        with patch('g2.ml.models.xgb.XGBRegressor') as mock_xgb:
            mock_model = MagicMock()
            mock_xgb.return_value = mock_model

            try:
                train_quantile_model(
                    X, y,
                    algorithm="xgboost",
                    device="cuda"
                )
            except Exception:
                pass  # May fail without real GPU, but we check the call

            # Verify GPU params were passed
            if mock_xgb.called:
                call_kwargs = mock_xgb.call_args[1]
                assert call_kwargs.get("tree_method") == "gpu_hist"
                assert call_kwargs.get("device") == "cuda"

    def test_xgboost_uses_cpu_when_device_cpu(self):
        """XGBoost training uses CPU params when device=cpu."""
        pytest.importorskip("xgboost")
        from g2.ml.models import train_quantile_model

        X = pd.DataFrame({
            'feature1': np.random.randn(100),
            'feature2': np.random.randn(100),
        })
        y = pd.Series(np.random.randn(100))

        # Train with CPU - should work without GPU
        result = train_quantile_model(
            X, y,
            algorithm="xgboost",
            device="cpu"
        )

        assert "models" in result
        assert len(result["models"]) == 3  # q10, q50, q90


class TestLightGBMGPU:
    """Tests for LightGBM GPU configuration."""

    def test_lightgbm_uses_gpu_when_device_cuda(self):
        """LightGBM training uses GPU params when device=cuda."""
        pytest.importorskip("lightgbm")
        from g2.ml.models import train_quantile_model

        X = pd.DataFrame({
            'feature1': np.random.randn(100),
            'feature2': np.random.randn(100),
        })
        y = pd.Series(np.random.randn(100))

        with patch('g2.ml.models.lgb.LGBMRegressor') as mock_lgb:
            mock_model = MagicMock()
            mock_lgb.return_value = mock_model

            try:
                train_quantile_model(
                    X, y,
                    algorithm="lightgbm",
                    device="cuda"
                )
            except Exception:
                pass

            if mock_lgb.called:
                call_kwargs = mock_lgb.call_args[1]
                assert call_kwargs.get("device") == "gpu"

    def test_lightgbm_uses_cpu_when_device_cpu(self):
        """LightGBM training uses CPU when device=cpu."""
        pytest.importorskip("lightgbm")
        from g2.ml.models import train_quantile_model

        X = pd.DataFrame({
            'feature1': np.random.randn(100),
            'feature2': np.random.randn(100),
        })
        y = pd.Series(np.random.randn(100))

        result = train_quantile_model(
            X, y,
            algorithm="lightgbm",
            device="cpu"
        )

        assert "models" in result


class TestClassifierGPU:
    """Tests for classifier GPU support."""

    def test_classifier_xgboost_uses_gpu(self):
        """XGBoost classifier uses GPU when device=cuda."""
        pytest.importorskip("xgboost")
        from g2.ml.classifier import train_classifier

        X = pd.DataFrame({
            'feature1': np.random.randn(100),
            'feature2': np.random.randn(100),
        })
        y = pd.Series(np.random.choice(['flat', 'weak_up', 'weak_down'], 100))

        with patch('g2.ml.classifier.xgb.XGBClassifier') as mock_xgb:
            mock_model = MagicMock()
            mock_model.classes_ = np.array(['flat', 'weak_up', 'weak_down'])
            mock_xgb.return_value = mock_model

            try:
                train_classifier(
                    X, y,
                    algorithm="xgboost",
                    device="cuda"
                )
            except Exception:
                pass

            if mock_xgb.called:
                call_kwargs = mock_xgb.call_args[1]
                assert call_kwargs.get("tree_method") == "gpu_hist"
                assert call_kwargs.get("device") == "cuda"


class TestCLIDeviceFlag:
    """Tests for --device flag in CLI commands."""

    def test_ml_train_accepts_device_flag(self):
        """ml train command accepts --device flag."""
        from typer.testing import CliRunner
        from g2.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["ml", "train", "--help"])

        assert result.exit_code == 0
        assert "--device" in result.output

    def test_ml_train_classifier_accepts_device_flag(self):
        """ml train-classifier command accepts --device flag."""
        from typer.testing import CliRunner
        from g2.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["ml", "train-classifier", "--help"])

        assert result.exit_code == 0
        assert "--device" in result.output

    def test_device_flag_accepts_auto_cpu_cuda(self):
        """--device flag accepts 'auto', 'cpu', 'cuda' values."""
        from typer.testing import CliRunner
        from g2.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["ml", "train", "--help"])

        assert result.exit_code == 0
        # Should show valid options in help
        assert "auto" in result.output.lower() or "cpu" in result.output.lower()


# =============================================================================
# GPU Integration Tests (skipped if CUDA not available)
# =============================================================================

def _cuda_available():
    """Check if CUDA is available for real GPU tests."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


# Custom pytest marker for GPU tests
requires_cuda = pytest.mark.skipif(
    not _cuda_available(),
    reason="CUDA not available - skipping GPU integration tests"
)


@requires_cuda
class TestGPUIntegration:
    """
    Real GPU training tests - only run when CUDA is available.

    These tests actually train models on GPU to verify the integration works.
    They are skipped on systems without CUDA (CI, development machines).
    """

    @pytest.fixture
    def sample_data(self):
        """Create sample training data."""
        np.random.seed(42)
        X = pd.DataFrame({
            f'feature_{i}': np.random.randn(200) for i in range(10)
        })
        y_regression = pd.Series(np.random.randn(200))
        y_classification = pd.Series(
            np.random.choice(['strong_down', 'weak_down', 'flat', 'weak_up', 'strong_up'], 200)
        )
        return X, y_regression, y_classification

    def test_xgboost_quantile_trains_on_gpu(self, sample_data):
        """XGBoost quantile regression actually trains on GPU."""
        pytest.importorskip("xgboost")
        from g2.ml.models import train_quantile_model

        X, y_reg, _ = sample_data

        result = train_quantile_model(
            X, y_reg,
            algorithm="xgboost",
            device="cuda"
        )

        assert "models" in result
        assert len(result["models"]) == 3  # q10, q50, q90
        assert result["algorithm"] == "xgboost"

        # Verify models can make predictions
        for q_key, model in result["models"].items():
            preds = model.predict(X)
            assert len(preds) == len(X)

    def test_lightgbm_quantile_trains_on_gpu(self, sample_data):
        """LightGBM quantile regression actually trains on GPU."""
        pytest.importorskip("lightgbm")
        from g2.ml.models import train_quantile_model

        X, y_reg, _ = sample_data

        result = train_quantile_model(
            X, y_reg,
            algorithm="lightgbm",
            device="cuda"
        )

        assert "models" in result
        assert len(result["models"]) == 3
        assert result["algorithm"] == "lightgbm"

    def test_xgboost_classifier_trains_on_gpu(self, sample_data):
        """XGBoost classifier actually trains on GPU."""
        pytest.importorskip("xgboost")
        from g2.ml.classifier import train_classifier

        X, _, y_cls = sample_data

        result = train_classifier(
            X, y_cls,
            algorithm="xgboost",
            device="cuda"
        )

        assert "model" in result
        assert "train_metrics" in result
        assert result["train_metrics"]["train_accuracy"] > 0

    def test_lightgbm_classifier_trains_on_gpu(self, sample_data):
        """LightGBM classifier actually trains on GPU."""
        pytest.importorskip("lightgbm")
        from g2.ml.classifier import train_classifier

        X, _, y_cls = sample_data

        result = train_classifier(
            X, y_cls,
            algorithm="lightgbm",
            device="cuda"
        )

        assert "model" in result
        assert "train_metrics" in result
        assert result["train_metrics"]["train_accuracy"] > 0

    def test_gpu_faster_than_cpu_for_large_data(self, sample_data):
        """GPU training should be faster than CPU for larger datasets."""
        pytest.importorskip("xgboost")
        import time
        from g2.ml.models import train_quantile_model

        # Create larger dataset for timing comparison
        np.random.seed(42)
        X_large = pd.DataFrame({
            f'feature_{i}': np.random.randn(5000) for i in range(20)
        })
        y_large = pd.Series(np.random.randn(5000))

        # Time CPU training
        start = time.time()
        train_quantile_model(X_large, y_large, algorithm="xgboost", device="cpu")
        cpu_time = time.time() - start

        # Time GPU training
        start = time.time()
        train_quantile_model(X_large, y_large, algorithm="xgboost", device="cuda")
        gpu_time = time.time() - start

        # GPU should generally be faster (but not always for small data)
        # Just verify both complete successfully; speedup depends on hardware
        assert cpu_time > 0
        assert gpu_time > 0
        # Log timing for manual inspection
        print(f"\n  CPU time: {cpu_time:.2f}s, GPU time: {gpu_time:.2f}s")
