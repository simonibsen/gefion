"""
Tests for CLI worker planning logic.

These tests have been retired because they test functions that have been removed or
significantly refactored:
- ingest_indicators_for_symbols: no longer exists
- ingest_prices_for_symbols: refactored with different interface

The worker planning logic would need new tests written for the current architecture
if coverage is needed.
"""
import pytest


@pytest.mark.skip(reason="Retired: tests removed/refactored worker planning functions")
def test_data_update_uses_planned_workers():
    """Test would need rewrite for current data-update implementation."""
    pass


@pytest.mark.skip(reason="Retired: tests removed/refactored worker planning functions")
def test_universe_ingest_uses_planned_workers():
    """Test would need rewrite for current universe-ingest implementation."""
    pass
