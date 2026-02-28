"""Structural test ensuring all @app.command() functions have create_span tracing."""

from __future__ import annotations

import re
from pathlib import Path


def _parse_cli_commands_and_spans() -> tuple[set[str], set[str]]:
    """Parse cli.py to find all @app.command names and their create_span calls.

    Returns:
        (command_names, traced_command_names)
    """
    cli_path = Path(__file__).parent.parent / "src" / "g2" / "cli.py"
    source = cli_path.read_text()

    # Find all @app.command("name") decorators
    command_pattern = re.compile(r'@app\.command\("([^"]+)"\)')
    command_names = set(command_pattern.findall(source))

    # Find all create_span("cli.<name>") calls
    span_pattern = re.compile(r'create_span\(\s*"cli\.([^"]+)"')
    traced_names = set(span_pattern.findall(source))

    return command_names, traced_names


def test_all_cli_commands_have_tracing_spans():
    """Every @app.command() must have a corresponding create_span('cli.<name>') call."""
    command_names, traced_names = _parse_cli_commands_and_spans()

    assert len(command_names) > 0, "No @app.command() decorators found — test is broken"

    missing = command_names - traced_names
    assert missing == set(), (
        f"{len(missing)} CLI command(s) missing create_span tracing: "
        f"{', '.join(sorted(missing))}"
    )
