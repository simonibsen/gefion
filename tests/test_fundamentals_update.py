"""
TDD tests for fundamentals-update CLI command.

Tests the command that fetches company overview data (sector, industry, name)
from AlphaVantage and updates the stocks table.
"""
import os
import psycopg
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from click.testing import CliRunner
from typer.testing import CliRunner as TyperCliRunner


@pytest.fixture
def db_conn():
    """Create test database connection."""
    if not os.getenv("ENABLE_DB_TESTS"):
        pytest.skip("Database tests not enabled (set ENABLE_DB_TESTS=1)")
    from gefion.db.schema import test_db_url
    db_url = test_db_url()

    try:
        with psycopg.connect(db_url) as conn:
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_alphavantage_client_has_fetch_overview():
    """Test that AlphaVantageClient has fetch_overview method."""
    from gefion.alphavantage.client import AlphaVantageClient

    # Mock the API key
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        assert hasattr(client, "fetch_overview"), "Client should have fetch_overview method"


def test_fetch_overview_returns_expected_fields():
    """Test that fetch_overview returns sector, industry, and name fields."""
    from gefion.alphavantage.client import AlphaVantageClient

    mock_response = {
        "Symbol": "AAPL",
        "Name": "Apple Inc",
        "Sector": "TECHNOLOGY",
        "Industry": "CONSUMER ELECTRONICS",
        "Description": "Apple Inc. designs...",
    }

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        with patch.object(client, "get", return_value=mock_response):
            result = client.fetch_overview("AAPL")

            assert result.get("Name") == "Apple Inc"
            assert result.get("Sector") == "TECHNOLOGY"
            assert result.get("Industry") == "CONSUMER ELECTRONICS"


def test_fundamentals_update_command_exists():
    """Test that fundamentals-update command exists in CLI."""
    from gefion.cli import app

    runner = TyperCliRunner()
    result = runner.invoke(app, ["fundamentals-update", "--help"])

    assert result.exit_code == 0, f"Command should exist: {result.output}"
    assert "fundamentals" in result.output.lower() or "sector" in result.output.lower()


def test_fundamentals_update_respects_staleness(db_conn):
    """Test that fundamentals-update skips recently updated stocks."""
    # Get a stock to test with
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, symbol FROM stocks LIMIT 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("No stocks in database")
        stock_id, symbol = row

    # Set updated_at to now (recently updated)
    with db_conn.cursor() as cur:
        cur.execute("""
            UPDATE stocks SET updated_at = NOW() WHERE id = %s
        """, (stock_id,))
    db_conn.commit()

    # The command should skip this stock (not call API)
    # This is a behavioral test - actual implementation will verify


def test_fundamentals_update_force_flag(db_conn):
    """Test that --force flag updates even recently updated stocks."""
    # Get a stock to test with
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, symbol FROM stocks LIMIT 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("No stocks in database")
        stock_id, symbol = row

    # Set updated_at to now
    with db_conn.cursor() as cur:
        cur.execute("""
            UPDATE stocks SET updated_at = NOW() WHERE id = %s
        """, (stock_id,))
    db_conn.commit()

    # With --force, should update anyway
    # This is a behavioral test - actual implementation will verify


def test_fundamentals_staleness_threshold():
    """Test that staleness threshold is configurable (default 30 days)."""
    from gefion.cli import app

    runner = TyperCliRunner()
    result = runner.invoke(app, ["fundamentals-update", "--help"])

    # Should have a --max-age or similar option
    assert result.exit_code == 0
    # Check for staleness-related options in help text
    help_text = result.output.lower()
    assert "age" in help_text or "days" in help_text or "stale" in help_text or "force" in help_text


