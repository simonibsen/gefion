"""
TDD tests for financials-backfill CLI command.

Fetches quarterly financial data (income statement, balance sheet,
cash flow, earnings) from AlphaVantage and stores in quarterly_financials.
"""
import json
import os
import pytest
from unittest.mock import Mock, patch, call
from typer.testing import CliRunner


runner = CliRunner()


def test_backfill_command_exists():
    """financials-backfill command exists and shows help."""
    from gefion.cli import app

    result = runner.invoke(app, ["financials-backfill", "--help"])
    assert result.exit_code == 0, f"Command should exist: {result.output}"
    assert "quarterly" in result.output.lower() or "financial" in result.output.lower()


def test_backfill_has_workers_option():
    """financials-backfill has --workers option."""
    from gefion.cli import app

    result = runner.invoke(app, ["financials-backfill", "--help"])
    assert "workers" in result.output.lower()


def test_backfill_has_limit_option():
    """financials-backfill has --limit option."""
    from gefion.cli import app

    result = runner.invoke(app, ["financials-backfill", "--help"])
    assert "limit" in result.output.lower()


def test_backfill_calls_four_endpoints_per_symbol():
    """Each symbol should trigger 4 API calls (income, balance, cash flow, earnings)."""
    from gefion.cli import _financials_backfill_impl
    from gefion.alphavantage.client import AlphaVantageClient

    stocks = [(1, "AAPL")]
    call_log = []

    def mock_fetch(endpoint_name):
        def fetch(symbol):
            call_log.append(endpoint_name)
            return {"quarterlyReports": []} if endpoint_name != "earnings" else {"quarterlyEarnings": []}
        return fetch

    mock_conn = Mock()
    mock_cur = Mock()
    mock_cur.fetchall.side_effect = [stocks, []]  # stocks query, then existing check
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)

    from contextlib import contextmanager

    @contextmanager
    def mock_connection(url):
        yield mock_conn

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key", "OTEL_ENABLED": "false"}):
        with patch("gefion.cli.db_connection", side_effect=mock_connection):
            with patch.object(AlphaVantageClient, "fetch_income_statement", side_effect=mock_fetch("income")):
                with patch.object(AlphaVantageClient, "fetch_balance_sheet", side_effect=mock_fetch("balance")):
                    with patch.object(AlphaVantageClient, "fetch_cash_flow", side_effect=mock_fetch("cashflow")):
                        with patch.object(AlphaVantageClient, "fetch_earnings", side_effect=mock_fetch("earnings")):
                            _financials_backfill_impl(
                                limit=None, force=True, workers=1,
                                calls_per_minute=9999, db_url="postgresql://test",
                                json_output=True,
                            )

    assert len(call_log) == 4, f"Expected 4 API calls, got {len(call_log)}: {call_log}"
    assert "income" in call_log
    assert "balance" in call_log
    assert "cashflow" in call_log
    assert "earnings" in call_log


def test_backfill_inserts_quarterly_rows():
    """Backfill should INSERT parsed quarterly data into the database."""
    from gefion.cli import _financials_backfill_impl
    from gefion.alphavantage.client import AlphaVantageClient

    income_response = {
        "quarterlyReports": [{
            "fiscalDateEnding": "2024-03-31",
            "totalRevenue": "94836000000",
            "netIncome": "23636000000",
            "grossProfit": "42819000000",
            "ebitda": "31897000000",
            "operatingIncome": "27900000000",
            "eps": "1.53",
        }]
    }
    empty_response = {"quarterlyReports": []}
    empty_earnings = {"quarterlyEarnings": []}

    stocks = [(1, "AAPL")]
    executed_sqls = []

    mock_conn = Mock()
    mock_cur = Mock()
    mock_cur.fetchall.side_effect = [stocks, []]

    def track_execute(sql, params=None):
        executed_sqls.append((sql.strip() if isinstance(sql, str) else str(sql).strip(), params))

    mock_cur.execute = track_execute
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)

    from contextlib import contextmanager

    @contextmanager
    def mock_connection(url):
        yield mock_conn

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key", "OTEL_ENABLED": "false"}):
        with patch("gefion.cli.db_connection", side_effect=mock_connection):
            with patch.object(AlphaVantageClient, "fetch_income_statement", return_value=income_response):
                with patch.object(AlphaVantageClient, "fetch_balance_sheet", return_value=empty_response):
                    with patch.object(AlphaVantageClient, "fetch_cash_flow", return_value=empty_response):
                        with patch.object(AlphaVantageClient, "fetch_earnings", return_value=empty_earnings):
                            _financials_backfill_impl(
                                limit=None, force=True, workers=1,
                                calls_per_minute=9999, db_url="postgresql://test",
                                json_output=True,
                            )

    # Should have INSERT into quarterly_financials
    insert_sqls = [
        (sql, params) for sql, params in executed_sqls
        if "quarterly_financials" in sql and "INSERT" in sql.upper()
    ]
    assert len(insert_sqls) >= 1, (
        f"Expected at least 1 INSERT into quarterly_financials, "
        f"SQLs: {[sql[:80] for sql, _ in executed_sqls]}"
    )


def test_backfill_uses_progress_reporter():
    """Backfill should use ProgressReporter for progress display."""
    from gefion.cli import _financials_backfill_impl
    from gefion.alphavantage.client import AlphaVantageClient

    stocks = [(1, "AAPL")]

    mock_conn = Mock()
    mock_cur = Mock()
    mock_cur.fetchall.side_effect = [stocks, []]
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)

    from contextlib import contextmanager

    @contextmanager
    def mock_connection(url):
        yield mock_conn

    empty = {"quarterlyReports": []}
    empty_e = {"quarterlyEarnings": []}

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key", "OTEL_ENABLED": "false"}):
        with patch("gefion.cli.db_connection", side_effect=mock_connection):
            with patch.object(AlphaVantageClient, "fetch_income_statement", return_value=empty):
                with patch.object(AlphaVantageClient, "fetch_balance_sheet", return_value=empty):
                    with patch.object(AlphaVantageClient, "fetch_cash_flow", return_value=empty):
                        with patch.object(AlphaVantageClient, "fetch_earnings", return_value=empty_e):
                            with patch("gefion.utils.progress.ProgressReporter") as MockReporter:
                                mock_reporter = MockReporter.return_value
                                mock_reporter.start_live.return_value = None
                                mock_reporter.live = None
                                mock_reporter.step_done = Mock()
                                mock_reporter.complete = Mock()
                                mock_reporter.json_output = True

                                _financials_backfill_impl(
                                    limit=None, force=True, workers=1,
                                    calls_per_minute=9999, db_url="postgresql://test",
                                    json_output=True,
                                )

                                MockReporter.assert_called_once()
                                mock_reporter.step_done.assert_called()
                                mock_reporter.complete.assert_called_once()
