#!/usr/bin/env python3
"""Quick diagnostic to check latest price dates."""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import psycopg
from g2.config import load_settings
from g2.db.ingest import latest_price_date

settings = load_settings()

with psycopg.connect(settings.database_url) as conn:
    # Check a few sample stocks
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, symbol FROM stocks LIMIT 10
    """)

    stocks = cursor.fetchall()

    print(f"Today: {date.today()}")
    print(f"\nSample of latest price dates:")
    print(f"{'Symbol':<10} {'Latest Date':<15} {'Days Old':<10}")
    print("-" * 40)

    for data_id, symbol in stocks:
        latest = latest_price_date(conn, data_id)
        if latest:
            days_old = (date.today() - latest).days
            print(f"{symbol:<10} {str(latest):<15} {days_old:<10}")
        else:
            print(f"{symbol:<10} {'None':<15} {'N/A':<10}")

    # Overall stats
    cursor.execute("""
        SELECT
            COUNT(*) as total_stocks,
            MAX(latest_date) as newest,
            COUNT(CASE WHEN CURRENT_DATE - latest_date <= 1 THEN 1 END) as within_1_day,
            COUNT(CASE WHEN CURRENT_DATE - latest_date > 1 THEN 1 END) as older_than_1_day
        FROM (
            SELECT data_id, MAX(date) as latest_date
            FROM stock_ohlcv
            GROUP BY data_id
        ) latest
    """)

    stats = cursor.fetchone()
    total, newest, within_1, older = stats

    print(f"\nOverall Statistics:")
    print(f"Total stocks: {total}")
    print(f"Newest date: {newest}")
    print(f"Within 1 day (will skip): {within_1} ({within_1/total*100:.1f}%)")
    print(f"Older than 1 day (will fetch): {older} ({older/total*100:.1f}%)")