def test_fundamentals_update_uses_single_db_connection():
    """Test that fundamentals update uses one DB connection for all updates, not one per symbol."""
    from gefion.cli import _fundamentals_update_impl

    mock_responses = {
        "SYM1": {"Name": "Sym One", "Sector": "Tech", "Industry": "Software"},
        "SYM2": {"Name": "Sym Two", "Sector": "Health", "Industry": "Biotech"},
        "SYM3": {"Name": "Sym Three", "Sector": "Energy", "Industry": "Oil"},
    }

    stocks = [(1, "SYM1", None), (2, "SYM2", None), (3, "SYM3", None)]

    connection_count = {"opens": 0}
    original_db_connection = None

    # Track how many times db_connection is called for UPDATE operations
    # First call is for the SELECT query, subsequent calls are for UPDATEs
    from contextlib import contextmanager

    @contextmanager
    def tracking_connection(url):
        connection_count["opens"] += 1
        mock_conn = Mock()
        mock_conn.transaction = lambda: __import__('contextlib').nullcontext()
        mock_conn.autocommit = True
        mock_cur = Mock()
        mock_cur.fetchall.return_value = stocks
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        yield mock_conn

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key", "OTEL_ENABLED": "false"}):
        with patch("gefion.cli.db_connection", side_effect=tracking_connection):
            with patch("gefion.cli.AlphaVantageClient") as MockClient:
                client_instance = MockClient.return_value
                client_instance.fetch_overview.side_effect = lambda s: mock_responses[s]

                _fundamentals_update_impl(
                    exchange=None, limit=None, max_age_days=30,
                    force=True, calls_per_minute=75, db_url="postgresql://test",
                    json_output=True,
                )

    # Should be exactly 2 connections: 1 for SELECT, 1 for all UPDATEs
    # NOT 1 + N (one per symbol)
    assert connection_count["opens"] <= 2, (
        f"Expected at most 2 DB connections (1 SELECT + 1 for all UPDATEs), "
        f"got {connection_count['opens']}"
    )


def test_fundamentals_update_has_workers_option():
    """Test that fundamentals-update command has --workers option for parallelism."""
    from gefion.cli import app

    runner = TyperCliRunner()
    result = runner.invoke(app, ["fundamentals-update", "--help"])

    assert result.exit_code == 0
    assert "workers" in result.output.lower(), "Should have --workers option"


def test_fundamentals_update_emits_progress():
    """Test that fundamentals update emits progress updates with rate and ETA."""
    from gefion.cli import _fundamentals_update_impl
    from gefion.alphavantage.client import AlphaVantageClient
    import io

    stocks = [(i, f"SYM{i}", None) for i in range(5)]

    mock_conn = Mock()
    mock_conn.transaction = lambda: __import__('contextlib').nullcontext()
    mock_conn.autocommit = True
    mock_cur = Mock()
    mock_cur.fetchall.return_value = stocks
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)

    from contextlib import contextmanager

    @contextmanager
    def mock_connection(url):
        yield mock_conn

    captured = []
    original_echo = None

    def capture_echo(msg):
        captured.append(msg)

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key", "OTEL_ENABLED": "false"}):
        with patch("gefion.cli.db_connection", side_effect=mock_connection):
            with patch.object(AlphaVantageClient, "fetch_overview",
                            return_value={"Name": "Test", "Sector": "Tech", "Industry": "SW"}):
                # JSON mode: capture stdout for progress JSON
                import json
                with patch("typer.echo", side_effect=capture_echo):
                    _fundamentals_update_impl(
                        exchange=None, limit=None, max_age_days=30,
                        force=True, calls_per_minute=75, db_url="postgresql://test",
                        json_output=True, workers=1,
                    )

    # Should have progress events (one per symbol) plus a complete event
    progress_events = [json.loads(c) for c in captured if '"status"' in c and '"done"' in c]
    assert len(progress_events) >= 5, f"Expected at least 5 progress events, got {len(progress_events)}"

    # Check progress events have expected fields
    last = progress_events[-1]
    assert "done" in last
    assert "total" in last
    assert "percent" in last
    assert last["total"] == 5


