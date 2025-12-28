"""
TDD tests for cross-sectional compute CLI command.

Tests the CLI interface for computing cross-sectional rankings.
"""
import pytest
from typer.testing import CliRunner


runner = CliRunner()


class TestCrossSectionalComputeCommand:
    """Tests for g2 cross-sectional-compute CLI command."""

    def test_command_exists(self):
        """Test that cross-sectional-compute command is registered."""
        from g2.cli import app

        result = runner.invoke(app, ["cross-sectional-compute", "--help"])
        assert result.exit_code == 0
        assert "cross-sectional" in result.output.lower()

    def test_help_shows_feature_option(self):
        """Test that --feature option is documented."""
        from g2.cli import app

        result = runner.invoke(app, ["cross-sectional-compute", "--help"])
        assert "--feature" in result.output
        assert "-f" in result.output  # short form

    def test_help_shows_sectors_option(self):
        """Test that --sectors/--no-sectors option is documented."""
        from g2.cli import app

        result = runner.invoke(app, ["cross-sectional-compute", "--help"])
        assert "--sectors" in result.output or "--no-sectors" in result.output

    def test_help_shows_industries_option(self):
        """Test that --industries option is documented."""
        from g2.cli import app

        result = runner.invoke(app, ["cross-sectional-compute", "--help"])
        assert "--industries" in result.output

    def test_help_shows_json_option(self):
        """Test that --json option is documented."""
        from g2.cli import app

        result = runner.invoke(app, ["cross-sectional-compute", "--help"])
        assert "--json" in result.output


@pytest.mark.skipif(
    not pytest.importorskip("psycopg"),
    reason="Database tests require psycopg"
)
class TestCrossSectionalComputeIntegration:
    """Integration tests for cross-sectional-compute with real database."""

    @pytest.fixture
    def db_url(self):
        """Get database URL for tests."""
        import os
        return os.environ.get("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")

    @pytest.mark.skipif(
        not pytest.importorskip("os").environ.get("ENABLE_DB_TESTS"),
        reason="Set ENABLE_DB_TESTS=1 to run database integration tests"
    )
    def test_computes_rankings_for_valid_feature(self, db_url):
        """Test that command successfully computes rankings."""
        from g2.cli import app

        result = runner.invoke(app, [
            "cross-sectional-compute",
            "--feature", "indicator_rsi_14",
            "--db-url", db_url,
        ])

        # Should succeed (or fail gracefully if no data)
        assert result.exit_code == 0 or "No data found" in result.output

    @pytest.mark.skipif(
        not pytest.importorskip("os").environ.get("ENABLE_DB_TESTS"),
        reason="Set ENABLE_DB_TESTS=1 to run database integration tests"
    )
    def test_json_output_format(self, db_url):
        """Test that --json outputs valid JSON."""
        import json
        from g2.cli import app

        result = runner.invoke(app, [
            "cross-sectional-compute",
            "--feature", "indicator_rsi_14",
            "--db-url", db_url,
            "--json",
        ])

        assert result.exit_code == 0
        # Should be valid JSON
        parsed = json.loads(result.output)
        assert "success" in parsed

    @pytest.mark.skipif(
        not pytest.importorskip("os").environ.get("ENABLE_DB_TESTS"),
        reason="Set ENABLE_DB_TESTS=1 to run database integration tests"
    )
    def test_nonexistent_feature_returns_error(self, db_url):
        """Test that nonexistent feature returns error message."""
        from g2.cli import app

        result = runner.invoke(app, [
            "cross-sectional-compute",
            "--feature", "nonexistent_feature_xyz",
            "--db-url", db_url,
        ])

        # Should show error
        assert "No data found" in result.output or "error" in result.output.lower()
