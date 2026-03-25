"""Tests for --json option on feature import/export commands."""

from typer.testing import CliRunner

from gefion import cli


runner = CliRunner()


def test_feat_def_import_accepts_json_option(tmp_path, monkeypatch):
    """feat-def-import should accept --json option."""
    # Mock db_connection to avoid database dependency
    class DummyCtx:
        def __enter__(self):
            return type("Conn", (), {"cursor": lambda self: DummyCursor()})()
        def __exit__(self, *args):
            return False

    class DummyCursor:
        def execute(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)
    monkeypatch.setattr(cli, "import_definitions_from_directory", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["feat-def-import", "--dir", str(tmp_path), "--json"])
    # Should not fail with "No such option: --json"
    assert result.exit_code == 0 or "No such option" not in result.output


def test_feat_def_export_accepts_json_option(tmp_path, monkeypatch):
    """feat-def-export should accept --json option."""
    class DummyCtx:
        def __enter__(self):
            return type("Conn", (), {"cursor": lambda self: DummyCursor()})()
        def __exit__(self, *args):
            return False

    class DummyCursor:
        def execute(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)
    monkeypatch.setattr(cli, "export_definitions_to_directory", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["feat-def-export", "--dir", str(tmp_path), "--json"])
    assert result.exit_code == 0 or "No such option" not in result.output


def test_feat_fx_import_accepts_json_option(tmp_path, monkeypatch):
    """feat-fx-import should accept --json option."""
    class DummyCtx:
        def __enter__(self):
            return type("Conn", (), {"cursor": lambda self: DummyCursor()})()
        def __exit__(self, *args):
            return False

    class DummyCursor:
        def execute(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)
    monkeypatch.setattr(cli, "import_functions_from_directory", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["feat-fx-import", "--dir", str(tmp_path), "--json"])
    assert result.exit_code == 0 or "No such option" not in result.output


def test_feat_fx_export_accepts_json_option(tmp_path, monkeypatch):
    """feat-fx-export should accept --json option."""
    class DummyCtx:
        def __enter__(self):
            return type("Conn", (), {"cursor": lambda self: DummyCursor()})()
        def __exit__(self, *args):
            return False

    class DummyCursor:
        def execute(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)
    monkeypatch.setattr(cli, "export_functions_to_directory", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["feat-fx-export", "--dir", str(tmp_path), "--json"])
    assert result.exit_code == 0 or "No such option" not in result.output