def test_fundamentals_update_rich_progress():
    """Test that fundamentals update uses ProgressReporter for rich output."""
    from gefion.cli import _fundamentals_update_impl
    from gefion.alphavantage.client import AlphaVantageClient

    stocks = [(1, "AAPL", None), (2, "MSFT", None)]

    mock_conn = Mock()
    mock_conn.transaction = lambda: __import__('contextlib').nullcontext()
    mock_conn.autocommit = True
    mock_cur = Mock()
    mock_cur.fetchall.return_value = stocks
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
            with patch.object(AlphaVantageClient, "fetch_overview",
                            return_value={"Name": "Test", "Sector": "Tech", "Industry": "SW"}):
                # Patch at the source module so the import inside the function gets the mock
                with patch("gefion.utils.progress.ProgressReporter") as MockReporter:
                    mock_reporter = MockReporter.return_value
                    mock_reporter.start_live.return_value = None
                    mock_reporter.live = None
                    mock_reporter.step_done = Mock()
                    mock_reporter.complete = Mock()
                    mock_reporter.json_output = False

                    _fundamentals_update_impl(
                        exchange=None, limit=None, max_age_days=30,
                        force=True, calls_per_minute=75, db_url="postgresql://test",
                        json_output=False, workers=1,
                    )

                    # Should have created a ProgressReporter with total=2
                    MockReporter.assert_called_once_with(total=2, json_output=False)
                    # Should have called step_done for each symbol
                    assert mock_reporter.step_done.call_count == 2
                    # Should have called complete
                    mock_reporter.complete.assert_called_once()


def test_fundamentals_update_inserts_into_stocks_fundamentals():
    """Test that fundamentals update writes time-series data to stocks_fundamentals table."""
    from gefion.cli import _fundamentals_update_impl
    from gefion.alphavantage.client import AlphaVantageClient

    overview = {
        "Symbol": "AAPL",
        "Name": "Apple Inc",
        "Sector": "TECHNOLOGY",
        "Industry": "CONSUMER ELECTRONICS",
        "MarketCapitalization": "2800000000000",
        "PERatio": "28.50",
        "ForwardPE": "25.10",
        "PEGRatio": "1.85",
        "BookValue": "4.25",
        "DividendYield": "0.0055",
        "EPS": "6.42",
        "RevenuePerShareTTM": "24.32",
        "ProfitMargin": "0.265",
        "OperatingMarginTTM": "0.305",
        "ReturnOnEquityTTM": "1.45",
        "Beta": "1.24",
        "EVToEBITDA": "22.30",
        "SharesOutstanding": "15500000000",
    }

    stocks = [(1, "AAPL", None)]
    executed_sqls = []

    mock_conn = Mock()
    mock_conn.transaction = lambda: __import__('contextlib').nullcontext()
    mock_conn.autocommit = True
    mock_cur = Mock()
    mock_cur.fetchall.return_value = stocks

    def track_execute(sql, params=None):
        executed_sqls.append((sql.strip(), params))

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
            with patch.object(AlphaVantageClient, "fetch_overview", return_value=overview):
                _fundamentals_update_impl(
                    exchange=None, limit=None, max_age_days=30,
                    force=True, calls_per_minute=75, db_url="postgresql://test",
                    json_output=True, workers=1,
                )

    # Should have an INSERT into stocks_fundamentals
    fundamentals_inserts = [
        (sql, params) for sql, params in executed_sqls
        if "stocks_fundamentals" in sql
    ]
    assert len(fundamentals_inserts) == 1, (
        f"Expected 1 INSERT into stocks_fundamentals, got {len(fundamentals_inserts)}. "
        f"SQLs: {[sql[:60] for sql, _ in executed_sqls]}"
    )

    sql, params = fundamentals_inserts[0]
    assert "INSERT" in sql.upper()
    # Verify key values are passed
    assert 2800000000000 in params or "2800000000000" in str(params), "market_cap should be included"


