"""
Tests for OTEL_ENABLED default behavior (#185).

Real interactive dev runs should default to OTEL on; tests must stay off
regardless of that new default (conftest.py forces OTEL_ENABLED=false before
gefion is imported), and an explicit OTEL_ENABLED=false must still win.
"""
import os
from unittest.mock import patch

import gefion.observability as obs


def test_test_context_forces_otel_off():
    """conftest.py forces OTEL_ENABLED=false for the pytest process itself,
    so the module-level state must be off even though the new default would
    otherwise enable it for a real run."""
    assert obs.is_enabled() is False


def test_explicit_disable_wins_over_default():
    """An explicit OTEL_ENABLED=false disables tracing even in a context
    that would otherwise default to on (interactive terminal, no CI)."""
    with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
        with patch.object(obs.sys.stdout, "isatty", return_value=True):
            assert obs._resolve_otel_enabled() is False


def test_default_enables_in_interactive_non_test_context():
    """With OTEL_ENABLED unset, a real interactive dev run (a TTY, no CI
    marker) should default to on."""
    env = os.environ.copy()
    env.pop("OTEL_ENABLED", None)
    env.pop("CI", None)
    with patch.dict(os.environ, env, clear=True):
        with patch.object(obs.sys.stdout, "isatty", return_value=True):
            assert obs._resolve_otel_enabled() is True


def test_default_stays_off_when_non_interactive():
    """With OTEL_ENABLED unset and no TTY (e.g. piped/redirected output,
    worktree agents), the default stays off."""
    env = os.environ.copy()
    env.pop("OTEL_ENABLED", None)
    env.pop("CI", None)
    with patch.dict(os.environ, env, clear=True):
        with patch.object(obs.sys.stdout, "isatty", return_value=False):
            assert obs._resolve_otel_enabled() is False


def test_default_stays_off_in_ci():
    """With OTEL_ENABLED unset and a CI marker present, the default stays
    off even if stdout happens to look like a TTY."""
    env = os.environ.copy()
    env.pop("OTEL_ENABLED", None)
    env["CI"] = "true"
    with patch.dict(os.environ, env, clear=True):
        with patch.object(obs.sys.stdout, "isatty", return_value=True):
            assert obs._resolve_otel_enabled() is False
