"""Tests for experiment safety checks module.

TDD: These tests are written FIRST, before implementation.
Tests real system calls (shutil.disk_usage, psutil.virtual_memory)
using extreme thresholds to verify behavior without mocking.
"""
import os
import pytest


class TestCheckDiskSpace:
    """Tests for check_disk_space function."""

    def test_returns_dict_with_required_keys(self):
        """check_disk_space returns a dict with ok, free_gb, message keys."""
        from gefion.experiments.safety import check_disk_space

        result = check_disk_space()

        assert isinstance(result, dict)
        assert "ok" in result
        assert "free_gb" in result
        assert "message" in result
        assert isinstance(result["ok"], bool)
        assert isinstance(result["free_gb"], float)
        assert isinstance(result["message"], str)

    def test_very_low_threshold_always_passes(self):
        """A threshold of 0.001 GB should always pass on any real system."""
        from gefion.experiments.safety import check_disk_space

        result = check_disk_space(min_free_gb=0.001)

        assert result["ok"] is True
        assert result["free_gb"] > 0.001

    def test_impossibly_high_threshold_always_fails(self):
        """A threshold of 999999 GB should always fail on any real system."""
        from gefion.experiments.safety import check_disk_space

        result = check_disk_space(min_free_gb=999999)

        assert result["ok"] is False
        assert result["free_gb"] < 999999


class TestCheckMemory:
    """Tests for check_memory function."""

    def test_returns_dict_with_required_keys(self):
        """check_memory returns a dict with ok, used_pct, available_mb, message keys."""
        from gefion.experiments.safety import check_memory

        result = check_memory()

        assert isinstance(result, dict)
        assert "ok" in result
        assert "used_pct" in result
        assert "available_mb" in result
        assert "message" in result
        assert isinstance(result["ok"], bool)
        assert isinstance(result["used_pct"], float)
        assert isinstance(result["available_mb"], float)
        assert isinstance(result["message"], str)

    def test_threshold_100_always_passes(self):
        """A threshold of 100.0% should always pass since usage can't exceed 100%."""
        from gefion.experiments.safety import check_memory

        result = check_memory(max_used_pct=100.0)

        assert result["ok"] is True

    def test_threshold_0_always_fails(self):
        """A threshold of 0.0% should always fail since some memory is always in use."""
        from gefion.experiments.safety import check_memory

        result = check_memory(max_used_pct=0.0)

        assert result["ok"] is False
        assert result["used_pct"] > 0.0


class TestCheckDbHealth:
    """Tests for check_db_health function."""

    @pytest.mark.skipif(
        os.environ.get("ENABLE_DB_TESTS") != "1",
        reason="Database tests disabled (ENABLE_DB_TESTS != 1)",
    )
    def test_returns_dict_with_required_keys(self):
        """check_db_health returns a dict with ok and message keys."""
        from gefion.experiments.safety import check_db_health
        from gefion.schema import test_db_url

        import psycopg2

        conn = psycopg2.connect(test_db_url())
        try:
            result = check_db_health(conn)

            assert isinstance(result, dict)
            assert "ok" in result
            assert "message" in result
            assert isinstance(result["ok"], bool)
            assert isinstance(result["message"], str)
        finally:
            conn.close()


class TestRunPreflightChecks:
    """Tests for run_preflight_checks function."""

    def test_aggregates_results(self):
        """run_preflight_checks returns ok and a list of check dicts."""
        from gefion.experiments.safety import run_preflight_checks

        # Pass None for conn to skip db check if the function supports it,
        # or use a real connection if DB tests are enabled.
        result = run_preflight_checks(conn=None, min_free_gb=0.001, max_memory_pct=100.0)

        assert isinstance(result, dict)
        assert "ok" in result
        assert "checks" in result
        assert isinstance(result["ok"], bool)
        assert isinstance(result["checks"], list)
        assert len(result["checks"]) >= 2  # at least disk + memory

        for check in result["checks"]:
            assert isinstance(check, dict)
            assert "ok" in check
            assert "message" in check

    def test_returns_false_if_any_check_fails(self):
        """run_preflight_checks returns ok=False when any individual check fails."""
        from gefion.experiments.safety import run_preflight_checks

        # Use an impossibly high disk threshold to force a failure
        result = run_preflight_checks(conn=None, min_free_gb=999999, max_memory_pct=100.0)

        assert result["ok"] is False

        # At least one check should have failed
        failed = [c for c in result["checks"] if not c["ok"]]
        assert len(failed) >= 1
