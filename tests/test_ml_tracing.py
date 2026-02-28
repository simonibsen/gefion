"""Tests that ML pipeline functions are instrumented with tracing spans.

Verifies that top-level ML functions wrap their work in create_span
so that traces show meaningful span names for ML operations.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

import g2.ml.models as models_mod
import g2.ml.dataset as dataset_mod
import g2.ml.evaluation as eval_mod
import g2.ml.classifier as classifier_mod
import g2.ml.importance as importance_mod


class SpanCollector:
    """Collect span names created during a test."""

    def __init__(self):
        self.spans: list[str] = []

    @contextmanager
    def fake_create_span(self, name: str, **attrs):
        self.spans.append(name)
        yield MagicMock()


@pytest.fixture
def collector():
    return SpanCollector()


@pytest.fixture
def sample_X():
    return pd.DataFrame({"feat_a": [1.0, 2.0, 3.0], "feat_b": [4.0, 5.0, 6.0]})


@pytest.fixture
def sample_y():
    return pd.Series([0.01, -0.02, 0.03], name="return_7d")


def test_train_quantile_model_has_span(collector, sample_X, sample_y):
    """train_quantile_model() should create an ml.train span."""
    with patch.object(models_mod, "create_span", collector.fake_create_span):
        models_mod.train_quantile_model(sample_X, sample_y)

    assert "ml.train" in collector.spans, (
        f"Expected 'ml.train' span, got: {collector.spans}"
    )


def test_predict_quantiles_has_span(collector, sample_X):
    """predict_quantiles() should create an ml.predict span."""
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.1, 0.2, 0.3])
    model_data = {
        "models": {"q10": mock_model, "q50": mock_model, "q90": mock_model},
        "feature_names": ["feat_a", "feat_b"],
    }

    with patch.object(models_mod, "create_span", collector.fake_create_span):
        models_mod.predict_quantiles(model_data, sample_X.copy())

    assert "ml.predict" in collector.spans, (
        f"Expected 'ml.predict' span, got: {collector.spans}"
    )


def test_export_dataset_artifacts_has_span(collector):
    """export_dataset_artifacts() should create an ml.dataset_export span."""
    manifest = {
        "symbols": ["AAPL"],
        "horizons": [7],
        "data_ids": [1],
        "features": ["feat_a"],
        "format": "csv",
        "weak_thresholds": [0.02],
        "strong_thresholds": [0.05],
    }
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(dataset_mod, "create_span", collector.fake_create_span):
        try:
            dataset_mod.export_dataset_artifacts(
                mock_conn, manifest=manifest, out_dir=Path("/tmp/test_ds")
            )
        except Exception:
            pass  # May fail on DB queries — we only care about span creation

    assert "ml.dataset_export" in collector.spans, (
        f"Expected 'ml.dataset_export' span, got: {collector.spans}"
    )


def test_train_classifier_has_span(collector, sample_X):
    """train_classifier() should create an ml.train_classifier span."""
    y = pd.Series(["flat", "weak_up", "strong_down"])

    with patch.object(classifier_mod, "create_span", collector.fake_create_span):
        classifier_mod.train_classifier(sample_X, y)

    assert "ml.train_classifier" in collector.spans, (
        f"Expected 'ml.train_classifier' span, got: {collector.spans}"
    )


def test_predict_classifier_has_span(collector, sample_X):
    """predict_classifier() should create an ml.predict_classifier span."""
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([2, 1, 0])
    mock_model.predict_proba.return_value = np.array([[0.1, 0.2, 0.5, 0.1, 0.1]] * 3)
    mock_model.classes_ = np.array(["flat", "strong_down", "strong_up", "weak_down", "weak_up"])
    mock_encoder = MagicMock()
    mock_encoder.inverse_transform.return_value = np.array(["flat", "weak_up", "strong_down"])
    mock_encoder.classes_ = np.array(["flat", "strong_down", "strong_up", "weak_down", "weak_up"])
    model_artifacts = {
        "model": mock_model,
        "label_encoder": mock_encoder,
        "feature_names": ["feat_a", "feat_b"],
        "classes": ["flat", "strong_down", "strong_up", "weak_down", "weak_up"],
    }

    with patch.object(classifier_mod, "create_span", collector.fake_create_span):
        classifier_mod.predict_classifier(model_artifacts, sample_X.copy())

    assert "ml.predict_classifier" in collector.spans, (
        f"Expected 'ml.predict_classifier' span, got: {collector.spans}"
    )


def test_compute_shap_importance_has_span(collector):
    """compute_shap_importance() should create an ml.feature_importance span."""
    X_sample = pd.DataFrame({"feat_a": [1.0], "feat_b": [2.0]})

    with patch.object(importance_mod, "create_span", collector.fake_create_span), \
         patch.object(importance_mod, "joblib") as mock_joblib:
        # Mock loading the model artifact
        mock_pipeline = MagicMock()
        mock_pipeline.named_steps = {"model": MagicMock()}
        mock_joblib.load.return_value = mock_pipeline

        try:
            importance_mod.compute_shap_importance(
                model_path=Path("/tmp/fake_model"), X_sample=X_sample
            )
        except Exception:
            pass  # May fail on SHAP import — we only care about span creation

    assert "ml.feature_importance" in collector.spans, (
        f"Expected 'ml.feature_importance' span, got: {collector.spans}"
    )
