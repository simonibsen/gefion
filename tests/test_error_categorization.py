"""Test error categorization in progress reporter."""
from g2.utils.progress import ProgressReporter


def test_progress_reporter_categorizes_data_errors():
    """Test that data availability errors are categorized correctly."""
    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Simulate data errors (benign)
    reporter.step_done("SYM1", error=True, meta={"reason": "no price data"})
    reporter.step_done("SYM2", error=True, meta={"reason": "empty indicators"})
    reporter.step_done("SYM3", error=True, meta={"reason": "features failed: rsi"})

    assert reporter.errors == 3, "Total errors should be 3"
    assert reporter.data_errors == 3, "All should be data errors"
    assert reporter.resource_errors == 0, "No resource errors"


def test_progress_reporter_categorizes_resource_errors():
    """Test that resource/performance errors are categorized correctly."""
    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Simulate resource errors (affect scaling)
    reporter.step_done("SYM1", error=True, meta={"reason": "connection timeout"})
    reporter.step_done("SYM2", error=True, meta={"reason": "deadlock detected"})
    reporter.step_done("SYM3", error=True, meta={"reason": "out of memory"})

    assert reporter.errors == 3, "Total errors should be 3"
    assert reporter.data_errors == 0, "No data errors"
    assert reporter.resource_errors == 3, "All should be resource errors"


def test_progress_reporter_categorizes_mixed_errors():
    """Test that mixed error types are tracked separately."""
    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Mix of data and resource errors
    reporter.step_done("SYM1", error=True, meta={"reason": "no price data"})  # data
    reporter.step_done("SYM2", error=True, meta={"reason": "connection timeout"})  # resource
    reporter.step_done("SYM3", error=True, meta={"reason": "empty indicators"})  # data
    reporter.step_done("SYM4", error=True, meta={"reason": "deadlock detected"})  # resource

    assert reporter.errors == 4, "Total errors should be 4"
    assert reporter.data_errors == 2, "Should have 2 data errors"
    assert reporter.resource_errors == 2, "Should have 2 resource errors"


def test_progress_reporter_unknown_errors_default_to_resource():
    """Test that unknown/unrecognized errors default to resource errors."""
    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Unknown error type
    reporter.step_done("SYM1", error=True, meta={"reason": "something went wrong"})
    reporter.step_done("SYM2", error=True, meta={})  # No reason

    assert reporter.errors == 2, "Total errors should be 2"
    assert reporter.data_errors == 0, "No data errors"
    assert reporter.resource_errors == 2, "Unknown errors should be resource errors"


def test_progress_reporter_explicit_error_type_override():
    """Test that explicit error_type parameter overrides auto-detection."""
    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Force categorization even if reason would suggest otherwise
    reporter.step_done("SYM1", error=True, meta={"reason": "timeout"}, error_type="data")
    reporter.step_done("SYM2", error=True, meta={"reason": "no price data"}, error_type="resource")

    assert reporter.errors == 2, "Total errors should be 2"
    assert reporter.data_errors == 1, "First error forced to data"
    assert reporter.resource_errors == 1, "Second error forced to resource"


def test_progress_reporter_json_includes_error_breakdown():
    """Test that JSON output includes error type breakdown."""
    import io
    import sys
    import json

    reporter = ProgressReporter(total=10, json_output=True, enabled=True)

    # Mix of errors
    reporter.step_done("SYM1", error=True, meta={"reason": "no price data"})
    reporter.step_done("SYM2", error=True, meta={"reason": "connection timeout"})

    # Capture JSON output
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    reporter.step_done("SYM3", error=False, meta={"inserted": 100})

    sys.stdout = old_stdout
    output = buffer.getvalue()

    data = json.loads(output.strip())
    assert "errors" in data, "JSON should include total errors"
    assert "data_errors" in data, "JSON should include data_errors"
    assert "resource_errors" in data, "JSON should include resource_errors"
    assert data["errors"] == 2, "Should have 2 total errors"
    assert data["data_errors"] == 1, "Should have 1 data error"
    assert data["resource_errors"] == 1, "Should have 1 resource error"


def test_progress_reporter_table_shows_error_breakdown():
    """Test that progress table displays error breakdown."""
    import io
    from rich.console import Console

    reporter = ProgressReporter(total=10, json_output=False, enabled=True)

    # Add some errors
    reporter.step_done("SYM1", error=True, meta={"reason": "no price data"})
    reporter.step_done("SYM2", error=True, meta={"reason": "deadlock"})

    table = reporter._build_table()

    # Render table to string
    console = Console(file=io.StringIO(), width=120)
    console.print(table)
    table_str = console.file.getvalue()

    # Should show breakdown
    assert "Data errors" in table_str or "data" in table_str.lower(), "Table should show data errors"
    assert "Resource errors" in table_str or "resource" in table_str.lower(), "Table should show resource errors"
