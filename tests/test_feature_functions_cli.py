import json

from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_functions_register_inserts(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            calls["query"] = query
            calls["params"] = params

        def fetchone(self):
            return (42,)

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_connect(url):
        calls["url"] = url
        return FakeConn()

    def fake_create(_conn):
        calls["created"] = True

    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)
    monkeypatch.setattr(cli.schema, "create_feature_functions_table", fake_create)

    payload = {
        "name": "obv",
        "version": "1.0.0",
        "language": "python_expr",
        "function_body": "def compute(df): return df['close']",
    }
    res = runner.invoke(cli.app, ["features-fx-register", "--definition", json.dumps(payload), "--json"])
    assert res.exit_code == 0, res.stdout
    assert "obv" in res.stdout
    assert calls["params"]["name"] == "obv"
    assert calls["params"]["version"] == "1.0.0"
    assert calls["params"]["function_body"].startswith("def compute")


def test_functions_register_sets_default_created_by(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            calls["params"] = params

        def fetchone(self):
            return (7,)

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli.psycopg, "connect", lambda *a, **kw: FakeConn())
    monkeypatch.setattr(cli.schema, "create_feature_functions_table", lambda _c: None)

    payload = {
        "name": "cmf",
        "version": "1.0.0",
        "language": "python_expr",
        "function_body": "def compute(df): return df['vol']",
    }
    res = runner.invoke(cli.app, ["features-fx-register", "--definition", json.dumps(payload), "--json"])
    assert res.exit_code == 0, res.stdout
    assert calls["params"]["created_by"] == "cli"


def test_functions_list(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            calls["query"] = query
            calls["params"] = params

        def fetchall(self):
            calls["fetched"] = True
            if "function_body" in calls["query"]:
                class _Row:
                    def isoformat(self):
                        return "2024-01-01T00:00:00"

                return [
                    ("obv", "1.0.0", "active", "python_expr", True, "desc", ["vol"], _Row(), "def compute(): return 1"),
                    ("obv", "0.9.0", "deprecated", "python_expr", False, None, None, None, "def compute(): return 0"),
                ]

            class _Row:
                def isoformat(self):
                    return "2024-01-01T00:00:00"

            return [
                ("obv", "1.0.0", "active", "python_expr", True, "desc", ["vol"], _Row()),
                ("obv", "0.9.0", "deprecated", "python_expr", False, None, None, None),
            ]

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_connect(url):
        calls["url"] = url
        return FakeConn()

    def fake_create(_conn):
        calls["created"] = True

    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)
    monkeypatch.setattr(cli.schema, "create_feature_functions_table", fake_create)

    res = runner.invoke(cli.app, ["features-fx-list", "--json", "--show-body", "--feature", "obv"])
    assert res.exit_code == 0, res.stdout
    assert "WHERE name =" in calls["query"]
    assert calls.get("created") is True
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    names = [f["name"] for f in payload["functions"]]
    assert "obv" in names
    assert any(f.get("function_body") for f in payload["functions"])
