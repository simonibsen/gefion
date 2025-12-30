import json
import os
from typer.testing import CliRunner

from g2 import cli

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
    # Parse the final JSON object (skip progress lines)
    # Find the last complete JSON object by looking for the final "}"
    output = res.stdout.strip()
    # The final output is pretty-printed JSON starting with "{"
    last_json_start = output.rfind('{\n  "_meta"')
    if last_json_start == -1:
        # Fallback: try to find any JSON starting with {
        last_json_start = output.rfind('{')
    payload = json.loads(output[last_json_start:])
    assert payload["status"] == "ok"
    assert called["symbols"] == ["IBM"]
