"""
Test to demonstrate the performance difference between iterrows() and to_dict('records').
"""
import time
from datetime import date, timedelta

import pandas as pd
import pytest


def test_iterrows_vs_to_dict_performance():
    """
    Demonstrate that to_dict('records') is significantly faster than iterrows()
    for converting DataFrames to list of dicts.
    """
    # Create a DataFrame with 1000 rows and multiple columns
    base_date = date(2020, 1, 1)
    data = {
        "date": [base_date + timedelta(days=i) for i in range(1000)],
        "rsi_14": [float(i % 100) for i in range(1000)],
        "sma_20": [100.0 + i * 0.1 for i in range(1000)],
        "macd": [i * 0.01 for i in range(1000)],
        "bb_upper": [105.0 + i * 0.1 for i in range(1000)],
        "bb_lower": [95.0 + i * 0.1 for i in range(1000)],
    }
    df = pd.DataFrame(data)

    # Method 1: iterrows() (current implementation pattern)
    start = time.time()
    results_iterrows = []
    for _, row in df.iterrows():
        out = {"date": row["date"], "source": "test"}
        for col in ["rsi_14", "sma_20", "macd", "bb_upper", "bb_lower"]:
            if col in row:
                val = row[col]
                if pd.notna(val):
                    out[col] = float(val)
        if len(out) > 2:
            results_iterrows.append(out)
    time_iterrows = time.time() - start

    # Method 2: to_dict('records') (optimized approach)
    start = time.time()
    # Add source column
    df_copy = df.copy()
    df_copy["source"] = "test"

    # Convert to records, filtering out NaN values
    results_to_dict = df_copy.to_dict("records")

    # Clean up NaN values
    cleaned_results = []
    for record in results_to_dict:
        cleaned = {k: v for k, v in record.items() if pd.notna(v) or k in ("date", "source")}
        if len(cleaned) > 2:  # More than just date and source
            # Convert numeric values to float
            for k in list(cleaned.keys()):
                if k not in ("date", "source"):
                    cleaned[k] = float(cleaned[k])
            cleaned_results.append(cleaned)
    time_to_dict = time.time() - start

    # Both should produce the same number of results
    assert len(results_iterrows) == len(cleaned_results)

    print(f"\niterrows() time: {time_iterrows:.4f}s")
    print(f"to_dict('records') time: {time_to_dict:.4f}s")
    print(f"Speed-up: {time_iterrows / time_to_dict:.1f}x")

    # to_dict should be significantly faster (at least 3x)
    # Using 3x threshold to avoid flaky failures from timing variance
    assert time_to_dict < time_iterrows / 3, \
        f"to_dict should be much faster than iterrows (got {time_iterrows / time_to_dict:.1f}x)"


if __name__ == "__main__":
    test_iterrows_vs_to_dict_performance()
