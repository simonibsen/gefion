from __future__ import annotations

from typing import Optional, Tuple

import psycopg
from gefion.observability import create_span, set_attributes


def get_available_connections(url: str) -> Optional[Tuple[int, int, int]]:
    """
    Return (available, max_connections, used) or None on failure.
    """
    try:
        with create_span("utils.db_load.get_available_connections"):
            with psycopg.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SHOW max_connections;")
                    max_conn = int(cur.fetchone()[0])
                    cur.execute("SELECT count(*) FROM pg_stat_activity;")
                    used = int(cur.fetchone()[0])
                    return max_conn - used, max_conn, used
    except Exception:
        return None


def plan_workers(
    available: Optional[int],
    requested_fetch: Optional[int],
    requested_writer: Optional[int],
    default_fetch: int,
    default_writer: int,
    reserve: int = 2,
) -> Tuple[int, int]:
    """
    Plan fetch and writer worker counts given available connections.
    Ensures fetch + writer <= available - reserve when available is known.
    """
    fetch = requested_fetch or default_fetch
    writer = requested_writer or default_writer
    if available is None:
        return max(1, fetch), max(1, writer)
    budget = max(1, available - reserve)
    # Clamp writer first, then fetch
    writer = max(1, min(writer, budget))
    fetch = max(1, min(fetch, max(1, budget - writer)))
    return fetch, writer
