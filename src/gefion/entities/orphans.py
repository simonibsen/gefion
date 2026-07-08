"""Entity-integrity orphan scan (007, T007 — US4).

The honest price of the declared entity model: with the hard FK retired,
orphaned feature values are *detectable* rather than impossible — so
detection must be loud. For every entity table any feature declares, count
feature values whose data_id has no home there. Consumed by db-health's
entity_integrity section (dimension-coverage style), which ships in the same
increment as the FK drop — never an undetectable window.
"""
from __future__ import annotations

from typing import Dict

from psycopg import sql

from gefion.entities.registry import declared_entity_tables, entity_identifier
from gefion.observability import create_span, set_attributes


def scan(conn) -> Dict[str, int]:
    """Orphan counts per declared entity table (0 = clean).

    One anti-join per table: values of features declaring it whose data_id
    does not exist there. Runtime is bounded — integer-key NOT EXISTS, one
    pass per declared table.
    """
    with create_span("entities.orphans.scan") as span:
        report: Dict[str, int] = {}
        for table in declared_entity_tables(conn):
            ident = entity_identifier(conn, table)  # validates before composing
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT count(*)
                        FROM computed_features cf
                        JOIN feature_definitions fd ON fd.id = cf.feature_id
                        WHERE fd.entity_table = {table_name}
                          AND NOT EXISTS (
                              SELECT 1 FROM {table} e WHERE e.id = cf.data_id
                          )
                        """
                    ).format(table=ident, table_name=sql.Literal(table))
                )
                report[table] = cur.fetchone()[0]
        set_attributes(span, n_tables=len(report),
                       total_orphans=sum(report.values()))
        return report
