import os

import pytest

from g2.cli import _auto_workers


def test_auto_workers_local_uses_cpu_count(monkeypatch):
    """Test that local computation uses CPU count for worker sizing."""
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    # Should use CPU count, but cap at reasonable maximum
    workers = _auto_workers(True, calls_per_minute=75)
    assert workers >= 4, "Should use at least 4 workers with 8 CPUs"
    assert workers <= 8, "Should cap workers even with high CPU count"


def test_auto_workers_local_small_system(monkeypatch):
    """Test worker sizing on small systems."""
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    workers = _auto_workers(True, calls_per_minute=75)
    assert workers >= 2, "Should use at least 2 workers"


def test_auto_workers_api_rate_limited(monkeypatch):
    """Test that API mode respects rate limits."""
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    # With 75 calls/minute, should be conservative with workers
    workers = _auto_workers(False, calls_per_minute=75)
    assert workers >= 2, "Should have minimum workers"
    assert workers <= 4, "Should limit workers to respect API rate limits"


def test_auto_workers_api_high_rate(monkeypatch):
    """Test with high API rate limit."""
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    # With 500 calls/minute, can use more workers
    workers = _auto_workers(False, calls_per_minute=500)
    assert workers >= 4, "Should use more workers with high rate limit"
    assert workers <= 10, "Should cap even with high rate limits"


def test_auto_workers_never_zero(monkeypatch):
    """Test that we always return at least 1 worker."""
    monkeypatch.setattr(os, "cpu_count", lambda: None)  # Simulate unknown CPU count
    workers = _auto_workers(True, calls_per_minute=5)
    assert workers >= 1, "Should always return at least 1 worker"


def test_auto_workers_backwards_compatible(monkeypatch):
    """Test that the function still works with original behavior for small setups."""
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    # For small systems, should still work well
    workers = _auto_workers(True, calls_per_minute=75)
    assert 2 <= workers <= 4, "Should return reasonable count for small system"
