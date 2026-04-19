"""Tests for AlphaVantage quarterly financial endpoint methods."""
import os
import pytest
from unittest.mock import Mock, patch


def test_client_has_fetch_income_statement():
    """AlphaVantageClient has fetch_income_statement method."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        assert hasattr(client, "fetch_income_statement")
        assert callable(client.fetch_income_statement)


def test_client_has_fetch_balance_sheet():
    """AlphaVantageClient has fetch_balance_sheet method."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        assert hasattr(client, "fetch_balance_sheet")
        assert callable(client.fetch_balance_sheet)


def test_client_has_fetch_cash_flow():
    """AlphaVantageClient has fetch_cash_flow method."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        assert hasattr(client, "fetch_cash_flow")
        assert callable(client.fetch_cash_flow)


def test_client_has_fetch_earnings():
    """AlphaVantageClient has fetch_earnings method."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        assert hasattr(client, "fetch_earnings")
        assert callable(client.fetch_earnings)


def test_fetch_income_statement_calls_correct_endpoint():
    """fetch_income_statement calls INCOME_STATEMENT endpoint."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        with patch.object(client, "get", return_value={"quarterlyReports": []}) as mock_get:
            client.fetch_income_statement("AAPL")
            mock_get.assert_called_once_with("INCOME_STATEMENT", symbol="AAPL")


def test_fetch_balance_sheet_calls_correct_endpoint():
    """fetch_balance_sheet calls BALANCE_SHEET endpoint."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        with patch.object(client, "get", return_value={"quarterlyReports": []}) as mock_get:
            client.fetch_balance_sheet("AAPL")
            mock_get.assert_called_once_with("BALANCE_SHEET", symbol="AAPL")


def test_fetch_cash_flow_calls_correct_endpoint():
    """fetch_cash_flow calls CASH_FLOW endpoint."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        with patch.object(client, "get", return_value={"quarterlyReports": []}) as mock_get:
            client.fetch_cash_flow("AAPL")
            mock_get.assert_called_once_with("CASH_FLOW", symbol="AAPL")


def test_fetch_earnings_calls_correct_endpoint():
    """fetch_earnings calls EARNINGS endpoint."""
    from gefion.alphavantage.client import AlphaVantageClient
    with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "test_key"}):
        client = AlphaVantageClient()
        with patch.object(client, "get", return_value={"quarterlyEarnings": []}) as mock_get:
            client.fetch_earnings("AAPL")
            mock_get.assert_called_once_with("EARNINGS", symbol="AAPL")
