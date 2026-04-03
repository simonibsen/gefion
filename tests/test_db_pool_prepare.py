import os

from gefion.db import pool


def test_should_prepare_respects_env(monkeypatch):
    # Ensure no pool is active (may be initialized by earlier tests)
    monkeypatch.setattr(pool, "_pool", None)
    monkeypatch.delenv("G2_PREPARE_STATEMENTS", raising=False)
    assert pool.should_prepare_statements() is True  # default on

    monkeypatch.setenv("G2_PREPARE_STATEMENTS", "0")
    assert pool.should_prepare_statements() is False

    monkeypatch.setenv("G2_PREPARE_STATEMENTS", "yes")
    assert pool.should_prepare_statements() is True


def test_pool_default_enables_prepare(monkeypatch):
    class DummyPool:
        pass

    dummy = DummyPool()
    dummy._gefion_prepare_statements = True
    monkeypatch.setattr(pool, "_pool", dummy)

    assert pool.should_prepare_statements() is True

    dummy._gefion_prepare_statements = False
    assert pool.should_prepare_statements() is False
