"""
Tests for the query_database read-only guard (issue #161).

TDD tests - written before implementation.

The guard must reject real DDL/DML by SQL word, not by substring:
column names like created_at / updated_at must not trip the CREATE /
UPDATE keyword checks.
"""

import asyncio
import sys
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import server


def run_async(coro):
    """Run a coroutine without tearing down the current event loop.

    asyncio.run() closes the loop and unsets the current one, which
    breaks tests (test_rbac.py) that rely on asyncio.get_event_loop().
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _query(sql: str) -> Dict:
    """Run _query_database with psql stubbed out (guard behavior only)."""
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ''
        result.stderr = ''
        return result

    with patch.object(server.subprocess, 'run', side_effect=fake_run):
        return run_async(server._query_database({'sql': sql}))


class TestGuardAllowsColumnNamesContainingKeywords:
    """Legitimate SELECTs over created_at / updated_at must pass (issue #161)."""

    def test_select_created_at_passes(self):
        result = _query(
            "SELECT name, scope, status, created_at::date AS created "
            "FROM feature_functions ORDER BY created_at DESC"
        )
        assert result.get('success') is True, (
            f"created_at column reference must not trip CREATE guard: {result}"
        )

    def test_select_updated_at_passes(self):
        result = _query(
            "SELECT symbol, updated_at FROM stocks WHERE updated_at IS NOT NULL"
        )
        assert result.get('success') is True, (
            f"updated_at column reference must not trip UPDATE guard: {result}"
        )

    def test_cte_with_keyword_like_alias_passes(self):
        result = _query(
            "WITH recent AS (SELECT id, created_at FROM ml_models) "
            "SELECT COUNT(*) FROM recent"
        )
        assert result.get('success') is True, result


class TestGuardStillRejectsRealStatements:
    """Actual DDL/DML must remain rejected."""

    def _assert_rejected(self, sql: str) -> None:
        result = _query(sql)
        assert result.get('success') is False, (
            f"guard must reject: {sql!r} -> {result}"
        )
        assert 'Dangerous SQL keyword' in result.get('error', '') or \
            'Only SELECT' in result.get('error', ''), result

    def test_create_table_rejected(self):
        self._assert_rejected("CREATE TABLE evil (id int)")

    def test_update_rejected(self):
        self._assert_rejected("UPDATE stocks SET symbol = 'X'")

    def test_drop_rejected(self):
        self._assert_rejected("DROP TABLE stocks")

    def test_delete_rejected(self):
        self._assert_rejected("DELETE FROM stocks")

    def test_stacked_statement_rejected(self):
        self._assert_rejected("SELECT 1; DROP TABLE stocks")

    def test_stacked_ddl_after_select_rejected(self):
        self._assert_rejected(
            "SELECT * FROM stocks; CREATE TABLE evil (id int)"
        )

    def test_insert_rejected(self):
        self._assert_rejected("INSERT INTO stocks (symbol) VALUES ('X')")
