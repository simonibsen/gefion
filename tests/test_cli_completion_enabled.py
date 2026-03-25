from typer.testing import CliRunner

from gefion import cli

runner = CliRunner()


def test_completion_flags_present():
    res = runner.invoke(cli.app, ["--help"])
    assert res.exit_code == 0
    assert "--install-completion" in res.stdout
    assert "--show-completion" in res.stdout
