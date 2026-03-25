import json

from gefion import cli


def test_emit_json_outputs_payload(capsys):
    """Test that emit_json outputs the payload with metadata wrapper."""
    payload = {"success": True, "total_inserted": 1}
    cli.emit_json(payload)

    captured = capsys.readouterr()
    output = json.loads(captured.out)

    # Output should contain _meta and the original payload fields
    assert "_meta" in output
    assert "timestamp" in output["_meta"]
    assert output["success"] == payload["success"]
    assert output["total_inserted"] == payload["total_inserted"]
