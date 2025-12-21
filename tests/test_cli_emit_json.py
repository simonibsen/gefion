import json

from g2 import cli


def test_emit_json_outputs_payload(monkeypatch):
    captured = {}

    def fake_echo(msg):
        captured["msg"] = msg

    monkeypatch.setattr(cli.typer, "echo", fake_echo)

    payload = {"success": True, "total_inserted": 1}
    cli.emit_json(payload)

    assert "msg" in captured
    assert json.loads(captured["msg"]) == payload
