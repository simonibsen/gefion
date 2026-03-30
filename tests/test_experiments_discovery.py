"""Tests for data discovery module.

TDD: These tests are written FIRST, before implementation.
"""
import os
import pytest


# ---------------------------------------------------------------------------
# Fixtures: sample principles and data structures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_principles():
    """Principles catalog matching the YAML schema."""
    return [
        {
            "id": "mean-reversion",
            "data_requirements": ["stock_ohlcv.close", "stock_ohlcv.volume"],
            "experiment_types": ["feature_engineering"],
            "claim": "Mean reversion is profitable on liquid stocks",
            "experiment_design": "Test z-score based entry signals",
        },
        {
            "id": "momentum",
            "data_requirements": ["stock_ohlcv.close", "fundamentals.market_cap"],
            "experiment_types": ["strategy_params", "feature_engineering"],
            "claim": "Momentum carries over 3-12 month windows",
            "experiment_design": "Compare lookback windows",
        },
        {
            "id": "volatility-regime",
            "data_requirements": ["options_iv.implied_vol"],
            "experiment_types": ["feature_engineering"],
            "claim": "Volatility regime detection improves timing",
            "experiment_design": "Cluster vol regimes and backtest",
        },
    ]


@pytest.fixture
def sample_data_sources():
    """Simulated output of discover_data_sources."""
    return [
        {
            "table": "stock_ohlcv",
            "columns": ["open", "high", "low", "close", "volume"],
            "row_count": 500_000,
            "date_range": ("2015-01-01", "2026-03-28"),
            "coverage_pct": 98.5,
            "freshness_days": 1,
        },
        {
            "table": "fundamentals",
            "columns": ["market_cap", "pe_ratio", "revenue"],
            "row_count": 12_000,
            "date_range": ("2018-01-01", "2026-03-15"),
            "coverage_pct": 85.0,
            "freshness_days": 14,
        },
    ]


@pytest.fixture
def sample_features():
    """Simulated output of discover_features."""
    return [
        {
            "name": "sma_20",
            "function_name": "compute_sma",
            "active": True,
            "params": {"window": 20},
            "coverage_pct": 95.0,
        },
        {
            "name": "rsi_14",
            "function_name": "compute_rsi",
            "active": True,
            "params": {"window": 14},
            "coverage_pct": 90.0,
        },
    ]


@pytest.fixture
def gaps_with_missing():
    """Gaps fixture where at least one requirement is missing."""
    return [
        {
            "principle_id": "volatility-regime",
            "required_data": ["options_iv.implied_vol"],
            "available": [],
            "missing": ["options_iv.implied_vol"],
            "hypothesis": None,
        },
    ]


# ---------------------------------------------------------------------------
# Pure function tests — no DB required
# ---------------------------------------------------------------------------


class TestDiscoverGaps:
    """Tests for discover_gaps pure function."""

    def test_detects_gap_when_data_exists_but_no_feature(
        self, sample_data_sources, sample_features, sample_principles
    ):
        """A principle whose required data exists in data_sources but has
        no corresponding feature should appear as a gap."""
        from gefion.experiments.discovery import discover_gaps

        gaps = discover_gaps(sample_data_sources, sample_features, sample_principles)

        # stock_ohlcv.close and stock_ohlcv.volume exist in data_sources,
        # but there is no feature explicitly covering them for the principle.
        # At minimum the volatility-regime principle requires options_iv
        # which is entirely missing.
        assert isinstance(gaps, list)
        assert len(gaps) > 0

        # Each gap must have the required keys
        for gap in gaps:
            assert "principle_id" in gap
            assert "required_data" in gap
            assert "available" in gap
            assert "missing" in gap
            assert "hypothesis" in gap

    def test_no_gaps_when_all_requirements_met(self, sample_features):
        """When every principle's data requirements are satisfied,
        discover_gaps should return an empty list."""
        from gefion.experiments.discovery import discover_gaps

        # Craft data sources that cover everything
        full_data_sources = [
            {
                "table": "stock_ohlcv",
                "columns": ["close", "volume"],
                "row_count": 100,
                "date_range": ("2020-01-01", "2026-03-28"),
                "coverage_pct": 99.0,
                "freshness_days": 1,
            },
        ]
        principles = [
            {
                "id": "simple",
                "data_requirements": ["stock_ohlcv.close"],
                "experiment_types": ["feature_engineering"],
                "claim": "Simple claim",
                "experiment_design": "Simple design",
            },
        ]

        gaps = discover_gaps(full_data_sources, sample_features, principles)

        assert isinstance(gaps, list)
        assert len(gaps) == 0

    def test_missing_data_marked_correctly(
        self, sample_data_sources, sample_features, sample_principles
    ):
        """When a principle requires data from a table that does not exist
        at all, the requirement should appear in the 'missing' list."""
        from gefion.experiments.discovery import discover_gaps

        gaps = discover_gaps(sample_data_sources, sample_features, sample_principles)

        # volatility-regime requires options_iv.implied_vol which is not
        # in sample_data_sources
        vol_gaps = [g for g in gaps if g["principle_id"] == "volatility-regime"]
        assert len(vol_gaps) == 1
        assert "options_iv.implied_vol" in vol_gaps[0]["missing"]
        assert "options_iv.implied_vol" not in vol_gaps[0]["available"]


