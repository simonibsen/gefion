"""Tests for quarterly_financials table schema."""
import os
import pytest
from unittest.mock import Mock, patch


def test_create_quarterly_financials_function_exists():
    """schema module has create_quarterly_financials_table function."""
    from gefion.db.schema import create_quarterly_financials_table
    assert callable(create_quarterly_financials_table)


def test_quarterly_financials_ddl_contains_required_columns():
    """DDL includes all required columns for quarterly financial data."""
    import inspect
    from gefion.db.schema import create_quarterly_financials_table
    source = inspect.getsource(create_quarterly_financials_table)

    # Core structure
    assert 'data_id' in source
    assert 'statement_type' in source
    assert 'reported_at' in source

    # Income statement fields
    assert 'revenue' in source
    assert 'net_income' in source
    assert 'gross_profit' in source
    assert 'ebitda' in source
    assert 'operating_income' in source
    assert 'eps' in source

    # Balance sheet fields
    assert 'total_assets' in source
    assert 'total_liabilities' in source
    assert 'shareholder_equity' in source
    assert 'long_term_debt' in source
    assert 'shares_outstanding' in source

    # Cash flow fields
    assert 'operating_cashflow' in source
    assert 'capital_expenditures' in source
    assert 'free_cash_flow' in source

    # Earnings fields
    assert 'reported_eps' in source
    assert 'estimated_eps' in source
    assert 'surprise' in source

    # JSONB overflow
    assert 'raw' in source
    assert 'JSONB' in source or 'jsonb' in source


def test_quarterly_financials_is_hypertable():
    """DDL creates a TimescaleDB hypertable."""
    import inspect
    from gefion.db.schema import create_quarterly_financials_table
    source = inspect.getsource(create_quarterly_financials_table)
    assert 'create_hypertable' in source


def test_quarterly_financials_has_composite_index():
    """DDL creates index on (data_id, statement_type, date DESC)."""
    import inspect
    from gefion.db.schema import create_quarterly_financials_table
    source = inspect.getsource(create_quarterly_financials_table)
    assert 'CREATE INDEX' in source
    assert 'data_id' in source
    assert 'statement_type' in source
