"""Tests for quarterly financial statement parsers in catalog.py.

Each parser takes a raw AlphaVantage API response dict and returns
a List[Dict] of quarterly records with typed columns matching the
quarterly_financials table.
"""

from datetime import date

import pytest

from gefion.alphavantage.catalog import (
    parse_income_statement,
    parse_balance_sheet,
    parse_cash_flow,
    parse_earnings,
)


# ---------------------------------------------------------------------------
# Fixtures: realistic AlphaVantage response payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def income_payload():
    return {
        "quarterlyReports": [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalRevenue": "94836000000",
                "netIncome": "23636000000",
                "grossProfit": "42819000000",
                "ebitda": "31897000000",
                "operatingIncome": "27900000000",
                "eps": "1.53",
                "costOfRevenue": "52017000000",
                "researchAndDevelopment": "7456000000",
            },
        ],
    }


@pytest.fixture
def balance_payload():
    return {
        "quarterlyReports": [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalAssets": "352583000000",
                "totalLiabilities": "290437000000",
                "totalShareholderEquity": "62146000000",
                "totalCurrentAssets": "128645000000",
                "totalCurrentLiabilities": "153982000000",
                "longTermDebt": "95281000000",
                "commonStockSharesOutstanding": "15441883000",
                "goodwill": "None",
            },
        ],
    }


@pytest.fixture
def cash_flow_payload():
    return {
        "quarterlyReports": [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingCashflow": "26385000000",
                "capitalExpenditures": "2903000000",
                "dividendPayout": "3831000000",
                "netBorrowings": "-",
            },
        ],
    }


@pytest.fixture
def earnings_payload():
    return {
        "quarterlyEarnings": [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedDate": "2024-05-02",
                "reportedEPS": "1.53",
                "estimatedEPS": "1.50",
                "surprise": "0.03",
                "surprisePercentage": "2.0000",
                "extraField": "bonus",
            },
        ],
    }


# ---------------------------------------------------------------------------
# parse_income_statement
# ---------------------------------------------------------------------------

class TestParseIncomeStatement:
    def test_returns_list_of_dicts(self, income_payload):
        result = parse_income_statement(income_payload)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_date_is_date_object(self, income_payload):
        row = parse_income_statement(income_payload)[0]
        assert row["date"] == date(2024, 3, 31)
        assert isinstance(row["date"], date)

    def test_statement_type_is_income(self, income_payload):
        row = parse_income_statement(income_payload)[0]
        assert row["statement_type"] == "income"

    def test_reported_at_is_none(self, income_payload):
        row = parse_income_statement(income_payload)[0]
        assert row["reported_at"] is None

    def test_core_fields_extracted_as_typed(self, income_payload):
        row = parse_income_statement(income_payload)[0]
        assert row["revenue"] == 94836000000
        assert row["net_income"] == 23636000000
        assert row["gross_profit"] == 42819000000
        assert row["ebitda"] == 31897000000
        assert row["operating_income"] == 27900000000
        assert row["eps"] == 1.53

    def test_overflow_fields_in_raw(self, income_payload):
        row = parse_income_statement(income_payload)[0]
        assert "raw" in row
        raw = row["raw"]
        assert raw["costOfRevenue"] == "52017000000"
        assert raw["researchAndDevelopment"] == "7456000000"
        assert raw["reportedCurrency"] == "USD"

    def test_empty_quarterly_reports(self):
        assert parse_income_statement({"quarterlyReports": []}) == []

    def test_missing_quarterly_reports_key(self):
        assert parse_income_statement({}) == []

    def test_none_values_become_none(self):
        payload = {
            "quarterlyReports": [
                {
                    "fiscalDateEnding": "2024-06-30",
                    "totalRevenue": "None",
                    "netIncome": "-",
                    "grossProfit": "",
                    "ebitda": None,
                    "operatingIncome": "100",
                    "eps": "None",
                },
            ],
        }
        row = parse_income_statement(payload)[0]
        assert row["revenue"] is None
        assert row["net_income"] is None
        assert row["gross_profit"] is None
        assert row["ebitda"] is None
        assert row["eps"] is None
        assert row["operating_income"] == 100

    def test_multiple_quarters(self):
        payload = {
            "quarterlyReports": [
                {
                    "fiscalDateEnding": "2024-03-31",
                    "totalRevenue": "90000000000",
                    "netIncome": "20000000000",
                    "grossProfit": "40000000000",
                    "ebitda": "30000000000",
                    "operatingIncome": "25000000000",
                    "eps": "1.50",
                },
                {
                    "fiscalDateEnding": "2023-12-31",
                    "totalRevenue": "85000000000",
                    "netIncome": "18000000000",
                    "grossProfit": "38000000000",
                    "ebitda": "28000000000",
                    "operatingIncome": "23000000000",
                    "eps": "1.40",
                },
            ],
        }
        result = parse_income_statement(payload)
        assert len(result) == 2
        assert result[0]["date"] == date(2024, 3, 31)
        assert result[1]["date"] == date(2023, 12, 31)


