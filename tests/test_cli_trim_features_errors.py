import json
from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_trim_features_missing_dates_plaintext(monkeypatch):
    res = runner.invoke(cli.app, ["features-trim", "--feature", "indicator_rsi_14"])
    assert res.exit_code != 0
    assert "Missing option '--before' or '--after'" in res.stdout or "Missing option" in res.stdout


def test_trim_features_missing_dates_json(monkeypatch):
    res = runner.invoke(cli.app, ["features-trim", "--feature", "indicator_rsi_14", "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "error"
    assert "Specify --before and/or --after" in payload["message"]
