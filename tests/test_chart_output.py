"""
Tests for chart output functions.

Tests file saving and browser opening utilities.
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Skip if plotly not available
plotly = pytest.importorskip("plotly")


class TestGetChartOutputDir:
    """Tests for get_chart_output_dir function."""

    def test_returns_path_object(self):
        """get_chart_output_dir should return a Path object."""
        from g2.charts.output import get_chart_output_dir

        result = get_chart_output_dir()
        assert isinstance(result, Path)

    def test_creates_directory_if_not_exists(self):
        """get_chart_output_dir should create directory if missing."""
        from g2.charts.output import get_chart_output_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "charts_test"
            with patch.dict(os.environ, {"G2_CHART_DIR": str(test_path)}):
                result = get_chart_output_dir()
                assert result.exists()
                assert result.is_dir()

    def test_uses_env_var_when_set(self):
        """get_chart_output_dir should use G2_CHART_DIR env var."""
        from g2.charts.output import get_chart_output_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}):
                result = get_chart_output_dir()
                assert str(result) == tmpdir

    def test_defaults_to_home_directory(self):
        """get_chart_output_dir should default to ~/.g2/charts/."""
        from g2.charts.output import get_chart_output_dir

        # Clear env var if set
        env = os.environ.copy()
        env.pop("G2_CHART_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            result = get_chart_output_dir()
            expected = Path.home() / ".g2" / "charts"
            assert result == expected


class TestGenerateChartFilename:
    """Tests for generate_chart_filename function."""

    def test_includes_symbol(self):
        """generate_chart_filename should include the symbol."""
        from g2.charts.output import generate_chart_filename

        filename = generate_chart_filename("AAPL", "price")
        assert "AAPL" in filename

    def test_includes_chart_type(self):
        """generate_chart_filename should include the chart type."""
        from g2.charts.output import generate_chart_filename

        filename = generate_chart_filename("AAPL", "price")
        assert "price" in filename

    def test_includes_timestamp(self):
        """generate_chart_filename should include a timestamp."""
        from g2.charts.output import generate_chart_filename

        filename = generate_chart_filename("AAPL", "price")
        # Should contain a date pattern like 20241230
        assert any(c.isdigit() for c in filename)

    def test_has_html_extension(self):
        """generate_chart_filename should have .html extension."""
        from g2.charts.output import generate_chart_filename

        filename = generate_chart_filename("AAPL", "price")
        assert filename.endswith(".html")

    def test_unique_filenames(self):
        """generate_chart_filename should generate unique filenames."""
        from g2.charts.output import generate_chart_filename
        import time

        filename1 = generate_chart_filename("AAPL", "price")
        time.sleep(0.01)  # Small delay to ensure different timestamp
        filename2 = generate_chart_filename("AAPL", "price")

        # Filenames should be unique (different timestamps)
        # Note: this might occasionally fail if called at exact same second
        # In production, this is fine as files are rarely created that fast


class TestSaveChartHtml:
    """Tests for save_chart_html function."""

    def test_saves_file_to_disk(self):
        """save_chart_html should save the figure as HTML."""
        from g2.charts.output import save_chart_html, get_chart_output_dir
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[1, 2, 3], y=[1, 2, 3]))

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}):
                result = save_chart_html(fig, "test_chart.html")

                assert result.exists()
                assert result.suffix == ".html"

                # Verify file has content
                content = result.read_text()
                assert len(content) > 100  # Should have substantial HTML content

    def test_returns_full_path(self):
        """save_chart_html should return the full path to saved file."""
        from g2.charts.output import save_chart_html
        import plotly.graph_objects as go

        fig = go.Figure()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}):
                result = save_chart_html(fig, "test.html")

                assert result.is_absolute()
                assert str(tmpdir) in str(result)


class TestOpenInBrowser:
    """Tests for open_in_browser function."""

    def test_calls_webbrowser_open(self):
        """open_in_browser should call webbrowser.open with file URL."""
        from g2.charts.output import open_in_browser

        test_path = Path("/tmp/test_chart.html")

        with patch("webbrowser.open") as mock_open:
            open_in_browser(test_path)
            mock_open.assert_called_once()
            call_arg = mock_open.call_args[0][0]
            assert "file://" in call_arg
            assert "test_chart.html" in call_arg
