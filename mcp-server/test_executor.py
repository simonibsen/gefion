"""
Tests for GefionExecutor command construction (issue #157).

TDD tests - written before implementation.

The MCP server is launched via the venv interpreter, which does NOT put
.venv/bin on PATH. The executor must therefore invoke the CLI through the
interpreter that is running the server (sys.executable -m gefion.cli),
never as a bare 'gefion' executable looked up on PATH.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import server


def run_async(coro):
    """Run a coroutine without tearing down the current event loop.

    asyncio.run() closes the loop it creates and unsets the current
    loop, which breaks later tests (test_rbac.py) that rely on
    asyncio.get_event_loop(). Reuse/set a persistent loop instead.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except (RuntimeError, DeprecationWarning):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _server_source() -> str:
    """Read server.py source directly (avoids import side effects)."""
    return (Path(__file__).parent / "server.py").read_text()


class TestExecutorCommandConstruction:
    """Executor must resolve the CLI via the running interpreter."""

    def test_executor_uses_sys_executable_module_invocation(self):
        """run() must build [sys.executable, -m, gefion.cli, ...args, --json]."""
        executor = server.GefionExecutor()
        captured: Dict[str, List[str]] = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stdout = '{"success": true}'
            result.stderr = ''
            return result

        with patch.object(server.subprocess, 'run', side_effect=fake_run):
            result = run_async(executor.run('health'))

        assert result == {'success': True}
        assert captured['cmd'][:3] == [sys.executable, '-m', 'gefion.cli'], (
            "Executor must invoke the CLI via the interpreter running the "
            "server, not rely on PATH lookup"
        )
        assert captured['cmd'][3:] == ['health', '--json']

    def test_executor_never_invokes_bare_gefion(self):
        """No element of the built command may be the bare 'gefion' name."""
        executor = server.GefionExecutor()
        captured: Dict[str, List[str]] = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stdout = '{}'
            result.stderr = ''
            return result

        with patch.object(server.subprocess, 'run', side_effect=fake_run):
            run_async(executor.run('data-update', '--limit', '10'))

        assert 'gefion' not in captured['cmd'], (
            "Bare 'gefion' requires .venv/bin on PATH, which the MCP server "
            "process does not have (issue #157)"
        )

    def test_executor_source_has_no_bare_gefion_literal(self):
        """Regression: GefionExecutor source must not build ['gefion'] + args."""
        src = _server_source()
        start = src.index("class GefionExecutor")
        end = src.index("\nclass ", start + 1)
        body = src[start:end]

        assert "['gefion']" not in body, (
            "GefionExecutor must not prepend the bare 'gefion' executable"
        )
        assert '["gefion"]' not in body


class TestNoDoublePrefixedExecutorCalls:
    """Callers of executor.run() must not pass a leading 'gefion' argument.

    executor.run() already prepends the CLI invocation, so a leading
    'gefion' argument would produce a doubled command.
    """

    def test_experiment_chain_does_not_pass_leading_gefion(self):
        src = _server_source()
        start = src.index("async def _experiment_chain(")
        end = src.index("\nasync def ", start + 1)
        body = src[start:end]

        assert '"gefion",' not in body and "'gefion'," not in body, (
            "_experiment_chain must not pass 'gefion' as the first argument "
            "to executor.run() — the executor already prepends the CLI"
        )
