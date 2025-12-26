"""
TDD tests for cross-sectional ranking feature function.

Tests ranking computation for different comparison groups:
- 'market': rank vs all stocks
- 'sector:X': rank vs sector peers
- 'industry:X': rank vs industry peers
"""
import pytest
from typing import List, Dict, Any


# Import will fail until we implement - that's TDD red phase
from g2.compute.cross_sectional import compute_rankings_by_group


class TestComputeRankingsByGroup:
    """Tests for flexible cross-sectional ranking computation."""

    def test_market_ranking_returns_all_stocks(self):
        """Test that market comparison ranks all stocks."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECHNOLOGY"},
            {"symbol": "JPM", "data_id": 3, "value": 60.0, "sector": "FINANCE"},
        ]

        results = compute_rankings_by_group(data, comparison_group="market")

        assert len(results) == 3
        # AAPL highest value = rank 1
        aapl = next(r for r in results if r["symbol"] == "AAPL")
        assert aapl["rank"] == 1
        assert aapl["percentile"] == 1.0
        assert aapl["comparison_group"] == "market"

        # JPM lowest value = rank 3
        jpm = next(r for r in results if r["symbol"] == "JPM")
        assert jpm["rank"] == 3
        assert jpm["percentile"] == 0.0

    def test_sector_ranking_only_includes_sector_peers(self):
        """Test that sector comparison only ranks within sector."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECHNOLOGY"},
            {"symbol": "JPM", "data_id": 3, "value": 200.0, "sector": "FINANCE"},
        ]

        results = compute_rankings_by_group(data, comparison_group="sector:TECHNOLOGY")

        # Only TECHNOLOGY stocks should be ranked
        assert len(results) == 2
        symbols = {r["symbol"] for r in results}
        assert symbols == {"AAPL", "MSFT"}

        # AAPL is #1 in tech (higher value)
        aapl = next(r for r in results if r["symbol"] == "AAPL")
        assert aapl["rank"] == 1
        assert aapl["percentile"] == 1.0
        assert aapl["comparison_group"] == "sector:TECHNOLOGY"

    def test_sector_ranking_excludes_stocks_without_sector(self):
        """Test that stocks without sector are excluded from sector ranking."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECHNOLOGY"},
            {"symbol": "XXX", "data_id": 3, "value": 200.0, "sector": None},
        ]

        results = compute_rankings_by_group(data, comparison_group="sector:TECHNOLOGY")

        assert len(results) == 2
        symbols = {r["symbol"] for r in results}
        assert "XXX" not in symbols

    def test_market_ranking_includes_stocks_without_sector(self):
        """Test that market ranking includes all stocks regardless of sector."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "XXX", "data_id": 2, "value": 200.0, "sector": None},
        ]

        results = compute_rankings_by_group(data, comparison_group="market")

        assert len(results) == 2
        # XXX has highest value, should be rank 1
        xxx = next(r for r in results if r["symbol"] == "XXX")
        assert xxx["rank"] == 1

    def test_percentile_calculation_with_ties(self):
        """Test percentile calculation when values are tied."""
        data = [
            {"symbol": "A", "data_id": 1, "value": 100.0, "sector": None},
            {"symbol": "B", "data_id": 2, "value": 100.0, "sector": None},
            {"symbol": "C", "data_id": 3, "value": 50.0, "sector": None},
        ]

        results = compute_rankings_by_group(data, comparison_group="market")

        # A and B tied at top
        a = next(r for r in results if r["symbol"] == "A")
        b = next(r for r in results if r["symbol"] == "B")
        c = next(r for r in results if r["symbol"] == "C")

        # Tied stocks should have same rank
        assert a["rank"] == b["rank"]
        assert c["rank"] == 3  # C is lowest

    def test_single_stock_in_group(self):
        """Test ranking when only one stock in comparison group."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "JPM", "data_id": 2, "value": 200.0, "sector": "FINANCE"},
        ]

        results = compute_rankings_by_group(data, comparison_group="sector:TECHNOLOGY")

        assert len(results) == 1
        aapl = results[0]
        assert aapl["rank"] == 1
        assert aapl["percentile"] == 1.0  # Single stock is 100th percentile

    def test_empty_comparison_group(self):
        """Test ranking when no stocks match comparison group."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
        ]

        results = compute_rankings_by_group(data, comparison_group="sector:FINANCE")

        assert len(results) == 0

    def test_result_includes_data_id(self):
        """Test that results include data_id for database storage."""
        data = [
            {"symbol": "AAPL", "data_id": 42, "value": 100.0, "sector": "TECHNOLOGY"},
        ]

        results = compute_rankings_by_group(data, comparison_group="market")

        assert results[0]["data_id"] == 42

    def test_result_includes_original_value(self):
        """Test that results include the original value."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 123.45, "sector": "TECHNOLOGY"},
        ]

        results = compute_rankings_by_group(data, comparison_group="market")

        assert results[0]["value"] == 123.45

    def test_industry_ranking(self):
        """Test ranking by industry comparison group."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECH", "industry": "SOFTWARE"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECH", "industry": "SOFTWARE"},
            {"symbol": "NVDA", "data_id": 3, "value": 200.0, "sector": "TECH", "industry": "HARDWARE"},
        ]

        results = compute_rankings_by_group(data, comparison_group="industry:SOFTWARE")

        assert len(results) == 2
        symbols = {r["symbol"] for r in results}
        assert symbols == {"AAPL", "MSFT"}
        assert results[0]["comparison_group"] == "industry:SOFTWARE"


class TestComputeRankingsByGroupAllGroups:
    """Tests for computing rankings across all comparison groups at once."""

    def test_compute_all_groups_returns_market_and_sectors(self):
        """Test computing rankings for market + all sectors in one call."""
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECHNOLOGY"},
            {"symbol": "JPM", "data_id": 3, "value": 60.0, "sector": "FINANCE"},
        ]

        from g2.compute.cross_sectional import compute_all_rankings

        results = compute_all_rankings(data)

        # Should have market rankings (3) + TECHNOLOGY (2) + FINANCE (1) = 6
        assert len(results) == 6

        # Check we have all comparison groups
        groups = {r["comparison_group"] for r in results}
        assert "market" in groups
        assert "sector:TECHNOLOGY" in groups
        assert "sector:FINANCE" in groups
