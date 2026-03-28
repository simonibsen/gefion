"""Database connection utilities for Streamlit UI."""

import os
from contextlib import contextmanager
from typing import Generator, List, Optional

import streamlit as st
from gefion.observability import create_span, set_attributes


@st.cache_resource
def get_db_pool():
    """Get or create database connection pool (cached)."""
    import psycopg_pool

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://gefion:gefionpass@localhost:6432/gefion"
    )

    return psycopg_pool.ConnectionPool(
        db_url,
        min_size=1,
        max_size=5,
        open=True,
    )


@contextmanager
def get_connection():
    """Get a database connection from the pool.

    Sets autocommit=True since UI queries are read-only. This prevents
    'rolling back returned connection' warnings from the pool when
    connections are returned with an open transaction.
    """
    with create_span("ui.database.get_connection"):
        pool = get_db_pool()
        conn = pool.getconn()
    try:
        conn.autocommit = True
        yield conn
    except Exception as e:
        # On OID or connection errors, try to reset the connection
        error_msg = str(e).lower()
        if "oid" in error_msg or "connection" in error_msg or "bad" in error_msg:
            try:
                conn.close()
            except Exception:
                pass
            # Clear the cached pool to force fresh connections
            get_db_pool.clear()
        raise
    finally:
        try:
            conn.autocommit = False
            pool.putconn(conn)
        except Exception:
            pass  # Connection may already be closed


def get_symbols(status: str = "Active") -> List[str]:
    """Get list of symbols from database."""
    with create_span("ui.database.get_symbols", status=status):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol FROM stocks WHERE status = %s ORDER BY symbol",
                    (status,)
                )
                return [row[0] for row in cur.fetchall()]


def get_sectors() -> List[str]:
    """Get list of unique sectors."""
    with create_span("ui.database.get_sectors"):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL ORDER BY sector"
                )
                return [row[0] for row in cur.fetchall()]


def get_models() -> List[dict]:
    """Get list of ML models."""
    with create_span("ui.database.get_models"):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, version, algorithm, created_at
                    FROM ml_models
                    ORDER BY created_at DESC
                """)
                return [
                    {"name": row[0], "version": row[1], "type": row[2], "created": row[3]}
                    for row in cur.fetchall()
                ]


def get_feature_definitions() -> List[dict]:
    """Get list of feature definitions."""
    with create_span("ui.database.get_feature_definitions"):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, function_name, active
                    FROM feature_definitions
                    ORDER BY name
                """)
                return [
                    {"name": row[0], "function": row[1], "active": row[2]}
                    for row in cur.fetchall()
                ]
