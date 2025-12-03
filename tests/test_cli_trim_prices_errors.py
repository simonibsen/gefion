import json
from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_trim_prices_missing_dates_plaintext():
    res = runner.invoke(cli.app, ["prices-trim"])
    assert res.exit_code != 0
    assert "Missing option" in res.stdout and "--before" in res.stdout


def test_trim_prices_missing_dates_json():
    res = runner.invoke(cli.app, ["prices-trim", "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "error"
    assert "Specify --before and/or --after" in payload["message"]