class TestGenerateHypotheses:
    """Tests for generate_hypotheses pure function."""

    def test_produces_hypotheses_from_gaps(self, gaps_with_missing, sample_principles):
        """generate_hypotheses should return at least one hypothesis for
        each gap that has missing data."""
        from gefion.experiments.discovery import generate_hypotheses

        hypotheses = generate_hypotheses(gaps_with_missing, sample_principles)

        assert isinstance(hypotheses, list)
        assert len(hypotheses) > 0

        for h in hypotheses:
            assert "feasibility" in h
            # feasibility should be a meaningful value
            assert h["feasibility"] is not None

    def test_empty_gaps_returns_empty(self, sample_principles):
        """With no gaps, generate_hypotheses should return an empty list."""
        from gefion.experiments.discovery import generate_hypotheses

        hypotheses = generate_hypotheses([], sample_principles)

        assert isinstance(hypotheses, list)
        assert len(hypotheses) == 0

    def test_hypothesis_includes_required_fields(
        self, gaps_with_missing, sample_principles
    ):
        """Every hypothesis must include principle_id and experiment_type."""
        from gefion.experiments.discovery import generate_hypotheses

        hypotheses = generate_hypotheses(gaps_with_missing, sample_principles)

        for h in hypotheses:
            assert "principle_id" in h
            assert "experiment_type" in h
            assert "description" in h
            assert "feasibility" in h


class TestRunDiscovery:
    """Tests for run_discovery orchestrator."""

    def test_returns_all_required_keys(self, sample_principles):
        """run_discovery must return a dict with data_sources, features,
        gaps, and hypotheses."""
        from unittest.mock import MagicMock
        from gefion.experiments.discovery import run_discovery

        mock_conn = MagicMock()
        # Configure the mock cursor to return empty results so the
        # orchestrator can run without a real database.
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.description = []
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = run_discovery(mock_conn, sample_principles)

        assert isinstance(result, dict)
        assert "data_sources" in result
        assert "features" in result
        assert "gaps" in result
        assert "hypotheses" in result

        # Each value should be a list
        assert isinstance(result["data_sources"], list)
        assert isinstance(result["features"], list)
        assert isinstance(result["gaps"], list)
        assert isinstance(result["hypotheses"], list)


# ---------------------------------------------------------------------------
# DB integration tests — guarded by ENABLE_DB_TESTS
# ---------------------------------------------------------------------------

DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


@pytest.mark.skipif(not DB_TESTS_ENABLED, reason="Database tests disabled (set ENABLE_DB_TESTS=1)")
class TestDiscoverDataSourcesDB:
    """Integration tests for discover_data_sources (requires DB)."""

    def test_returns_list_of_dicts(self):
        from gefion.experiments.discovery import discover_data_sources
        from gefion.schema import test_db_url
        import psycopg2

        conn = psycopg2.connect(test_db_url())
        try:
            result = discover_data_sources(conn)
            assert isinstance(result, list)
            for item in result:
                assert isinstance(item, dict)
                assert "table" in item
                assert "columns" in item
                assert "row_count" in item
                assert "date_range" in item
                assert "coverage_pct" in item
                assert "freshness_days" in item
        finally:
            conn.close()


@pytest.mark.skipif(not DB_TESTS_ENABLED, reason="Database tests disabled (set ENABLE_DB_TESTS=1)")
class TestDiscoverFeaturesDB:
    """Integration tests for discover_features (requires DB)."""

    def test_returns_list_of_dicts(self):
        from gefion.experiments.discovery import discover_features
        from gefion.schema import test_db_url
        import psycopg2

        conn = psycopg2.connect(test_db_url())
        try:
            result = discover_features(conn)
            assert isinstance(result, list)
            for item in result:
                assert isinstance(item, dict)
                assert "name" in item
                assert "function_name" in item
                assert "active" in item
                assert "params" in item
                assert "coverage_pct" in item
        finally:
            conn.close()
