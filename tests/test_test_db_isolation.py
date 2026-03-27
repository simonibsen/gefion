"""
Unit tests for test database isolation infrastructure.

Tests that test_db_url() correctly resolves the test database URL
using the priority: TEST_DATABASE_URL > DATABASE_URL + _test suffix > default gefion_test.
"""
import os

import pytest

from gefion.db import schema


class TestTestDbUrl:
    """Tests for schema.test_db_url() resolution logic."""

    def test_test_db_url_returns_test_database(self, monkeypatch):
        """With no env vars set, returns URL with gefion_test DB name."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        url = schema.test_db_url()
        assert url == "postgresql://gefion:gefionpass@localhost:6432/gefion_test"

    def test_test_db_url_uses_TEST_DATABASE_URL(self, monkeypatch):
        """TEST_DATABASE_URL env var takes priority over everything."""
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://custom:pass@remote:5432/my_test_db")
        monkeypatch.setenv("DATABASE_URL", "postgresql://gefion:gefionpass@localhost:6432/gefion")

        url = schema.test_db_url()
        assert url == "postgresql://custom:pass@remote:5432/my_test_db"

    def test_test_db_url_derives_from_DATABASE_URL(self, monkeypatch):
        """Appends _test to DB name from DATABASE_URL when TEST_DATABASE_URL not set."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@myhost:5433/mydb")

        url = schema.test_db_url()
        assert url == "postgresql://user:pass@myhost:5433/mydb_test"

    def test_test_db_url_no_double_suffix(self, monkeypatch):
        """Doesn't append _test if DB name already ends with _test."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@myhost:5433/mydb_test")

        url = schema.test_db_url()
        assert url == "postgresql://user:pass@myhost:5433/mydb_test"

    def test_append_test_suffix_with_query_params(self, monkeypatch):
        """Handles URLs with query parameters like ?sslmode=require."""
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/prod_db?sslmode=require&connect_timeout=10")

        url = schema.test_db_url()
        assert url == "postgresql://user:pass@host:5432/prod_db_test?sslmode=require&connect_timeout=10"


class TestAppendTestSuffix:
    """Tests for _append_test_suffix() helper."""

    def test_simple_url(self):
        result = schema._append_test_suffix("postgresql://gefion:gefionpass@localhost:6432/gefion")
        assert result == "postgresql://gefion:gefionpass@localhost:6432/gefion_test"

    def test_already_has_suffix(self):
        result = schema._append_test_suffix("postgresql://gefion:gefionpass@localhost:6432/gefion_test")
        assert result == "postgresql://gefion:gefionpass@localhost:6432/gefion_test"

    def test_with_query_string(self):
        result = schema._append_test_suffix("postgresql://u:p@h:5432/db?sslmode=require")
        assert result == "postgresql://u:p@h:5432/db_test?sslmode=require"

    def test_preserves_scheme(self):
        result = schema._append_test_suffix("postgres://u:p@h:5432/mydb")
        assert result == "postgres://u:p@h:5432/mydb_test"
