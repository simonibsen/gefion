import json
import os
from pathlib import Path

from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


class DummyCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows


class DummyConn:
    def __init__(self, rows):
        self.rows = rows
        self.autocommit = False
        self.cursor_obj = DummyCursor(rows)

    def cursor(self):
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_features_export_writes_files(tmp_path, monkeypatch):
    funcs = [
        (
            "fx_a",
            "1.0.0",
            "active",
            "desc",
            "python_expr",
            "body",
            {"i": 1},
            "value",
            "double precision",
            {"p": 1},
            {"d": 1},
            {"dep": 1},
            "chk",
            ["tag"],
            "0.1.0",
            True,
            "cli",
        )
    ]
    defs = [
        (
            "feat_a",
            "fx_a",
            {"window": 5},
            "stock_ohlcv",
            "close",
            "computed_features",
            "value",
            "double precision",
            True,
            "1.0.0",
        )
    ]

    def fake_connect(url):
        return DummyConn(funcs if "feature_functions" in cli.dict_get else defs)  # type: ignore[arg-type]

    def fake_connect_funcs(url):
        return DummyConn(funcs)

    def fake_connect_defs(url):
        return DummyConn(defs)

    # Monkeypatch sequential connections: first call for functions, second for definitions
    monkeypatch.setattr(cli.psycopg, "connect", lambda url: fake_connect_funcs(url))
    first_call = True

    def connect_switch(url):
        nonlocal first_call
        if first_call:
            first_call = False
            return fake_connect_funcs(url)
        return fake_connect_defs(url)

    monkeypatch.setattr(cli.psycopg, "connect", connect_switch)
    monkeypatch.setattr(cli.schema, "create_feature_functions_table", lambda conn: None)
    monkeypatch.setattr(cli.schema, "create_feature_definitions_table", lambda conn: None)

    res = runner.invoke(cli.app, ["features-export", "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.stdout

    funcs_path = tmp_path / "feature_functions.json"
    defs_path = tmp_path / "feature_definitions.json"

    assert funcs_path.exists()
    assert defs_path.exists()

    funcs_data = json.loads(funcs_path.read_text())
    defs_data = json.loads(defs_path.read_text())

    assert funcs_data[0]["name"] == "fx_a"
    assert defs_data[0]["name"] == "feat_a"
    assert defs_data[0]["function_name"] == "fx_a"


def test_features_import_reads_files_and_upserts(tmp_path, monkeypatch):
    funcs_path = tmp_path / "feature_functions.json"
    defs_path = tmp_path / "feature_definitions.json"

    funcs_payload = [
        {
            "name": "fx_a",
            "version": "1.0.0",
            "language": "python_expr",
            "function_body": "def compute():\\n  return 1",
        }
    ]
    defs_payload = [
        {
            "name": "feat_a",
            "function_name": "fx_a",
            "params": {"window": 5},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True,
        }
    ]
    funcs_path.write_text(json.dumps(funcs_payload))
    defs_path.write_text(json.dumps(defs_payload))

    captured = {"functions": [], "defs": None, "stores": None}

    def fake_connect(url):
        class _Conn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            def cursor(self_inner):
                class _Cur:
                    def __enter__(self_cur):
                        return self_cur

                    def __exit__(self_cur, exc_type, exc, tb):
                        return False

                    def execute(self_cur, q, params=None):
                        captured["functions"].append(params)

                return _Cur()

        return _Conn()

    monkeypatch.setenv("DATABASE_URL", "postgresql://example.com/db")
    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)
    monkeypatch.setattr(cli.schema, "create_feature_functions_table", lambda conn: None)
    monkeypatch.setattr(cli.schema, "create_feature_definitions_table", lambda conn: None)
    monkeypatch.setattr(cli.schema, "create_computed_features_table", lambda conn: None)
    monkeypatch.setattr(cli, "ensure_feature_definitions", lambda conn, defs: captured.update({"defs": defs}) or [1])
    monkeypatch.setattr(cli, "ensure_store_targets", lambda conn, defs: captured.update({"stores": defs}))

    res = runner.invoke(cli.app, ["features-import", "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.stdout

    assert captured["functions"], "Expected functions to be upserted"
    assert captured["defs"] is not None
    assert captured["stores"] is not None
    assert captured["defs"][0]["name"] == "feat_a"
