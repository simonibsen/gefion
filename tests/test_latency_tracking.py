"""Test latency tracking in progress reporter."""
import time
from gefion.utils.progress import ProgressReporter


def test_progress_reporter_tracks_write_latency():
    """Test that ProgressReporter tracks and reports write latency."""
    import pytest

    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Simulate writes with known latency
    reporter.record_write_latency(0.1)  # 100ms
    reporter.record_write_latency(0.2)  # 200ms
    reporter.record_write_latency(0.3)  # 300ms

    # Should calculate average
    avg_latency = reporter.get_avg_write_latency()
    assert avg_latency == pytest.approx(0.2), f"Expected 0.2s, got {avg_latency}s"


def test_progress_reporter_latency_moving_average():
    """Test that latency uses exponential moving average."""
    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Record several writes
    for _ in range(5):
        reporter.record_write_latency(0.1)

    # Recent write should influence average more
    reporter.record_write_latency(1.0)

    avg = reporter.get_avg_write_latency()
    # EMA should be between 0.1 and 1.0, closer to recent value
    assert 0.1 < avg < 1.0, f"EMA should be between 0.1 and 1.0, got {avg}"


def test_progress_reporter_displays_latency():
    """Test that latency appears in progress table."""
    import io
    from rich.console import Console

    reporter = ProgressReporter(total=10, json_output=False, enabled=True)
    reporter.record_write_latency(0.123)

    table = reporter._build_table()

    # Render table to string
    console = Console(file=io.StringIO(), width=120)
    console.print(table)
    table_str = console.file.getvalue()

    # Should show latency in milliseconds
    assert "123" in table_str or "ms" in table_str, f"Latency should appear in table, got: {table_str}"


def test_progress_reporter_latency_in_json():
    """Test that latency appears in JSON output."""
    reporter = ProgressReporter(total=10, json_output=True, enabled=True)
    reporter.record_write_latency(0.15)

    # Capture JSON output
    import io
    import sys
    import json

    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    reporter.step_done("TEST", error=False, meta={"inserted": 100})

    sys.stdout = old_stdout
    output = buffer.getvalue()

    data = json.loads(output.strip())
    assert "avg_write_latency_ms" in data, "JSON should include latency"
    assert data["avg_write_latency_ms"] == 150, f"Expected 150ms, got {data['avg_write_latency_ms']}"
