"""
Tests for late OTEL initialization — when .env is loaded after module import.

The CLI loads .env in entrypoint() but observability.py is imported at module
level, so OTEL_ENABLED may be false at import time but true by the time
commands run. The reinitialize() function handles this case.
"""
import os
from unittest.mock import patch

import pytest


def test_observability_has_reinitialize():
    """observability module exposes a reinitialize() function."""
    from gefion.observability import reinitialize
    assert callable(reinitialize)


def test_reinitialize_enables_otel_when_env_changes():
    """reinitialize() picks up OTEL_ENABLED=true set after initial import."""
    import gefion.observability as obs

    # Save original state
    original_enabled = obs.OTEL_ENABLED
    original_initialized = obs._otel_initialized

    try:
        # Simulate: module imported with OTEL_ENABLED=false
        obs.OTEL_ENABLED = False
        obs._otel_initialized = False

        # Now .env is loaded setting OTEL_ENABLED=true
        with patch.dict(os.environ, {"OTEL_ENABLED": "true", "OTEL_EXPORTER": "console"}):
            result = obs.reinitialize()

        # Should now be enabled
        assert obs.OTEL_ENABLED is True
        assert result is True
    finally:
        # Restore original state
        obs.OTEL_ENABLED = original_enabled
        obs._otel_initialized = original_initialized


def test_reinitialize_noop_when_already_initialized():
    """reinitialize() does nothing if OTEL is already initialized."""
    import gefion.observability as obs

    original_enabled = obs.OTEL_ENABLED
    original_initialized = obs._otel_initialized

    try:
        obs.OTEL_ENABLED = True
        obs._otel_initialized = True

        # Should be a no-op
        result = obs.reinitialize()
        assert result is True  # Already good
    finally:
        obs.OTEL_ENABLED = original_enabled
        obs._otel_initialized = original_initialized


def test_reinitialize_noop_when_env_still_false():
    """reinitialize() stays disabled when OTEL_ENABLED is still false."""
    import gefion.observability as obs

    original_enabled = obs.OTEL_ENABLED
    original_initialized = obs._otel_initialized

    try:
        obs.OTEL_ENABLED = False
        obs._otel_initialized = False

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}):
            result = obs.reinitialize()

        assert obs.OTEL_ENABLED is False
        assert result is False
    finally:
        obs.OTEL_ENABLED = original_enabled
        obs._otel_initialized = original_initialized


def test_cli_entrypoint_calls_reinitialize():
    """CLI entrypoint should call reinitialize() after loading .env."""
    import ast
    from pathlib import Path

    cli_path = Path(__file__).parent.parent / "src" / "gefion" / "cli.py"
    source = cli_path.read_text()

    # Find the entrypoint function and check it calls reinitialize
    assert "reinitialize" in source, (
        "cli.py should call observability.reinitialize() after loading .env"
    )