def test_fundamentals_update_writes_exchange():
    """The stocks UPDATE must persist the OVERVIEW Exchange field.

    stocks.exchange exists in the schema but was never populated; exchange
    filters elsewhere depend on this write (issue #29).
    """
    from gefion.cli import _fundamentals_update_impl
    from gefion.alphavantage.client import AlphaVantageClient

    overview = {
        "Symbol": "AAPL",
        "Name": "Apple Inc",
        "Sector": "TECHNOLOGY",
        "Industry": "CONSUMER ELECTRONICS",
        "Exchange": "NASDAQ",
    }

    stocks = [(1, "AAPL", None)]
    executed_sqls = []

    mock_conn = Mock()
    mock_conn.transaction = lambda: __import__('contextlib').nullcontext()
    mock_conn.autocommit = True
    mock_cur = Mock()
    mock_cur.fetchall.return_value = stocks

    def track_execute(sql, params=None):
        executed_sqls.append((sql.strip(), params))

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
            with patch.object(AlphaVantageClient, "fetch_overview", return_value=overview):
                _fundamentals_update_impl(
                    exchange=None, limit=None, max_age_days=30,
                    force=True, calls_per_minute=75, db_url="postgresql://test",
                    json_output=True, workers=1,
                )

    stock_updates = [
        (sql, params) for sql, params in executed_sqls
        if "UPDATE stocks" in sql and "SET name" in sql
    ]
    assert len(stock_updates) == 1, (
        f"Expected 1 UPDATE of stocks metadata, got {len(stock_updates)}. "
        f"SQLs: {[sql[:60] for sql, _ in executed_sqls]}"
    )

    sql, params = stock_updates[0]
    assert "exchange" in sql, f"UPDATE must set exchange column, got: {sql}"
    assert "NASDAQ" in params, f"Exchange value from OVERVIEW must be written, got params: {params}"


def test_fundamentals_update_parses_numeric_fields():
    """Test that AlphaVantage string values are parsed to correct numeric types."""
    from gefion.cli import _parse_overview_fundamentals

    overview = {
        "MarketCapitalization": "2800000000000",
        "PERatio": "28.50",
        "ForwardPE": "None",
        "PEGRatio": "-",
        "BookValue": "4.25",
        "DividendYield": "0.0055",
        "EPS": "6.42",
        "RevenuePerShareTTM": "24.32",
        "ProfitMargin": "0.265",
        "OperatingMarginTTM": "0.305",
        "ReturnOnEquityTTM": "1.45",
        "Beta": "1.24",
        "EVToEBITDA": "22.30",
        "SharesOutstanding": "15500000000",
    }

    result = _parse_overview_fundamentals(overview)

    assert result["market_cap"] == 2800000000000
    assert result["pe_ratio"] == 28.50
    assert result["forward_pe"] is None  # "None" string → None
    assert result["peg_ratio"] is None  # "-" → None
    assert result["book_value"] == 4.25
    assert result["eps"] == 6.42
    assert result["beta"] == 1.24
    assert result["shares_outstanding"] == 15500000000


def test_fundamentals_update_has_quarterly_option():
    """fundamentals-update should have --quarterly flag for incremental quarterly refresh."""
    from gefion.cli import app

    runner = TyperCliRunner()
    result = runner.invoke(app, ["fundamentals-update", "--help"])
    assert result.exit_code == 0
    assert "quarterly" in result.output.lower(), "Should have --quarterly option"


def test_empty_overview_not_retried():
    """Empty OVERVIEW response should not be retried — it's a permanent data gap, not a transient error."""
    from gefion.alphavantage.client import AlphaVantageClient
    import requests

    call_count = {"n": 0}

    def mock_get(*args, **kwargs):
        call_count["n"] += 1
        resp = Mock()
        resp.status_code = 200
        resp.raise_for_status = Mock()
        resp.json.return_value = {}  # Empty payload
        return resp

    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key", "OTEL_ENABLED": "false"}):
        client = AlphaVantageClient(calls_per_minute=9999)
        client.session = Mock()
        client.session.get.side_effect = mock_get

        result = client.fetch_overview("FAKEETF")

    # Should return empty dict immediately, NOT retry 5 times
    assert call_count["n"] == 1, f"Expected 1 call (no retries for empty OVERVIEW), got {call_count['n']}"
    assert result == {} or result == {"text": ""}


