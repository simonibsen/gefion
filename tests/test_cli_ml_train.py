"""Tests for g2 ml train command."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import g2.cli as cli


@pytest.fixture
def mock_dataset_manifest(tmp_path):
    """Create a mock dataset manifest for testing."""
    manifest = {
        "name": "test_dataset",
        "version": "v1",
        "universe": {"symbols": ["AAPL", "MSFT"]},
        "feature_names": ["rsi_14", "macd"],
        "lookback_days": 100,
        "horizons_days": [7, 30],
        "label_spec": {
            "type": "forward_return_5class",
            "thresholds": {
                "7": {"weak": 0.02, "strong": 0.05},
                "30": {"weak": 0.05, "strong": 0.10},
            },
        },
        "split_spec": {"type": "walk_forward"},
        "artifact_uri": str(tmp_path / "dataset.json"),
    }
    manifest_path = tmp_path / "dataset.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest


def test_ml_train_requires_dataset_name():
    """Test that ml train requires --dataset-name."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train"])
    assert result.exit_code != 0
    assert "dataset-name" in result.output.lower() or "required" in result.output.lower()


def test_ml_train_requires_dataset_version():
    """Test that ml train requires --dataset-version."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--dataset-name", "test"])
    assert result.exit_code != 0
    assert "dataset-version" in result.output.lower() or "required" in result.output.lower()


def test_ml_train_requires_model_name():
    """Test that ml train requires --model-name."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--dataset-name", "test", "--dataset-version", "v1"])
    assert result.exit_code != 0


@pytest.mark.skip(reason="Requires database with ml_datasets table - TODO: add DB fixture")
def test_ml_train_creates_model_artifact(tmp_path, mock_dataset_manifest):
    """Test that ml train creates a model artifact file."""
    runner = CliRunner()
    model_dir = tmp_path / "models"
    result = runner.invoke(
        cli.app,
        [
            "ml",
            "train",
            "--dataset-name",
            "test_dataset",
            "--dataset-version",
            "v1",
            "--model-name",
            "test_model",
            "--model-version",
            "v1",
            "--out-dir",
            str(model_dir),
        ],
    )
    assert result.exit_code == 0
    # Should create model artifact
    assert (model_dir / "test_model_v1.pkl").exists() or any(model_dir.glob("*.pkl"))


def test_ml_train_registers_model_in_db(tmp_path):
    """Test that ml train registers the model in ml_models table."""
    # This test would check that after training, a row exists in ml_models
    pass  # TODO: Implement with actual DB fixture


def test_ml_train_creates_run_record():
    """Test that ml train creates a record in ml_runs table."""
    # This test would verify ml_runs has a new row with run_type='train'
    pass  # TODO: Implement with actual DB fixture


def test_ml_train_stores_metrics():
    """Test that ml train stores training metrics in ml_models."""
    # Should store metrics like train_loss, val_loss, etc. in JSONB field
    pass  # TODO: Implement with actual DB fixture
