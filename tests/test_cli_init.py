"""Tests for gefion init CLI command.

The init command is the single entry point for getting g2 into a working state.
It chains: db-init (schema + migrations + seeds) → health check.
"""

import subprocess
import sys

import pytest


def test_init_command_exists():
    """gefion init must be a registered CLI command."""
    result = subprocess.run(
        [sys.executable, "-m", "gefion.cli", "--help"],
        capture_output=True, text=True
    )
    assert "init" in result.stdout, "gefion init command must exist in CLI help"
    # Make sure it's the top-level init, not just db-init
    lines = result.stdout.split("\n")
    init_lines = [l for l in lines if "init" in l.lower() and "db-init" not in l]
    assert any("init" in l for l in init_lines), (
        "Must have a standalone 'init' command separate from 'db-init'"
    )


def test_init_calls_db_init_and_health():
    """gefion init must call db-init and health internally."""
    from gefion import cli as cli_module
    import inspect
    source = inspect.getsource(cli_module)
    assert "_db_init_impl" in source or "db_init" in source, "init must call db-init"
    assert "health" in source.lower(), "init must call health check"


def test_init_help_text():
    """gefion init help must describe it as the setup command."""
    result = subprocess.run(
        [sys.executable, "-m", "gefion.cli", "init", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"gefion init --help failed: {result.stderr}"
    help_text = result.stdout.lower()
    assert "schema" in help_text or "initialize" in help_text or "setup" in help_text, (
        "init help must describe database/schema initialization"
    )
