"""Tests for signal_strength_view SQL view.

TDD: These tests are written FIRST, before running the migration.
"""
import os
import pytest
import psycopg


@pytest.fixture
def db_conn():
    """Get database connection."""
    url = os.environ.get("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    with psycopg.connect(url) as conn:
        yield conn


@pytest.mark.skipif(
    not os.environ.get("ENABLE_DB_TESTS"),
    reason="Database tests disabled"
)
class TestSignalStrengthView:
    """Tests for the signal_strength_view."""

    def test_view_exists(self, db_conn):
        """Test that the view exists in the database."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.views
                    WHERE table_name = 'signal_strength_view'
                )
            """)
            exists = cur.fetchone()[0]
        assert exists, "signal_strength_view should exist"

    def test_view_returns_expected_columns(self, db_conn):
        """Test that the view has expected columns."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'signal_strength_view'
                ORDER BY ordinal_position
            """)
            columns = [row[0] for row in cur.fetchall()]

        expected_columns = [
            "data_id",
            "symbol",
            "prediction_date",
            "horizon_days",
            "quantile_component",
            "classifier_component",
            "q50",
            "q10",
            "q90",
            "predicted_class",
            "signal_score",
            "signal_direction",
            "quantile_confidence",
            "classifier_confidence",
            "margin",
            "avg_confidence",
            "iqr_width",
            "strong_threshold",
            "weak_threshold",
            "historical_volatility",
        ]
        assert columns == expected_columns

    def test_view_can_be_queried(self, db_conn):
        """Test that the view can be queried without error."""
        with db_conn.cursor() as cur:
            # Should not raise an error
            cur.execute("SELECT * FROM signal_strength_view LIMIT 1")
            # Result may be empty, that's fine
            cur.fetchall()

    def test_signal_score_bounded(self, db_conn):
        """Test that signal_score is always between -1 and 1."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM signal_strength_view
                WHERE signal_score < -1 OR signal_score > 1
            """)
            count = cur.fetchone()[0]
        assert count == 0, "All signal_score values should be in [-1, 1]"

    def test_signal_direction_values(self, db_conn):
        """Test that signal_direction only has valid values."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT signal_direction
                FROM signal_strength_view
            """)
            directions = {row[0] for row in cur.fetchall()}

        valid_directions = {"bullish", "bearish", "neutral"}
        assert directions <= valid_directions, f"Invalid directions: {directions - valid_directions}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
