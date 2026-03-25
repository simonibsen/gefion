"""Tests for UI error logger (g2.ui.errors).

Tests log_ui_error, read_session_errors, and clear_errors without
requiring Streamlit or database connections.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def error_file(tmp_path):
    """Provide a temporary error file path and patch _error_file to use it."""
    path = tmp_path / "ui_errors.jsonl"
    with patch("gefion.ui.errors._error_file", return_value=path):
        yield path


class TestLogUIError:
    """Test log_ui_error writes entries correctly."""

    def test_log_creates_jsonl_entry(self, error_file):
        from gefion.ui.errors import log_ui_error

        log_ui_error(source="test_source", message="something broke")

        lines = error_file.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["source"] == "test_source"
        assert entry["message"] == "something broke"
        assert "timestamp" in entry

    def test_log_includes_context(self, error_file):
        from gefion.ui.errors import log_ui_error

        log_ui_error(
            source="bg_process",
            message="exit code 1",
            context={"key": "data_update", "returncode": 1},
        )

        entry = json.loads(error_file.read_text().strip())
        assert entry["context"]["key"] == "data_update"
        assert entry["context"]["returncode"] == 1

    def test_log_appends_multiple_entries(self, error_file):
        from gefion.ui.errors import log_ui_error

        log_ui_error(source="a", message="first")
        log_ui_error(source="b", message="second")

        lines = error_file.read_text().splitlines()
        assert len(lines) == 2

    def test_log_without_context_omits_key(self, error_file):
        from gefion.ui.errors import log_ui_error

        log_ui_error(source="src", message="msg")

        entry = json.loads(error_file.read_text().strip())
        assert "context" not in entry

    def test_log_timestamp_is_utc_iso(self, error_file):
        from gefion.ui.errors import log_ui_error

        log_ui_error(source="src", message="msg")

        entry = json.loads(error_file.read_text().strip())
        ts = datetime.fromisoformat(entry["timestamp"])
        assert ts.tzinfo is not None  # timezone-aware


class TestReadSessionErrors:
    """Test read_session_errors reads and filters correctly."""

    def test_read_returns_all_entries(self, error_file):
        from gefion.ui.errors import log_ui_error, read_session_errors

        log_ui_error(source="a", message="first")
        log_ui_error(source="b", message="second")

        errors = read_session_errors()
        assert len(errors) == 2
        assert errors[0]["source"] == "a"
        assert errors[1]["source"] == "b"

    def test_read_returns_empty_when_no_file(self, error_file):
        from gefion.ui.errors import read_session_errors

        errors = read_session_errors()
        assert errors == []

    def test_read_since_filters_old_entries(self, error_file):
        from gefion.ui.errors import read_session_errors

        # Write entries with known timestamps
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()

        error_file.write_text(
            json.dumps({"timestamp": old_ts, "source": "old", "message": "old"}) + "\n"
            + json.dumps({"timestamp": new_ts, "source": "new", "message": "new"}) + "\n"
        )

        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        errors = read_session_errors(since=since)
        assert len(errors) == 1
        assert errors[0]["source"] == "new"

    def test_read_skips_malformed_lines(self, error_file):
        from gefion.ui.errors import read_session_errors

        error_file.write_text(
            "not json\n"
            + json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "source": "ok", "message": "ok"}) + "\n"
            + "\n"  # blank line
        )

        errors = read_session_errors()
        assert len(errors) == 1
        assert errors[0]["source"] == "ok"


class TestClearErrors:
    """Test clear_errors removes the log file."""

    def test_clear_removes_file(self, error_file):
        from gefion.ui.errors import log_ui_error, clear_errors

        log_ui_error(source="a", message="msg")
        assert error_file.exists()

        clear_errors()
        assert not error_file.exists()

    def test_clear_is_safe_when_no_file(self, error_file):
        from gefion.ui.errors import clear_errors

        # Should not raise
        clear_errors()
