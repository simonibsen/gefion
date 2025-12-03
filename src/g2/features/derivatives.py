"""
Time series derivative features for ML.

Computes slope (first derivative) and concavity (second derivative)
for technical indicators to capture trend and acceleration patterns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List, Optional, Dict, Any, Tuple


def add_derivative_features(
    df: pd.DataFrame,
    columns: List[str],
    slope_window: int = 5,
    concavity_window: int = 5,
) -> pd.DataFrame:
    """
    Add slope and concavity features for specified columns.

    For each column (e.g., 'rsi_14'), adds:
    - '{column}_slope_{window}': Rate of change over window periods
    - '{column}_concavity_{window}': Acceleration over window periods

    Args:
        df: DataFrame with indicator columns
        columns: Which columns to compute derivatives for
        slope_window: Lookback period for slope calculation
        concavity_window: Lookback period for concavity calculation

    Returns:
        DataFrame with additional derivative columns

    Example:
        >>> df = pd.DataFrame({'rsi_14': [50, 52, 55, 59, 64]})
        >>> df = add_derivative_features(df, ['rsi_14'], slope_window=3)
        >>> df.columns
        Index(['rsi_14', 'rsi_14_slope_3', 'rsi_14_concavity_3'])
    """
    df = df.copy()

    for col in columns:
        if col not in df.columns:
            continue

        # Slope (first derivative) - measures trend strength/direction
        slope_col = f"{col}_slope_{slope_window}"
        df[slope_col] = compute_slope(df[col], slope_window)

        # Concavity (second derivative) - measures acceleration/deceleration
        concavity_col = f"{col}_concavity_{concavity_window}"
        df[concavity_col] = compute_concavity(df[col], concavity_window)

    return df


def compute_slope(series: pd.Series, window: int, method: str = "linreg") -> pd.Series:
    """
    Compute slope (first derivative) over rolling window.

    Methods:
        - 'linreg': Linear regression slope (default, smoother)
        - 'diff': Simple difference (faster, noisier)

    Args:
        series: Time series data
        window: Rolling window size
        method: Computation method

    Returns:
        Series of slopes

    Example:
        >>> s = pd.Series([1, 2, 4, 7, 11])
        >>> compute_slope(s, window=3, method='linreg')
        # Returns slopes: [NaN, NaN, 1.5, 2.5, 3.5]
    """
    if method == "diff":
        # Simple difference (faster but noisier)
        return series.diff(window) / window

    # Linear regression slope (smoother, better for ML)
    def rolling_slope(values):
        if len(values) < 2 or np.isnan(values).any():
            return np.nan
        x = np.arange(len(values))
        # Linear fit: y = mx + b, return m
        slope, _ = np.polyfit(x, values, 1)
        return slope

    return series.rolling(window, min_periods=window).apply(rolling_slope, raw=True)


def compute_concavity(series: pd.Series, window: int, method: str = "quadratic") -> pd.Series:
    """
    Compute concavity (second derivative / curvature).

    Positive concavity = accelerating upward
    Negative concavity = decelerating / accelerating downward

    Methods:
        - 'quadratic': Quadratic polynomial fit (default, smoother)
        - 'diff': Double difference (faster, noisier)

    Args:
        series: Time series data
        window: Rolling window size
        method: Computation method

    Returns:
        Series of concavity values

    Example:
        >>> s = pd.Series([1, 2, 4, 7, 11, 16])  # Accelerating
        >>> compute_concavity(s, window=4, method='quadratic')
        # Returns positive values (upward acceleration)
    """
    if method == "diff":
        # Double difference (faster but noisier)
        return series.diff().diff().rolling(window).mean()

    # Quadratic fit: y = ax² + bx + c, return 2a (curvature)
    def rolling_curvature(values):
        if len(values) < 3 or np.isnan(values).any():
            return np.nan
        x = np.arange(len(values))
        # Fit quadratic polynomial
        coeffs = np.polyfit(x, values, 2)
        # Second derivative of ax² + bx + c is 2a
        return 2 * coeffs[0]

    return series.rolling(window, min_periods=window).apply(rolling_curvature, raw=True)


def compute_rate_of_change(series: pd.Series, period: int = 1) -> pd.Series:
    """
    Compute percentage rate of change.

    Args:
        series: Time series data
        period: Number of periods to look back

    Returns:
        Series of percentage changes

    Example:
        >>> s = pd.Series([100, 105, 110])
        >>> compute_rate_of_change(s, period=1)
        # Returns: [NaN, 5.0, 4.76...]  (percentage changes)
    """
    return series.pct_change(period) * 100


def compute_momentum(series: pd.Series, period: int = 10) -> pd.Series:
    """
    Compute momentum (current value - value N periods ago).

    Args:
        series: Time series data
        period: Lookback period

    Returns:
        Series of momentum values
    """
    return series - series.shift(period)


def compute_derivatives(
    source_rows: List[Dict[str, Any]],
    derivative_specs: List[Dict[str, Any]],
    return_failures: bool = False,
) -> List[Dict[str, Any]] | Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """
    Compute derivative features from source data.

    Pure function following the compute_indicators pattern.
    Takes source data + specifications, returns computed derivatives.

    Args:
        source_rows: List of dicts with 'date' and 'value' keys
        derivative_specs: List of derivative specifications, each with:
            - name: Feature name (e.g., 'rsi_14_slope_5')
            - type: 'slope' or 'concavity'
            - window: Rolling window size
            - method: Optional computation method ('linreg', 'diff', 'quadratic')
        return_failures: If True, return (results, failures) tuple

    Returns:
        List of dicts with date and computed derivative values,
        or (results, failures) tuple if return_failures=True

    Example:
        >>> source_rows = [
        ...     {'date': date(2024, 1, 1), 'value': 50.0},
        ...     {'date': date(2024, 1, 2), 'value': 52.0},
        ...     # ...
        ... ]
        >>> specs = [
        ...     {'name': 'rsi_slope_5', 'type': 'slope', 'window': 5},
        ...     {'name': 'rsi_concavity_5', 'type': 'concavity', 'window': 5}
        ... ]
        >>> results = compute_derivatives(source_rows, specs)
    """
    if not source_rows:
        if return_failures:
            return [], []
        return []

    # Convert to DataFrame for easier computation
    df = pd.DataFrame(source_rows)

    if 'date' not in df.columns or 'value' not in df.columns:
        if return_failures:
            return [], [('all', 'Missing required columns: date, value')]
        return []

    # Ensure date is sorted
    df = df.sort_values('date').reset_index(drop=True)

    failures = []

    # Compute each derivative
    for spec in derivative_specs:
        name = spec.get('name')
        deriv_type = spec.get('type')
        window = spec.get('window', 5)
        method = spec.get('method')

        try:
            if deriv_type == 'slope':
                method = method or 'linreg'
                df[name] = compute_slope(df['value'], window, method=method)

            elif deriv_type == 'concavity':
                method = method or 'quadratic'
                df[name] = compute_concavity(df['value'], window, method=method)

            else:
                failures.append((name, f'Unknown derivative type: {deriv_type}'))

        except Exception as exc:
            failures.append((name, str(exc)))

    # Convert back to list of dicts
    result_columns = ['date'] + [spec['name'] for spec in derivative_specs if spec['name'] in df.columns]

    results = []
    for _, row in df.iterrows():
        row_dict = {'date': row['date']}

        # Only include non-null values
        for col in result_columns[1:]:  # Skip 'date'
            if col in row and pd.notna(row[col]):
                row_dict[col] = float(row[col])

        results.append(row_dict)

    if return_failures:
        return results, failures

    return results
