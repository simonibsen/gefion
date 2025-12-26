"""
Database functions for cross-sectional features.

Cross-sectional features compare stocks to their peers at the same point in time.
"""
from __future__ import annotations

from typing import Any, Dict, List

import psycopg
from psycopg.rows import dict_row


def insert_cross_sectional_features(
    conn: psycopg.Connection, features: List[Dict[str, Any]]
) -> int:
    """
    Insert cross-sectional features into database.

    Args:
        conn: Database connection
        features: List of dicts with:
            - symbol: Stock symbol
            - date: Date string (YYYY-MM-DD)
            - feature_name: Feature name (e.g., "return_vs_market")
            - comparison_group: Comparison group (default: "market")
            - value: Feature value (float)
            - rank: Rank (int)
            - percentile: Percentile (float 0-1)

    Returns:
        Number of rows inserted

    Example:
        features = [
            {
                "symbol": "AAPL",
                "date": "2024-01-02",
                "feature_name": "return_vs_market",
                "comparison_group": "market",
                "value": 3.67,
                "rank": 1,
                "percentile": 1.0,
            }
        ]

        inserted = insert_cross_sectional_features(conn, features)
    """
    if not features:
        return 0

    # Build a mapping from symbol to stock ID
    symbols = list(set(f["symbol"] for f in features))

    with conn.cursor(row_factory=dict_row) as cur:
        # Fetch stock IDs
        placeholders = ",".join(["%s"] * len(symbols))
        cur.execute(
            f"SELECT id, symbol FROM stocks WHERE symbol IN ({placeholders})", symbols
        )
        rows = cur.fetchall()
        symbol_to_id = {row["symbol"]: row["id"] for row in rows}

        # Build insert data
        insert_data = []
        for f in features:
            symbol = f["symbol"]
            if symbol not in symbol_to_id:
                # Skip features for symbols not in database
                continue

            data_id = symbol_to_id[symbol]
            insert_data.append(
                (
                    data_id,
                    f["date"],
                    f["feature_name"],
                    f.get("comparison_group", "market"),
                    f.get("value"),
                    f.get("rank"),
                    f.get("percentile"),
                )
            )

        if not insert_data:
            return 0

        # Batch insert with ON CONFLICT DO UPDATE
        cur.executemany(
            """
            INSERT INTO cross_sectional_features
                (data_id, date, feature_name, comparison_group, value, rank, percentile)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (data_id, date, feature_name, comparison_group)
            DO UPDATE SET
                value = EXCLUDED.value,
                rank = EXCLUDED.rank,
                percentile = EXCLUDED.percentile,
                created_at = NOW()
            """,
            insert_data,
        )

        return len(insert_data)
