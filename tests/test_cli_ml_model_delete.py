"""Tests for ml model-delete command."""

import pytest
from typer.testing import CliRunner

from g2.cli import app


runner = CliRunner()


class TestModelDeleteCommand:
    """Test ml model-delete CLI command."""

    def test_command_exists(self):
        """model-delete command should be registered."""
        result = runner.invoke(app, ["ml", "model-delete", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output
        assert "--version" in result.output

    def test_requires_name_and_version(self):
        """Command should require --name and --version options."""
        result = runner.invoke(app, ["ml", "model-delete"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_accepts_json_output(self):
        """Command should accept --json flag."""
        result = runner.invoke(app, ["ml", "model-delete", "--help"])
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

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from g2 import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, [
            "ml", "model-delete",
            "--name", "nonexistent",
            "--version", "v1",
        ])

        assert "not found" in result.output.lower()


class TestModelDeleteOutput:
    """Test model-delete output format."""

    def test_json_output_on_delete(self, monkeypatch):
        """JSON output should include delete confirmation."""
        class DummyCursor:
            def __init__(self):
                self._call_count = 0

            def execute(self, *args):
                self._call_count += 1

            def fetchone(self):
                if self._call_count == 1:
                    # Model found: (id, artifact_uri)
                    return (1, None)
                # Prediction counts
                return (0,)

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from g2 import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, [
            "ml", "model-delete",
            "--name", "test_model",
            "--version", "v1",
            "--json",
        ])

        assert result.exit_code == 0
        assert "deleted" in result.output.lower()
