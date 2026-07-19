"""GPU detection + device provenance (#146).

TDD: written FIRST. The owner intent is "use GPU when available"; the old
CUDA probe imported torch (never installed — constitution: no deep
learning), so detection silently returned cpu on GPU hosts, and the
experiment path never consulted detection at all. The fix: a torch-free
probe (nvidia-smi + micro-validation with graceful fallback), auto-detect
as the train_quantile_model default, LightGBM safety (pip wheel is
CPU-only — warn and train on cpu, never error), and device recorded in
model provenance so reproduction paths can pin it.
"""
import numpy as np
import pandas as pd
import pytest

from gefion.ml import device as mldevice


def _tiny_xy(n=60):
    rng = np.random.default_rng(7)
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    y = pd.Series(rng.normal(size=n))
    return X, y


class TestTorchFreeDetection:
    def test_no_torch_no_nvidia_smi_is_cpu(self, monkeypatch):
        monkeypatch.setattr(mldevice, "_torch_cuda_available", lambda: None)
        monkeypatch.setattr(mldevice.shutil, "which", lambda _: None)
        assert mldevice.detect_device() == "cpu"

    def test_nvidia_smi_present_and_validated_is_cuda(self, monkeypatch):
        monkeypatch.setattr(mldevice, "_torch_cuda_available", lambda: None)
        monkeypatch.setattr(mldevice.shutil, "which",
                            lambda _: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(mldevice, "_nvidia_smi_ok", lambda: True)
        monkeypatch.setattr(mldevice, "_validate_cuda_training", lambda: True)
        info = mldevice.detect_device(return_info=True)
        assert info["device"] == "cuda"
        assert info["cuda_available"] is True

    def test_failed_validation_falls_back_to_cpu(self, monkeypatch):
        """A GPU that nvidia-smi sees but training can't use must fall back
        gracefully — never break training."""
        monkeypatch.setattr(mldevice, "_torch_cuda_available", lambda: None)
        monkeypatch.setattr(mldevice.shutil, "which",
                            lambda _: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(mldevice, "_nvidia_smi_ok", lambda: True)
        monkeypatch.setattr(mldevice, "_validate_cuda_training", lambda: False)
        assert mldevice.detect_device() == "cpu"

    def test_torch_answer_wins_when_torch_exists(self, monkeypatch):
        monkeypatch.setattr(mldevice, "_torch_cuda_available", lambda: True)
        assert mldevice.detect_device() == "cuda"

    def test_force_device_still_honored(self):
        assert mldevice.detect_device(force_device="cpu") == "cpu"
        with pytest.raises(ValueError):
            mldevice.detect_device(force_device="tpu")


class TestDeviceProvenance:
    def test_default_is_auto_detect_and_recorded(self, monkeypatch):
        """train_quantile_model defaults to auto-detect (device=None) so the
        experiment framework inherits GPU support with zero call changes,
        and the resolved device lands in the returned model data."""
        from gefion.ml import models
        monkeypatch.setattr(models, "detect_device", lambda: "cpu")
        X, y = _tiny_xy()
        data = models.train_quantile_model(
            X, y, algorithm="quantile_regression", quantiles=[0.5])
        assert data["device"] == "cpu"

    def test_signature_default_is_none(self):
        import inspect
        from gefion.ml.models import train_quantile_model
        assert inspect.signature(train_quantile_model) \
            .parameters["device"].default is None

    def test_explicit_device_recorded(self):
        from gefion.ml import models
        X, y = _tiny_xy()
        data = models.train_quantile_model(
            X, y, algorithm="quantile_regression", quantiles=[0.5],
            device="cpu")
        assert data["device"] == "cpu"

    def test_lightgbm_cuda_falls_back_to_cpu_never_errors(self):
        """The pip LightGBM wheel has no GPU support: requesting cuda must
        warn and train on cpu — never raise, and provenance must record the
        device actually used."""
        from gefion.ml import models
        X, y = _tiny_xy()
        data = models.train_quantile_model(
            X, y, algorithm="lightgbm", quantiles=[0.5], device="cuda",
            hyperparams={"n_estimators": 5})
        assert data["device"] == "cpu"       # what actually ran
        assert "q50" in data["models"]

    def test_artifact_metadata_carries_device(self, tmp_path):
        from gefion.ml import models
        X, y = _tiny_xy()
        data = models.train_quantile_model(
            X, y, algorithm="quantile_regression", quantiles=[0.5],
            device="cpu")
        models.save_model_artifact(data, tmp_path / "m", {"algorithm":
                                                          "quantile_regression"})
        loaded = models.load_model_artifact(tmp_path / "m")
        assert loaded["device"] == "cpu"

    def test_warm_start_pins_base_model_device(self, tmp_path, monkeypatch):
        """Reproduction paths pin the ORIGINAL device: retraining from a
        base artifact inherits its recorded device rather than auto-detect
        (GPU/CPU training is not numerically identical)."""
        from gefion.ml import models
        X, y = _tiny_xy()
        base = models.train_quantile_model(
            X, y, algorithm="lightgbm", quantiles=[0.5], device="cpu",
            hyperparams={"n_estimators": 5})
        models.save_model_artifact(base, tmp_path / "base",
                                   {"algorithm": "lightgbm"})
        # auto-detect would say cuda — the base artifact must win
        monkeypatch.setattr(models, "detect_device", lambda: "cuda")
        data = models.train_quantile_model(
            X, y, algorithm="lightgbm", quantiles=[0.5],
            hyperparams={"n_estimators": 5},
            base_model_path=tmp_path / "base")
        assert data["device"] == "cpu"
        assert data["warm_start"] is True
