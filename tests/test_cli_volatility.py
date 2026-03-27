"""Tests for g2 volatility CLI commands.

TDD: These tests are written FIRST, before implementation.
"""
import pytest
from typer.testing import CliRunner

from gefion.cli import app

runner = CliRunner()


class TestVolatilityComputeCommand:
    """Tests for the volatility compute command."""

    def test_volatility_compute_help_shows_options(self):
        """Test that volatility compute --help shows expected options."""
        result = runner.invoke(app, ["volatility", "compute", "--help"])

        assert result.exit_code == 0
        assert "--symbols" in result.output or "--exchange" in result.output
        assert "--horizons" in result.output
        assert "--date" in result.output

    def test_volatility_compute_requires_symbols_or_exchange_or_limit(self):
        """Test that command requires either symbols, exchange, or limit."""
        result = runner.invoke(app, ["volatility", "compute", "--horizons", "7,30"])

        # Should fail without symbols, exchange, or limit
        assert result.exit_code != 0


class TestVolatilityModule:
    """Tests for volatility module integration."""

    def test_compute_adaptive_thresholds_for_multiple_horizons(self):
        """Test computing thresholds for multiple horizons."""
        from gefion.ml.volatility import compute_adaptive_thresholds

        vol = 0.25
        horizons = [7, 30, 90]

        thresholds = {}
        for h in horizons:
            weak, strong = compute_adaptive_thresholds(vol, h)
            thresholds[h] = (weak, strong)

        # All thresholds should be positive
        for h, (weak, strong) in thresholds.items():
            assert weak > 0
            assert strong > weak

        # Longer horizons should have wider thresholds
        assert thresholds[30][0] > thresholds[7][0]
        assert thresholds[90][0] > thresholds[30][0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