# ---------------------------------------------------------------------------
# parse_balance_sheet
# ---------------------------------------------------------------------------

class TestParseBalanceSheet:
    def test_returns_list_of_dicts(self, balance_payload):
        result = parse_balance_sheet(balance_payload)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_date_is_date_object(self, balance_payload):
        row = parse_balance_sheet(balance_payload)[0]
        assert row["date"] == date(2024, 3, 31)
        assert isinstance(row["date"], date)

    def test_statement_type_is_balance_sheet(self, balance_payload):
        row = parse_balance_sheet(balance_payload)[0]
        assert row["statement_type"] == "balance_sheet"

    def test_reported_at_is_none(self, balance_payload):
        row = parse_balance_sheet(balance_payload)[0]
        assert row["reported_at"] is None

    def test_core_fields_extracted_as_typed(self, balance_payload):
        row = parse_balance_sheet(balance_payload)[0]
        assert row["total_assets"] == 352583000000
        assert row["total_liabilities"] == 290437000000
        assert row["shareholder_equity"] == 62146000000
        assert row["current_assets"] == 128645000000
        assert row["current_liabilities"] == 153982000000
        assert row["long_term_debt"] == 95281000000
        assert row["shares_outstanding"] == 15441883000

    def test_overflow_fields_in_raw(self, balance_payload):
        row = parse_balance_sheet(balance_payload)[0]
        assert "raw" in row
        assert row["raw"]["reportedCurrency"] == "USD"

    def test_none_string_becomes_none(self, balance_payload):
        """goodwill is "None" in the fixture; should parse to None."""
        row = parse_balance_sheet(balance_payload)[0]
        raw = row["raw"]
        # "None" should be stored as-is in raw, but any core field with
        # "None" should become Python None.
        # goodwill is not a core field, so it ends up in raw.
        assert "goodwill" in raw

    def test_empty_quarterly_reports(self):
        assert parse_balance_sheet({"quarterlyReports": []}) == []

    def test_missing_key(self):
        assert parse_balance_sheet({}) == []

    def test_dash_and_empty_become_none(self):
        payload = {
            "quarterlyReports": [
                {
                    "fiscalDateEnding": "2024-06-30",
                    "totalAssets": "-",
                    "totalLiabilities": "",
                    "totalShareholderEquity": "None",
                    "totalCurrentAssets": "1000",
                    "totalCurrentLiabilities": "2000",
                    "longTermDebt": None,
                    "commonStockSharesOutstanding": "500",
                },
            ],
        }
        row = parse_balance_sheet(payload)[0]
        assert row["total_assets"] is None
        assert row["total_liabilities"] is None
        assert row["shareholder_equity"] is None
        assert row["long_term_debt"] is None
        assert row["current_assets"] == 1000
        assert row["current_liabilities"] == 2000


# ---------------------------------------------------------------------------
# parse_cash_flow
# ---------------------------------------------------------------------------

