import json

from typer.testing import CliRunner

from gefion import cli

runner = CliRunner()


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
                    ("obv", "1.0.0", "active", "python_expr", True, "desc", ["vol"], _Row(), "stock", "def compute(): return 1"),
                    ("obv", "0.9.0", "deprecated", "python_expr", False, None, None, None, "stock", "def compute(): return 0"),
                ]

            class _Row:
                def isoformat(self):
                    return "2024-01-01T00:00:00"

            return [
                ("obv", "1.0.0", "active", "python_expr", True, "desc", ["vol"], _Row(), "stock"),
                ("obv", "0.9.0", "deprecated", "python_expr", False, None, None, None, "stock"),
            ]

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

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

    res = runner.invoke(cli.app, ["feat-fx-list", "--json", "--show-body", "--feature", "obv"])
    assert res.exit_code == 0, res.stdout
    assert "WHERE name =" in calls["query"]
    assert calls.get("created") is True
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    names = [f["name"] for f in payload["functions"]]
    assert "obv" in names
    assert any(f.get("function_body") for f in payload["functions"])
