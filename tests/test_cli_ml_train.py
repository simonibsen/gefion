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


# Hyperparameter CLI option tests


def test_ml_train_accepts_learning_rate_option():
    """Test that ml train accepts --learning-rate option."""
    runner = CliRunner()
    # This should fail for missing required args, not for unknown option
    result = runner.invoke(cli.app, ["ml", "train", "--learning-rate", "0.05"])
    # Check the error is about missing required options, not unknown option
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_n_estimators_option():
    """Test that ml train accepts --n-estimators option."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--n-estimators", "200"])
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_max_depth_option():
    """Test that ml train accepts --max-depth option."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--max-depth", "8"])
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_min_child_weight_option():
    """Test that ml train accepts --min-child-weight option."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--min-child-weight", "5"])
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_subsample_option():
    """Test that ml train accepts --subsample option."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--subsample", "0.8"])
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_colsample_bytree_option():
    """Test that ml train accepts --colsample-bytree option."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--colsample-bytree", "0.8"])
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_reg_alpha_option():
    """Test that ml train accepts --reg-alpha option (L1 regularization)."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--reg-alpha", "0.1"])
    assert "no such option" not in result.output.lower()


def test_ml_train_accepts_reg_lambda_option():
    """Test that ml train accepts --reg-lambda option (L2 regularization)."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--reg-lambda", "1.0"])
    assert "no such option" not in result.output.lower()


def test_ml_train_help_shows_hyperparameter_options():
    """Test that ml train --help shows all hyperparameter options."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "train", "--help"])
    assert result.exit_code == 0
    # Check all hyperparameter options are documented
    assert "--learning-rate" in result.output
    assert "--n-estimators" in result.output
    assert "--max-depth" in result.output
    assert "--min-child-weight" in result.output
    assert "--subsample" in result.output
    assert "--colsample-bytree" in result.output
    assert "--reg-alpha" in result.output
    assert "--reg-lambda" in result.output
