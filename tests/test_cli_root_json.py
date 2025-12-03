import json
from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_root_json_shows_commands():
    res = runner.invoke(cli.app, ["--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "error"
    assert "commands" in payload
