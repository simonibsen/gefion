"""Regression tests for issue #214: `feat-compute --json` progress lines must
report the RESOLVED worker configuration (from --max-workers/--writer-workers
and the effective --max-parallel-functions), not stale/default values that
drift away from what the user actually configured.
"""
import json

from typer.testing import CliRunner

from gefion import cli
from gefion.utils.progress import ProgressReporter

runner = CliRunner()


def test_emit_json_includes_max_parallel_functions():
    """_emit_json should surface max_parallel_functions when set on the reporter."""
    reporter = ProgressReporter(total=10, json_output=True, enabled=True)
    reporter.max_workers = 6
    reporter.writer_workers = 3
    reporter.max_parallel_functions = 5

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    reporter.step_done("AAA", error=False, meta={"inserted": 1})
    sys.stdout = old_stdout

    data = json.loads(buffer.getvalue().strip())
    assert data["max_workers"] == 6
    assert data["writer_workers"] == 3
    assert data["max_parallel_functions"] == 5


def test_features_compute_json_reports_resolved_worker_counts(monkeypatch):
    """feat-compute --json --max-workers 6 --writer-workers 3 must report those
    resolved values on every progress line, not stale/auto-tuned defaults."""
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
            return (1,)

        def fetchall(self):
            return [("feat1",)]

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

    class DummyPool:
        def close(self):
            calls["closed"] = True

    def fake_init_pool(url, min_size=2, max_size=10, timeout=30.0, prepare_statements=True):
        dummy = DummyPool()
        dummy._g2_prepare_statements = True
        cli.db_pool._pool = dummy
        return dummy

    def fake_get_pool():
        return None

    monkeypatch.setattr(cli.db_pool, "get_pool", fake_get_pool)
    monkeypatch.setattr(cli.db_pool, "init_pool", fake_init_pool)
    monkeypatch.setattr(cli.db_pool, "get_connection", lambda: FakeConn())
    monkeypatch.setattr(cli.psycopg, "connect", lambda *a, **kw: FakeConn())
    monkeypatch.setattr(cli.schema, "create_feature_definitions_table", lambda conn: None)
    monkeypatch.setattr(cli.schema, "create_computed_features_table", lambda conn: None)
    monkeypatch.setattr(cli, "get_available_connections", lambda url: (10,))

    result = runner.invoke(
        cli.app,
        [
            "feat-compute",
            "--symbols", "AAA,BBB",
            "--features", "feat1",
            "--json",
            "--max-workers", "6",
            "--writer-workers", "3",
        ],
    )

    progress_lines = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if "max_workers" in payload:
            progress_lines.append(payload)

    assert progress_lines, f"expected JSON progress lines with max_workers, got stdout: {result.stdout!r}"
    for payload in progress_lines:
        assert payload["max_workers"] == 6, f"expected resolved max_workers=6, got {payload}"
        assert payload["writer_workers"] == 3, f"expected resolved writer_workers=3, got {payload}"
        assert "max_parallel_functions" in payload, f"expected max_parallel_functions in payload: {payload}"
