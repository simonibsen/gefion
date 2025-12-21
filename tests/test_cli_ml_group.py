from typer.testing import CliRunner

from g2 import cli


def test_ml_group_help_exists():
    runner = CliRunner()
    res = runner.invoke(cli.app, ["ml", "--help"])
    assert res.exit_code == 0
    assert "init" in res.output


def test_ml_init_calls_schema_init(monkeypatch):
    called = {}

    class DummyConn:
        pass

    class DummyCtx:
        def __enter__(self):
            return DummyConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_db_connection(db_url, autocommit=True):
        called["db_url"] = db_url
        called["autocommit"] = autocommit
        return DummyCtx()

    def fake_init_schema_tables(conn, tables):
        called["tables"] = tables

    monkeypatch.setattr(cli, "db_connection", fake_db_connection)
    monkeypatch.setattr(cli, "init_schema_tables", fake_init_schema_tables)

    runner = CliRunner()
    res = runner.invoke(cli.app, ["ml", "init", "--db-url", "postgresql://example", "--json"])
    assert res.exit_code == 0, res.output
    assert called["db_url"] == "postgresql://example"
    assert called["autocommit"] is True
    assert "ml_models" in called["tables"]
    assert "quantile_predictions" in called["tables"]
    assert "trend_class_predictions" in called["tables"]


def test_ml_device_command_runs_without_torch_installed():
    runner = CliRunner()
    res = runner.invoke(cli.app, ["ml", "device", "--json"])
    assert res.exit_code == 0, res.output
    # Should always report a device even if torch isn't installed.
    assert "cpu" in res.output.lower()
