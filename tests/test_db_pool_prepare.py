import os

from g2.db import pool


def test_should_prepare_respects_env(monkeypatch):
    monkeypatch.delenv("G2_PREPARE_STATEMENTS", raising=False)
    assert pool.should_prepare_statements() is True  # default on

    monkeypatch.setenv("G2_PREPARE_STATEMENTS", "0")
    assert pool.should_prepare_statements() is False

    monkeypatch.setenv("G2_PREPARE_STATEMENTS", "yes")
    assert pool.should_prepare_statements() is True