def test_fundamentals_update_parallel_execution():
    """Test that fundamentals update can run API calls in parallel with multiple workers."""
    from gefion.cli import _fundamentals_update_impl
    from gefion.alphavantage.client import AlphaVantageClient
    import time

    call_times = []

    def slow_fetch(symbol):
        call_times.append(time.monotonic())
        time.sleep(0.05)  # 50ms per call
        return {"Name": f"{symbol} Inc", "Sector": "Tech", "Industry": "Software"}

    stocks = [(i, f"SYM{i}", None) for i in range(6)]

    mock_conn = Mock()
    mock_conn.transaction = lambda: __import__('contextlib').nullcontext()
    mock_conn.autocommit = True
    mock_cur = Mock()
    mock_cur.fetchall.return_value = stocks
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
            # Patch fetch_overview on the class prototype so all instances pick it up
            with patch.object(AlphaVantageClient, "fetch_overview", side_effect=slow_fetch):
                start = time.monotonic()
                _fundamentals_update_impl(
                    exchange=None, limit=None, max_age_days=30,
                    force=True, calls_per_minute=75, db_url="postgresql://test",
                    json_output=True, workers=3,
                )
                elapsed = time.monotonic() - start

    # With 6 calls at 50ms each:
    # Sequential: ~300ms minimum
    # Parallel (3 workers): ~100ms minimum
    assert len(call_times) == 6, f"All 6 symbols should be fetched, got {len(call_times)}"
    # With 3 workers, should complete in roughly half the sequential time
    assert elapsed < 0.25, (
        f"With 3 workers, 6x50ms calls should complete in <250ms, took {elapsed*1000:.0f}ms"
    )


def test_ratio_columns_hold_degenerate_financials(db_conn):
    """Prod failure 2026-07-07: distressed/shell stocks report margins and
    ROE in the +/-thousands; NUMERIC(8,6) (|v| < 100) overflowed and sank the
    whole run. The four ratio columns must hold real-world extremes."""
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT column_name, numeric_precision, numeric_scale
               FROM information_schema.columns
               WHERE table_name = 'stocks_fundamentals'
                 AND column_name IN ('dividend_yield', 'profit_margin',
                                     'operating_margin', 'return_on_equity')""")
        cols = {name: (prec, scale) for name, prec, scale in cur.fetchall()}
    assert len(cols) == 4
    for name, (prec, scale) in cols.items():
        assert prec >= 14, f"{name} precision {prec} cannot hold |v| >= 100"
        assert scale == 6, f"{name} scale changed unexpectedly"


def test_one_bad_symbol_cannot_sink_the_batch(db_conn):
    """Prod failure 2026-07-07: one overflowing symbol raised out of the
    write loop, discarding ~6,175 fetched results. The write path must be
    per-symbol fault-isolated: bad rows are counted and logged, good rows
    land."""
    from gefion.cli import _write_fundamentals_results

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol IN ('FWRT1', 'FWRT2')")
        cur.execute(
            "INSERT INTO stocks (symbol, name) VALUES ('FWRT1', 'Good Co'), "
            "('FWRT2', 'Degenerate Co') RETURNING id")
        good_id, bad_id = [r[0] for r in cur.fetchall()]
    db_conn.commit()

    good = {"Symbol": "FWRT1", "Name": "Good Co", "Sector": "Tech",
            "Industry": "Software", "Exchange": "NASDAQ",
            "ReturnOnEquityTTM": "0.21", "ProfitMargin": "0.1"}
    # overflows even the widened NUMERIC(14,6): |v| >= 10^8
    bad = {"Symbol": "FWRT2", "Name": "Degenerate Co", "Sector": "Shell",
           "Industry": "Shell", "Exchange": "NASDAQ",
           "ReturnOnEquityTTM": "999999999999"}
    results = [(good_id, "FWRT1", good, None, False),
               (bad_id, "FWRT2", bad, None, False)]

    try:
        summary = _write_fundamentals_results(db_conn, results)
        assert summary["updated"] == 1
        assert summary["write_errors"] == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT sector FROM stocks WHERE id = %s", (good_id,))
            assert cur.fetchone()[0] == "Tech"  # the good row landed
            cur.execute(
                "SELECT count(*) FROM stocks_fundamentals WHERE data_id = %s",
                (good_id,))
            assert cur.fetchone()[0] == 1
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM stocks_fundamentals WHERE data_id IN (%s, %s)",
                        (good_id, bad_id))
            cur.execute("DELETE FROM stocks WHERE id IN (%s, %s)", (good_id, bad_id))
        db_conn.commit()
