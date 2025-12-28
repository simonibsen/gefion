"""
Tests for system_status MCP tool.

Following TDD: Write tests first, then implement.
This is a comprehensive meta-tool that includes infrastructure health,
data analysis, gap identification, and actionable suggestions.
"""
import pytest
from datetime import date, timedelta


class TestSystemStatusComprehensive:
    """Tests for comprehensive system_status tool."""

    def test_system_status_includes_infrastructure_health(self):
        """Test that system_status includes all infrastructure checks."""
        # Expected: Returns health status for Docker, PostgreSQL, Tempo
        # Should incorporate health_check functionality
        pass

    def test_system_status_analyzes_data_freshness(self):
        """Test that system_status checks if data is current."""
        # Expected: Checks latest OHLCV date vs today
        # Identifies if data is stale (e.g., > 1 day old)
        pass

    def test_system_status_identifies_missing_features(self):
        """Test that system_status detects when features aren't computed."""
        # Expected: Checks if computed_features table is empty
        # Suggests running feat-compute
        pass

    def test_system_status_suggests_data_update_when_stale(self):
        """Test suggestions for stale data."""
        # Expected: If latest data is old, suggest:
        # "g2 data-update --exchange NASDAQ --limit 10"
        pass

    def test_system_status_prioritizes_suggestions(self):
        """Test that suggestions have priority levels."""
        # Expected: Returns issues with priority: high/medium/low
        # Infrastructure down = high
        # Stale data = high
        # Missing features = medium
        # No models = low
        pass

    def test_system_status_returns_ordered_next_steps(self):
        """Test that next_steps are in logical order."""
        # Expected: Returns ordered workflow:
        # 1. Fix infrastructure (if down)
        # 2. Update price data (if stale)
        # 3. Compute features (if missing)
        # 4. Build dataset / train models (if none)
        pass

    def test_system_status_when_everything_healthy(self):
        """Test system_status when all is well."""
        # Expected: Returns status: "healthy", no critical issues
        # May still suggest optional improvements
        pass

    def test_system_status_handles_multiple_issues(self):
        """Test handling of multiple simultaneous issues."""
        # Expected: Returns all issues with priorities
        # Orders suggestions by priority
        # Provides clear next steps
        pass

    def test_system_status_includes_metrics(self):
        """Test that status includes key metrics."""
        # Expected: Returns metrics like:
        # - Days since last data update
        # - Number of stocks with data
        # - Number of computed features
        # - Database size
        pass

    def test_system_status_suggests_specific_commands(self):
        """Test that suggestions include executable commands."""
        # Expected: Each suggestion has:
        # - description: What's wrong
        # - command: Exact g2 command to fix it
        # - priority: high/medium/low
        pass


class TestSystemStatusDataAnalysis:
    """Tests for data analysis components."""

    def test_detects_empty_database(self):
        """Test detection of empty database."""
        # Expected: If no stocks/data, suggest initial data ingestion
        pass

    def test_detects_partial_feature_computation(self):
        """Test detection of incomplete feature computation."""
        # Expected: If some stocks have features, some don't
        # Suggest computing missing features
        pass

    def test_calculates_data_staleness_in_days(self):
        """Test staleness calculation."""
        # Expected: Returns days_since_last_update
        # E.g., "358 days old" for 2024-01-01 data when now is 2025-12-24
        pass

    def test_identifies_missing_ml_infrastructure(self):
        """Test detection of missing ML models/datasets."""
        # Expected: Checks for datasets, models, predictions
        # Suggests ML workflow if missing
        pass


class TestSystemStatusSuggestionEngine:
    """Tests for suggestion prioritization and ordering."""

    def test_infrastructure_down_is_highest_priority(self):
        """Test that infrastructure issues are prioritized."""
        # Expected: If PostgreSQL down, that's priority: "critical"
        # Should be first in suggestions list
        pass

    def test_suggestions_are_actionable(self):
        """Test that every suggestion has a clear action."""
        # Expected: Each suggestion has executable command
        # Not just "data is stale" but "run: g2 data-update ..."
        pass

    def test_next_steps_workflow_is_logical(self):
        """Test that next_steps follow dependency order."""
        # Expected: Don't suggest "compute features" before "ingest data"
        # Dependency: data → features → dataset → model → predictions
        pass


class TestFeatureRegistrationDetection:
    """Tests for detecting unregistered features and functions."""

    def test_detects_unregistered_feature_definitions(self):
        """Test detection of feature definitions on disk not in DB."""
        # Expected: Compares feature-definitions/*.json count vs feature_definitions table
        # If mismatch, suggests: g2 feat-def-import
        pass

    def test_detects_unregistered_feature_functions(self):
        """Test detection of feature functions on disk not in DB."""
        # Expected: Compares feature-functions/*.json count vs feature_functions table
        # If mismatch, suggests: g2 feat-fx-import
        pass

    def test_feature_definition_mismatch_priority(self):
        """Test that unregistered features have medium priority."""
        # Expected: unregistered_features priority = "medium"
        # Not critical but should be imported
        pass

    def test_suggests_correct_import_commands(self):
        """Test that suggestions include correct import commands."""
        # Expected:
        # - Feature definitions: "g2 feat-def-import --directory feature-definitions"
        # - Feature functions: "g2 feat-fx-import --directory feature-functions"
        pass

    def test_handles_missing_directories(self):
        """Test graceful handling when directories don't exist."""
        # Expected: If feature-definitions/ doesn't exist, no error
        # Just reports as "0 definitions on disk"
        pass

    def test_all_registered_no_issue(self):
        """Test when all disk files are registered in DB."""
        # Expected: No unregistered_features issue
        # Status can still be healthy
        pass


class TestFundamentalsStalenessDetection:
    """Tests for detecting stale fundamentals data (sector/industry)."""

    def test_detects_stale_fundamentals(self):
        """Test detection of fundamentals data older than threshold."""
        # Expected: Checks MAX(updated_at) from stocks table
        # If older than 30 days, suggests: g2 fundamentals-update
        pass

    def test_detects_missing_fundamentals(self):
        """Test detection when stocks have no fundamentals data."""
        # Expected: Checks for stocks with NULL sector/industry
        # Suggests: g2 fundamentals-update
        pass

    def test_fundamentals_staleness_priority_is_low(self):
        """Test that stale fundamentals has lower priority than prices."""
        # Expected: stale_fundamentals priority = "low"
        # Prices/features are more time-sensitive
        pass

    def test_fundamentals_includes_age_in_days(self):
        """Test that status includes fundamentals age."""
        # Expected: data.fundamentals_days_old = N
        # Helps user understand urgency
        pass

    def test_suggests_correct_fundamentals_command(self):
        """Test that suggestion includes correct command."""
        # Expected: "g2 fundamentals-update"
        pass

    def test_no_issue_when_fundamentals_fresh(self):
        """Test no issue when fundamentals recently updated."""
        # Expected: If all stocks updated within 30 days
        # No stale_fundamentals issue
        pass