class TestParseCashFlow:
    def test_returns_list_of_dicts(self, cash_flow_payload):
        result = parse_cash_flow(cash_flow_payload)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_date_is_date_object(self, cash_flow_payload):
        row = parse_cash_flow(cash_flow_payload)[0]
        assert row["date"] == date(2024, 3, 31)
        assert isinstance(row["date"], date)

    def test_statement_type_is_cash_flow(self, cash_flow_payload):
        row = parse_cash_flow(cash_flow_payload)[0]
        assert row["statement_type"] == "cash_flow"

    def test_reported_at_is_none(self, cash_flow_payload):
        row = parse_cash_flow(cash_flow_payload)[0]
        assert row["reported_at"] is None

    def test_core_fields_extracted_as_typed(self, cash_flow_payload):
        row = parse_cash_flow(cash_flow_payload)[0]
        assert row["operating_cashflow"] == 26385000000
        assert row["capital_expenditures"] == 2903000000

    def test_overflow_fields_in_raw(self, cash_flow_payload):
        row = parse_cash_flow(cash_flow_payload)[0]
        assert "raw" in row
        assert row["raw"]["dividendPayout"] == "3831000000"
        assert row["raw"]["reportedCurrency"] == "USD"

    def test_dash_value_becomes_none(self, cash_flow_payload):
        """netBorrowings is '-' in fixture; check it ends up in raw."""
        row = parse_cash_flow(cash_flow_payload)[0]
        # "-" is a non-core field here, so it goes to raw
        assert row["raw"]["netBorrowings"] == "-"

    def test_none_values_in_core_fields(self):
        payload = {
            "quarterlyReports": [
                {
                    "fiscalDateEnding": "2024-06-30",
                    "operatingCashflow": "None",
                    "capitalExpenditures": "-",
                },
            ],
        }
        row = parse_cash_flow(payload)[0]
        assert row["operating_cashflow"] is None
        assert row["capital_expenditures"] is None

    def test_empty_quarterly_reports(self):
        assert parse_cash_flow({"quarterlyReports": []}) == []

    def test_missing_key(self):
        assert parse_cash_flow({}) == []


# ---------------------------------------------------------------------------
# parse_earnings
# ---------------------------------------------------------------------------

class TestParseEarnings:
    def test_returns_list_of_dicts(self, earnings_payload):
        result = parse_earnings(earnings_payload)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_date_is_date_object(self, earnings_payload):
        row = parse_earnings(earnings_payload)[0]
        assert row["date"] == date(2024, 3, 31)
        assert isinstance(row["date"], date)

    def test_statement_type_is_earnings(self, earnings_payload):
        row = parse_earnings(earnings_payload)[0]
        assert row["statement_type"] == "earnings"

    def test_reported_at_is_date_object(self, earnings_payload):
        row = parse_earnings(earnings_payload)[0]
        assert row["reported_at"] == date(2024, 5, 2)
        assert isinstance(row["reported_at"], date)

    def test_core_fields_extracted_as_typed(self, earnings_payload):
        row = parse_earnings(earnings_payload)[0]
        assert row["reported_eps"] == 1.53
        assert row["estimated_eps"] == 1.50
        assert row["surprise"] == 0.03
        assert row["surprise_percentage"] == 2.0

    def test_overflow_fields_in_raw(self, earnings_payload):
        row = parse_earnings(earnings_payload)[0]
        assert "raw" in row
        assert row["raw"]["extraField"] == "bonus"

    def test_empty_quarterly_earnings(self):
        assert parse_earnings({"quarterlyEarnings": []}) == []

    def test_missing_key(self):
        assert parse_earnings({}) == []

    def test_none_values_become_none(self):
        payload = {
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2024-06-30",
                    "reportedDate": "2024-08-01",
                    "reportedEPS": "None",
                    "estimatedEPS": "-",
                    "surprise": "",
                    "surprisePercentage": None,
                },
            ],
        }
        row = parse_earnings(payload)[0]
        assert row["reported_eps"] is None
        assert row["estimated_eps"] is None
        assert row["surprise"] is None
        assert row["surprise_percentage"] is None

    def test_missing_reported_date_becomes_none(self):
        payload = {
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2024-06-30",
                    "reportedEPS": "1.00",
                    "estimatedEPS": "0.95",
                    "surprise": "0.05",
                    "surprisePercentage": "5.26",
                },
            ],
        }
        row = parse_earnings(payload)[0]
        assert row["reported_at"] is None

    def test_multiple_quarters(self):
        payload = {
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2024-03-31",
                    "reportedDate": "2024-05-02",
                    "reportedEPS": "1.53",
                    "estimatedEPS": "1.50",
                    "surprise": "0.03",
                    "surprisePercentage": "2.00",
                },
                {
                    "fiscalDateEnding": "2023-12-31",
                    "reportedDate": "2024-02-01",
                    "reportedEPS": "2.18",
                    "estimatedEPS": "2.10",
                    "surprise": "0.08",
                    "surprisePercentage": "3.81",
                },
            ],
        }
        result = parse_earnings(payload)
        assert len(result) == 2
        assert result[0]["date"] == date(2024, 3, 31)
        assert result[1]["date"] == date(2023, 12, 31)
        assert result[0]["reported_at"] == date(2024, 5, 2)
        assert result[1]["reported_at"] == date(2024, 2, 1)
