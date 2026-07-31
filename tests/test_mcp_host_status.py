"""#158: system_status is wired to the host-capability posture.

Source-inspection (matches tests/test_mcp_regime.py): the pure logic lives in
gefion.host / gefion.config and is tested there; here we assert server.py is a
thin caller — it loads .env as the SOT and folds the posture into system_status.
"""
import pathlib

SERVER = pathlib.Path("mcp-server/server.py")


def _src() -> str:
    return SERVER.read_text()


def _system_status_body() -> str:
    src = _src()
    start = src.index("async def _system_status(")
    end = src.index("\nasync def ", start + 1)
    return src[start:end]


def test_server_loads_dotenv_as_sot():
    src = _src()
    assert "apply_dotenv(" in src, "server must load .env as the config SOT (#158)"


def test_server_imports_host_capabilities():
    src = _src()
    assert "from gefion.host import" in src or "import gefion.host" in src


def test_system_status_folds_in_host_posture():
    body = _system_status_body()
    assert '"host"' in body or "'host'" in body, \
        "system_status must expose a host posture block (#158)"
    assert "inventory(" in body and "assess(" in body, \
        "system_status must measure + assess host capabilities (#158)"
