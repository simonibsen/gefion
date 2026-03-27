"""Tests for ml model-inspect command."""

import pytest
from typer.testing import CliRunner

from gefion.cli import app


runner = CliRunner()


class TestModelInspectCommand:
    """Test ml model-inspect CLI command."""

    def test_command_exists(self):
        """model-inspect command should be registered."""
        result = runner.invoke(app, ["ml", "model-inspect", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output
        assert "--version" in result.output

    def test_requires_name_and_version(self):
        """Command should require --name and --version options."""
        result = runner.invoke(app, ["ml", "model-inspect"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_accepts_json_output(self):
        """Command should accept --json flag."""
        result = runner.invoke(app, ["ml", "model-inspect", "--help"])
        assert "--json" in result.output

    def test_shows_model_not_found(self, monkeypatch):
        """Should report when model not found."""
        # Mock database to return no results
        class DummyCursor:
            def execute(self, *args):
                pass

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from gefion import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, [
            "ml", "model-inspect",
            "--name", "nonexistent",
            "--version", "v1",
        ])

        assert "not found" in result.output.lower()


class TestModelInspectOutput:
    """Test model-inspect output format."""

    def test_json_output_includes_model_info(self, monkeypatch):
        """JSON output should include model metadata."""
        from datetime import datetime

        class DummyCursor:
            def __init__(self):
                self._call_count = 0

            def execute(self, *args):
                self._call_count += 1

            def fetchone(self):
                if self._call_count == 1:
                    # Model info
                    return (
                        1, "test_model", "v1", datetime(2024, 1, 1),
                        "xgboost", {"n_estimators": 100},
                        {"train_loss": 0.01}, "models/test_model_v1",
                        True, 1, "test_dataset", "v1"
                    )
                return None

            def fetchall(self):
                # Predictions count
                return []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from gefion import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, [
            "ml", "model-inspect",
            "--name", "test_model",
            "--version", "v1",
            "--json",
        ])

        assert result.exit_code == 0
        assert "test_model" in result.output
