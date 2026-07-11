"""Unified CLI output component (issue #88, Constitution V).

TDD: written FIRST. One `render_cli_output(key, title)` in
ui/components/cli_output.py replaces the three divergent render patterns
(process status, cull status, freeform CLI output). The JSON-line parser is
pure and tested without Streamlit; the views delegate to the component so a
new CLI command gets proper UI rendering for free.
"""
from pathlib import Path

UI = Path(__file__).parent.parent / "src" / "gefion" / "ui"


# --- pure parser ----------------------------------------------------------------------

def test_parser_splits_events_final_and_plain():
    from gefion.ui.components.cli_output import parse_output_lines
    lines = [
        '{"phase": "cull", "table": "stock_ohlcv", "deleted": 42, "step": 1, "total_steps": 2}',
        "plain progress text",
        '{"phase": "complete", "deleted": 42, "tables": 1}',
    ]
    parsed = parse_output_lines(lines)
    assert len(parsed["events"]) == 1
    assert parsed["events"][0]["table"] == "stock_ohlcv"
    assert parsed["final"]["deleted"] == 42          # last summary-shaped dict
    assert parsed["plain"] == ["plain progress text"]


def test_parser_progress_fraction_from_hints():
    from gefion.ui.components.cli_output import parse_output_lines
    lines = ['{"phase": "x", "step": 3, "total_steps": 4}']
    parsed = parse_output_lines(lines)
    assert parsed["progress"] == 0.75                # step/total_steps hint


def test_parser_plain_text_fallback():
    from gefion.ui.components.cli_output import parse_output_lines
    parsed = parse_output_lines(["just", "text"])
    assert parsed["events"] == [] and parsed["final"] is None
    assert parsed["plain"] == ["just", "text"]
    assert parsed["progress"] is None


def test_parser_final_is_last_summary_dict():
    from gefion.ui.components.cli_output import parse_output_lines
    lines = ['{"summary": {"a": 1}}', '{"summary": {"a": 2}}']
    parsed = parse_output_lines(lines)
    assert parsed["final"] == {"summary": {"a": 2}}


# --- component + delegation -----------------------------------------------------------

def test_component_module_exists_with_renderers():
    src = (UI / "components" / "cli_output.py").read_text()
    assert "def render_cli_output(" in src
    assert "def render_cli_state(" in src
    assert "def parse_output_lines(" in src


def test_views_delegate_to_component():
    data = (UI / "views" / "data.py").read_text()
    assistant = (UI / "views" / "assistant.py").read_text()
    for src, name in ((data, "data.py"), (assistant, "assistant.py")):
        assert "from gefion.ui.components.cli_output import" in src, name
    # the cull-specific renderer is gone; process status delegates
    assert "_render_cull_status" not in data.replace(
        "render_cli_state", "")  # no leftover bespoke cull renderer
    assert "render_cli_state(" in data or "render_cli_output(" in data
    assert ("render_cli_state(" in assistant or "render_cli_output(" in assistant
            or "render_structured(" in assistant)
