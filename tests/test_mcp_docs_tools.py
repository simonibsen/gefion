"""Tests for MCP documentation tools.

Remote MCP clients (claude.ai, sandboxed chats) cannot read the repo, so
documentation must be reachable as tools: list, read, search.

TDD: Tests written first, before implementation.
"""
import pathlib

SERVER_PATH = pathlib.Path("mcp-server/server.py")


def _src() -> str:
    return SERVER_PATH.read_text()


class TestDocsListTool:
    def test_tool_definition_exists(self):
        assert 'name="docs_list"' in _src()

    def test_handler_dispatch(self):
        assert 'name == "docs_list"' in _src()

    def test_handler_function_exists(self):
        assert "async def _docs_list(" in _src()


class TestDocsReadTool:
    def test_tool_definition_exists(self):
        assert 'name="docs_read"' in _src()

    def test_handler_dispatch(self):
        assert 'name == "docs_read"' in _src()

    def test_requires_name(self):
        src = _src()
        idx = src.index('name="docs_read"')
        assert '"required": ["name"]' in src[idx:idx + 700]

    def test_guards_path_traversal(self):
        """docs_read must refuse names that escape the docs directory."""
        src = _src()
        idx = src.index("async def _docs_read(")
        block = src[idx:idx + 1500]
        assert "_resolve_doc" in block or "resolve()" in block


class TestDocsSearchTool:
    def test_tool_definition_exists(self):
        assert 'name="docs_search"' in _src()

    def test_handler_dispatch(self):
        assert 'name == "docs_search"' in _src()

    def test_requires_query(self):
        src = _src()
        idx = src.index('name="docs_search"')
        assert '"required": ["query"]' in src[idx:idx + 700]


class TestDocResolution:
    """The path guard itself, exercised directly.

    Loading the server module requires the `mcp` package, which CI does
    not install — these tests run locally and skip there. The
    source-inspection tests above still cover CI.
    """

    def _load_server_module(self):
        import importlib.util
        import pytest as _pytest
        _pytest.importorskip("mcp")
        spec = importlib.util.spec_from_file_location("gefion_mcp_server", SERVER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_resolves_known_doc(self):
        mod = self._load_server_module()
        path = mod._resolve_doc("USER_GUIDE.md")
        assert path is not None and path.name == "USER_GUIDE.md"

    def test_resolves_readme(self):
        mod = self._load_server_module()
        path = mod._resolve_doc("README.md")
        assert path is not None

    def test_rejects_traversal(self):
        mod = self._load_server_module()
        assert mod._resolve_doc("../src/gefion/cli.py") is None
        assert mod._resolve_doc("/etc/passwd") is None
        assert mod._resolve_doc("..%2F..%2Fsecrets") is None

    def test_rejects_unknown(self):
        mod = self._load_server_module()
        assert mod._resolve_doc("NOT_A_DOC.md") is None
