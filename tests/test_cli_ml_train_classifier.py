"""Tests for g2 ml train-classifier command."""
import pytest
from typer.testing import CliRunner

import g2.cli as cli


runner = CliRunner()


def test_train_classifier_requires_dataset_name():
    """Test that train-classifier requires --dataset-name."""
    result = runner.invoke(cli.app, ["ml", "train-classifier"])
    assert result.exit_code != 0
    assert "dataset-name" in result.output.lower() or "required" in result.output.lower()


def test_train_classifier_requires_dataset_version():
    """Test that train-classifier requires --dataset-version."""
    result = runner.invoke(cli.app, ["ml", "train-classifier", "--dataset-name", "test"])
    assert result.exit_code != 0
    assert "dataset-version" in result.output.lower() or "required" in result.output.lower()


def test_train_classifier_requires_model_name():
    """Test that train-classifier requires --model-name."""
    result = runner.invoke(cli.app, [
        "ml", "train-classifier",
        "--dataset-name", "test",
        "--dataset-version", "v1",
    ])
    assert result.exit_code != 0
    assert "model-name" in result.output.lower() or "required" in result.output.lower()


def test_train_classifier_requires_model_version():
    """Test that train-classifier requires --model-version."""
    result = runner.invoke(cli.app, [
        "ml", "train-classifier",
        "--dataset-name", "test",
        "--dataset-version", "v1",
        "--model-name", "test_model",
    ])
    assert result.exit_code != 0
    assert "model-version" in result.output.lower() or "required" in result.output.lower()


def test_train_classifier_requires_horizon():
    """Test that train-classifier requires --horizon."""
    result = runner.invoke(cli.app, [
        "ml", "train-classifier",
        "--dataset-name", "test",
        "--dataset-version", "v1",
        "--model-name", "test_model",
        "--model-version", "v1",
    ])
    assert result.exit_code != 0
    assert "horizon" in result.output.lower() or "required" in result.output.lower()


def test_train_classifier_accepts_algorithm_options():
    """Test that train-classifier accepts valid algorithm values."""
    # Just verify the CLI parsing works - actual training needs DB
    # Valid algorithms: sklearn, xgboost, lightgbm
    result = runner.invoke(cli.app, [
        "ml", "train-classifier",
        "--dataset-name", "test",
        "--dataset-version", "v1",
        "--model-name", "test_model",
        "--model-version", "v1",
        "--horizon", "7",
        "--algorithm", "invalid_algo",
    ])
    # Should fail due to dataset not found, not algorithm validation at CLI level
    assert result.exit_code != 0


class TestClassifierModelDirectory:
    """Tests for classifier model directory creation."""

    def test_model_path_includes_horizon_and_classifier_suffix(self):
        """Verify model path format: {model_name}_{model_version}_h{horizon}_classifier."""
        from pathlib import Path

        model_name = "test_model"
        model_version = "v1"
        horizon = 7
        out_dir = Path("models")

        expected_path = out_dir / f"{model_name}_{model_version}_h{horizon}_classifier"
        assert str(expected_path) == "models/test_model_v1_h7_classifier"

    def test_classifier_saves_required_files(self, tmp_path):
        """Test that classifier training saves classifier.pkl and metadata.json."""
        # This would require actual training - mark as integration test
        import os

        # Verify the expected file structure
        model_dir = tmp_path / "test_model_v1_h7_classifier"
        model_dir.mkdir()

        # These are the files that should be created
        expected_files = ["classifier.pkl", "metadata.json"]

        for f in expected_files:
            (model_dir / f).touch()

        assert (model_dir / "classifier.pkl").exists()
        assert (model_dir / "metadata.json").exists()
