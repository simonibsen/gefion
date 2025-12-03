from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    rsi = pd.to_numeric(rsi, errors="coerce").fillna(100.0)
    return rsi


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _psar(high: pd.Series, low: pd.Series, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    """
    Simple Parabolic SAR implementation.
    """
    if high.isna().all() or low.isna().all():
        return pd.Series(index=high.index, dtype=float)
    psar = [low.iloc[0]]
    ep = high.iloc[0]
    af = step
    long = True
    for i in range(1, len(high)):
        prev_psar = psar[-1]
        if long:
            psar_val = prev_psar + af * (ep - prev_psar)
            psar_val = min(psar_val, low.iloc[i - 1:i + 1].min())
            if high.iloc[i] > ep:
                ep = high.iloc[i]
                af = min(af + step, max_step)
            if low.iloc[i] < psar_val:
                long = False
                psar_val = ep
                ep = low.iloc[i]
                af = step
        else:
            psar_val = prev_psar + af * (ep - prev_psar)
            psar_val = max(psar_val, high.iloc[i - 1:i + 1].max())
            if low.iloc[i] < ep:
                ep = low.iloc[i]
                af = min(af + step, max_step)
            if high.iloc[i] > psar_val:
                long = True
                psar_val = ep
                ep = high.iloc[i]
                af = step
        psar.append(psar_val)
    return pd.Series(psar, index=high.index, dtype=float)


def compute_indicators(
    price_rows: Iterable[Mapping[str, object]],
    indicators: Iterable[str],
    return_failures: bool = False,
) -> List[Dict[str, object]] | tuple[List[Dict[str, object]], List[tuple[str, str]]]:
    df = pd.DataFrame(price_rows)
    if df.empty:
        return ([], []) if return_failures else []
    df = df.sort_values("date").copy()
    for col in ["open", "high", "low", "close", "adjusted_close", "volume"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close", "adjusted_close"], how="all")
    if df.empty:
        return ([], []) if return_failures else []
    close = df["adjusted_close"].fillna(df["close"])
    high = df.get("high")
    low = df.get("low")

    # Track which features failed during computation with error messages
    failed_features: List[tuple[str, str]] = []

    # Define computation functions for each indicator
    def compute_rsi():
        df["rsi_14"] = _rsi(close, 14)

    def compute_macd():
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        df["ema_12"] = ema12
        df["ema_26"] = ema26
        df["macd"] = ema12 - ema26
        df["macd_signal"] = _ema(df["macd"], 9)
        df["macd_hist"] = df["macd"] - df["macd_signal"]

    def compute_bbands():
        ma = _sma(close, 20)
        std = close.rolling(window=20, min_periods=20).std()
        df["bb_middle"] = ma
        df["bb_upper"] = ma + (2 * std)
        df["bb_lower"] = ma - (2 * std)

    def compute_adx():
        if high is None or low is None:
            return
        # Check if high/low have enough valid numeric data (need 14+ for rolling)
        if high.isna().all() or low.isna().all():
            return
        if high.notna().sum() < 14 or low.notna().sum() < 14:
            return
        # Check for variation - flat/frozen prices can't compute ADX
        # If all highs are the same or all lows are the same, skip
        if high.nunique() <= 1 or low.nunique() <= 1:
            return
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        tr = (high.combine(low, max) - low.combine(high, min)).fillna(0)
        tr_sum = tr.rolling(14, min_periods=14).sum()
        tr_sum = tr_sum.replace(0, pd.NA)
        plus_di = 100 * (plus_dm.rolling(14, min_periods=14).sum() / tr_sum)
        minus_di = 100 * (minus_dm.rolling(14, min_periods=14).sum() / tr_sum)
        denom = (plus_di + minus_di).replace(0, pd.NA)
        dx = (abs(plus_di - minus_di) / denom).abs() * 100
        df["adx_14"] = dx.rolling(14, min_periods=14).mean()

    def compute_stoch():
        if high is None or low is None:
            return
        # Check if high/low have enough valid numeric data (need 14+ for rolling)
        if high.isna().all() or low.isna().all():
            return
        if high.notna().sum() < 14 or low.notna().sum() < 14:
            return
        # Check for variation - flat/frozen prices can't compute stochastic
        # If all highs are the same or all lows are the same, skip
        if high.nunique() <= 1 or low.nunique() <= 1:
            return
        lowest_low = low.rolling(14, min_periods=14).min()
        highest_high = high.rolling(14, min_periods=14).max()
        denom = (highest_high - lowest_low).replace(0, pd.NA)
        stoch_k = ((close - lowest_low) / denom) * 100
        df["stoch_k"] = stoch_k
        df["stoch_d"] = stoch_k.rolling(3, min_periods=3).mean()

    def compute_sma20():
        df["sma_20"] = _sma(close, 20)

    def compute_sma50():
        df["sma_50"] = _sma(close, 50)

    def compute_sma200():
        df["sma_200"] = _sma(close, 200)

    def compute_ema12():
        df["ema_12"] = _ema(close, 12)

    def compute_ema26():
        df["ema_26"] = _ema(close, 26)

    def compute_psar():
        if high is None or low is None:
            return
        # Check if high/low have enough valid numeric data
        if high.isna().all() or low.isna().all():
            return
        if high.notna().sum() < 2 or low.notna().sum() < 2:
            return
        df["psar"] = _psar(high.ffill(), low.ffill())

    # Dispatch table mapping indicator names to computation functions
    indicator_dispatch = {
        "rsi": compute_rsi,
        "macd": compute_macd,
        "bbands": compute_bbands,
        "adx": compute_adx,
        "stoch": compute_stoch,
        "sma20": compute_sma20,
        "sma50": compute_sma50,
        "sma200": compute_sma200,
        "ema12": compute_ema12,
        "ema26": compute_ema26,
        "psar": compute_psar,
    }

    # Compute requested indicators with individual error handling
    # This prevents one failing indicator from breaking all others
    for indicator in indicators:
        if indicator in indicator_dispatch:
            try:
                indicator_dispatch[indicator]()
            except Exception as e:
                error_msg = str(e)
                logger.debug(f"Failed to compute feature '{indicator}': {error_msg}")
                failed_features.append((indicator, error_msg))
                # Skip failed feature, continue with others

    # Add source column before conversion
    df["source"] = "local"

    # Use to_dict('records') for 5-10x better performance than iterrows()
    records = df.to_dict("records")

    # Filter out NaN values and convert to proper types
    indicator_cols = [
        "rsi_14", "adx_14", "sma_20", "sma_50", "sma_200",
        "ema_12", "ema_26", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_middle", "bb_lower", "stoch_k", "stoch_d", "psar"
    ]

    results: List[Dict[str, object]] = []
    for record in records:
        out: Dict[str, object] = {"date": record["date"], "source": "local"}
        has_indicators = False

        for col in indicator_cols:
            if col in record:
                val = record[col]
                if pd.notna(val):
                    out[col] = float(val)
                    has_indicators = True

        if has_indicators:
            results.append(out)

    if return_failures:
        return results, failed_features
    return results
