#!/usr/bin/env python3
"""
Example: Adding slope and concavity features for ML analysis.

This demonstrates how to enhance technical indicators with derivative
features to capture trend, momentum, and acceleration patterns.
"""
import pandas as pd
from datetime import date
import psycopg
from gefion.features.derivatives import add_derivative_features


def fetch_features_with_derivatives(
    db_url: str,
    symbol: str,
    start_date: date,
    end_date: date
) -> pd.DataFrame:
    """
    Fetch computed features and add derivative features.

    Returns DataFrame suitable for ML model training/inference.
    """
    with psycopg.connect(db_url) as conn:
        # Fetch stock and features
        query = """
        SELECT
            cf.date,
            s.symbol,
            cf.close,
            cf.rsi_14,
            cf.macd,
            cf.macd_signal,
            cf.bb_middle,
            cf.bb_upper,
            cf.bb_lower,
            cf.adx_14,
            cf.stoch_k,
            cf.stoch_d
        FROM computed_features cf
        JOIN stocks s ON s.id = cf.data_id
        WHERE s.symbol = %s
          AND cf.date BETWEEN %s AND %s
        ORDER BY cf.date
        """

        df = pd.read_sql(query, conn, params=(symbol, start_date, end_date))

    if df.empty:
        return df

    # Add derivative features
    indicator_cols = [
        'close', 'rsi_14', 'macd', 'bb_middle', 'adx_14', 'stoch_k'
    ]

    df = add_derivative_features(
        df,
        columns=indicator_cols,
        slope_window=5,      # 5-day trend
        concavity_window=5   # 5-day acceleration
    )

    return df


def example_usage():
    """Example: Load features and analyze derivatives."""
    db_url = "postgresql://gefion:gefionpass@localhost:5432/gefion"

    # Fetch features with derivatives
    df = fetch_features_with_derivatives(
        db_url,
        symbol="AAPL",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 1)
    )

    if df.empty:
        print("No data found. Run ingestion first.")
        return

    print("Features with derivatives:")
    print(df.columns.tolist())
    print()

    # Example 1: Detect bearish divergence
    print("=== Bearish Divergence Detection ===")
    recent = df.tail(20)

    divergence = (
        (recent['close_slope_5'] > 0) &  # Price trending up
        (recent['rsi_14_slope_5'] < 0)    # RSI trending down
    )

    if divergence.any():
        print("⚠️  Bearish divergence detected:")
        print(recent[divergence][['date', 'close', 'close_slope_5', 'rsi_14', 'rsi_14_slope_5']])
    else:
        print("✅ No bearish divergence")
    print()

    # Example 2: Detect acceleration/deceleration
    print("=== Momentum Analysis ===")
    latest = df.iloc[-1]

    print(f"Symbol: {latest['symbol']}")
    print(f"Date: {latest['date']}")
    print(f"Close: ${latest['close']:.2f}")
    print(f"Price Slope: {latest['close_slope_5']:.3f} (trend direction)")
    print(f"Price Concavity: {latest['close_concavity_5']:.3f} (acceleration)")
    print()

    # Interpret concavity
    if latest['close_concavity_5'] > 0.1:
        print("📈 Accelerating upward - bullish")
    elif latest['close_concavity_5'] < -0.1:
        print("📉 Decelerating/reversing - bearish")
    else:
        print("➡️  Steady trend - neutral")
    print()

    # Example 3: Feature summary for ML
    print("=== ML Feature Summary (last 5 days) ===")
    ml_features = [
        'date', 'close', 'close_slope_5', 'close_concavity_5',
        'rsi_14', 'rsi_14_slope_5', 'rsi_14_concavity_5',
        'macd', 'macd_slope_5', 'macd_concavity_5'
    ]

    print(df[ml_features].tail(5).to_string(index=False))
    print()

    # Example 4: Correlation analysis
    print("=== Feature Correlations ===")
    derivative_cols = [col for col in df.columns if 'slope' in col or 'concavity' in col]
    correlations = df[derivative_cols].corr()

    print("Top correlated derivative pairs:")
    # Find high correlations (but not self-correlations)
    for i, col1 in enumerate(derivative_cols):
        for col2 in derivative_cols[i+1:]:
            corr = correlations.loc[col1, col2]
            if abs(corr) > 0.7:
                print(f"  {col1} <-> {col2}: {corr:.3f}")


def example_pattern_detection():
    """Example: Detect specific patterns using derivatives."""
    db_url = "postgresql://gefion:gefionpass@localhost:5432/gefion"

    df = fetch_features_with_derivatives(
        db_url,
        symbol="AAPL",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 1)
    )

    if df.empty:
        return

    print("=== Pattern Detection ===")

    # Pattern 1: Momentum exhaustion (price rising but decelerating)
    exhaustion = (
        (df['close_slope_5'] > 0) &           # Price still rising
        (df['close_concavity_5'] < -0.05)     # But decelerating
    )

    print(f"Momentum exhaustion signals: {exhaustion.sum()}")

    # Pattern 2: Bullish reversal (price falling but starting to accelerate up)
    reversal = (
        (df['close_slope_5'] < 0) &           # Price still falling
        (df['close_concavity_5'] > 0.05)      # But accelerating upward
    )

    print(f"Bullish reversal signals: {reversal.sum()}")

    # Pattern 3: Strong trend (high slope, low concavity = sustained)
    strong_trend = (
        (df['close_slope_5'].abs() > 1.0) &   # Strong trend
        (df['close_concavity_5'].abs() < 0.05) # Sustained (low acceleration change)
    )

    print(f"Strong trend periods: {strong_trend.sum()}")


if __name__ == "__main__":
    print("Slope & Concavity Feature Examples\n")
    print("=" * 60)
    print()

    example_usage()
    print()
    print("=" * 60)
    print()
    example_pattern_detection()
