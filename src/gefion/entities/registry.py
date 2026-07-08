"""Entity-table declaration and validation (007, T005).

The declared entity axis is only trustworthy if declarations are validated at
registration time: the named table must exist and carry an integer `id`
primary key (spec edge case: refused at registration, never a runtime
surprise). The legal set is self-maintaining — whatever tables satisfy the
shape — with no registry-of-registries (Simplicity, R1). Dynamic table names
are composed with psycopg.sql.Identifier only after validation.
"""
from __future__ import annotations

import re
from typing import List

from psycopg import sql

from gefion.observability import create_span, set_attributes

# Conservative lexical gate before any catalog lookup: plain lowercase
# identifiers only. Hostile names never reach SQL in any form.
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_INTEGER_TYPES = {"integer", "bigint", "smallint"}


class EntityTableError(ValueError):
    """Raised when an entity-table declaration is invalid."""


def validate_entity_table(conn, name: str) -> None:
    """Refuse unless `name` is a real table with an integer `id` primary key.

    Raises EntityTableError with the specific reason; returns None on success.
    """
    with create_span("entities.registry.validate", entity_table=name):
        if not isinstance(name, str) or not _NAME_RE.match(name or ""):
            raise EntityTableError(
                f"entity table name {name!r} is not a plain lowercase identifier")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_name = %s""",
                (name,),
            )
            if cur.fetchone() is None:
                raise EntityTableError(f"entity table {name!r} does not exist")
            cur.execute(
                """SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                   FROM pg_index i
                   JOIN pg_attribute a ON a.attrelid = i.indrelid
                                      AND a.attnum = ANY(i.indkey)
                   WHERE i.indrelid = %s::regclass AND i.indisprimary""",
                (name,),
            )
            pk = cur.fetchall()
        if len(pk) != 1 or pk[0][0] != "id" or pk[0][1] not in _INTEGER_TYPES:
            raise EntityTableError(
                f"entity table {name!r} must have an integer 'id' primary key "
                f"(found: {pk!r})")


def entity_identifier(conn, name: str) -> sql.Identifier:
    """A safely composable identifier for an entity table — validated first."""
    validate_entity_table(conn, name)
    return sql.Identifier(name)


def declared_entity_tables(conn) -> List[str]:
    """Every entity table currently declared by any feature definition —
    the iteration set for the orphan scan and the feeds graph."""
    with create_span("entities.registry.declared") as span:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT entity_table FROM feature_definitions ORDER BY 1")
            tables = [r[0] for r in cur.fetchall()]
        set_attributes(span, n_tables=len(tables))
        return tables
