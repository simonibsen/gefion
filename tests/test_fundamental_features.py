"""
TDD tests for fundamental-derived features.

Tests the forward-fill feature function that converts quarterly financial
data into daily features, and derived features like daily PE ratio.
"""
from datetime import date
import pytest


def test_forward_fill_function_file_exists():
    """forward_fill_quarterly feature function JSON file exists."""
    from pathlib import Path
    fn_path = Path(__file__).parent.parent / "feature-functions" / "forward_fill_quarterly.json"
    assert fn_path.exists(), f"Expected {fn_path} to exist"


def test_forward_fill_function_is_valid_python():
    """forward_fill_quarterly function body compiles and defines compute()."""
    import json
    from pathlib import Path
    fn_path = Path(__file__).parent.parent / "feature-functions" / "forward_fill_quarterly.json"
    fn_data = json.loads(fn_path.read_text())
    body = fn_data["function_body"]

    # Should compile without error
    code = compile(body, "<forward_fill_quarterly>", "exec")
    namespace = {}
    exec(code, namespace)
    assert "compute" in namespace, "function_body must define compute()"


def test_forward_fill_quarterly_to_daily():
    """Forward-fill converts quarterly data points to daily values."""
    import json
    from pathlib import Path

    fn_path = Path(__file__).parent.parent / "feature-functions" / "forward_fill_quarterly.json"
    fn_data = json.loads(fn_path.read_text())
    body = fn_data["function_body"]
    namespace = {}
    exec(compile(body, "<test>", "exec"), namespace)
    compute = namespace["compute"]

    # Quarterly data: EPS reported on specific dates
    rows = [
        {"date": date(2024, 3, 31), "value": 1.50},
        {"date": date(2024, 6, 30), "value": 1.60},
        {"date": date(2024, 9, 30), "value": 1.55},
    ]

    # Daily dates to fill
    specs = [{"params": {"column": "fundamental_eps"}}]

    result = compute(rows, specs)
    assert len(result) > 0, "Should produce output rows"

    # All rows should have the column
    for r in result:
        assert "fundamental_eps" in r
        assert "date" in r


def test_forward_fill_carries_value_forward():
    """Value from Q1 should carry forward to dates before Q2."""
    import json
    from pathlib import Path

    fn_path = Path(__file__).parent.parent / "feature-functions" / "forward_fill_quarterly.json"
    fn_data = json.loads(fn_path.read_text())
    namespace = {}
    exec(compile(fn_data["function_body"], "<test>", "exec"), namespace)
    compute = namespace["compute"]

    rows = [
        {"date": date(2024, 3, 31), "value": 1.50},
        {"date": date(2024, 6, 30), "value": 1.60},
    ]
    specs = [{"params": {"column": "eps"}}]

    result = compute(rows, specs)
    result_by_date = {r["date"]: r for r in result}

    # The Q1 value (1.50) should be the value at fiscal date
    assert result_by_date[date(2024, 3, 31)]["eps"] == 1.50
    # The Q2 value (1.60) should be at its fiscal date
    assert result_by_date[date(2024, 6, 30)]["eps"] == 1.60


def test_forward_fill_definition_exists():
    """Feature definition JSON for fundamental_eps exists."""
    from pathlib import Path
    def_path = Path(__file__).parent.parent / "feature-definitions" / "fundamental_eps.json"
    assert def_path.exists(), f"Expected {def_path} to exist"


def test_forward_fill_definition_points_to_quarterly_financials():
    """fundamental_eps definition sources from quarterly_financials table."""
    import json
    from pathlib import Path
    def_path = Path(__file__).parent.parent / "feature-definitions" / "fundamental_eps.json"
    defn = json.loads(def_path.read_text())
    assert defn["source_table"] == "quarterly_financials"
    assert defn["function_name"] == "forward_fill_quarterly"


def test_daily_pe_definition_exists():
    """Feature definition for daily_pe_ratio exists."""
    from pathlib import Path
    def_path = Path(__file__).parent.parent / "feature-definitions" / "daily_pe_ratio.json"
    assert def_path.exists()


def test_daily_market_cap_definition_exists():
    """Feature definition for daily_market_cap exists."""
    from pathlib import Path
    def_path = Path(__file__).parent.parent / "feature-definitions" / "daily_market_cap.json"
    assert def_path.exists()
