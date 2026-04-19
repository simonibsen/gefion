import json
import os
from typer.testing import CliRunner

from gefion import cli

runner = CliRunner()


def test_prices_ingest_api_fetch(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo")

    called = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            called["client"] = True

    def dummy_ingest(**kwargs):
        called["symbols"] = kwargs["symbols"]
        return 5

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", dummy_ingest)

    res = runner.invoke(cli.app, ["prices-ingest", "--symbol", "IBM", "--json"])
    assert res.exit_code == 0
    # Parse the last JSON line from output
    output = res.stdout.strip()
    lines = [l for l in output.splitlines() if l.strip().startswith("{")]
    payload = json.loads(lines[-1])
    assert payload["status"] == "ok"
    assert called["symbols"] == ["IBM"]
