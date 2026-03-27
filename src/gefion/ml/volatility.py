"""
Volatility computation and adaptive thresholds.

Computes per-stock volatility metrics and derives adaptive thresholds
that replace static percentages (2%/5%/10%) with stock-specific values.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np
import pandas as pd


def calculate_historical_volatility(
    returns: pd.Series,
    window: int = 60,
    annualize: bool = True
) -> Optional[float]:
    """
    Calculate historical volatility as rolling std dev of returns.

    Args:
        returns: Series of daily returns
        window: Rolling window in days (default 60 = ~3 months)
        annualize: If True, multiply by sqrt(252)

    Returns:
        Annualized volatility (e.g., 0.25 for 25%), or None if insufficient data
    """
    if len(returns) < window:
        return None

    std = returns.rolling(window=window).std().iloc[-1]

    if pd.isna(std):
        return None

    if annualize:
        std *= math.sqrt(252)

    return float(std)


def calculate_bb_width(
    bb_upper: float,
    bb_lower: float,
    bb_middle: float
) -> Optional[float]:
    """
    Calculate Bollinger Band width as normalized spread.

    Args:
        bb_upper: Upper Bollinger Band value
        bb_lower: Lower Bollinger Band value
        bb_middle: Middle Bollinger Band value (typically 20-day SMA)

    Returns:
        Band width as fraction of price (e.g., 0.08 for 8%), or None if invalid
    """
    if bb_middle <= 0:
        return None

    return (bb_upper - bb_lower) / bb_middle


def compute_adaptive_thresholds(
    volatility: float,
    horizon_days: int,
    volatility_percentile: float = 0.5,
    weak_sigma: float = 0.5,
    strong_sigma: float = 1.5
) -> Tuple[float, float]:
    """
    Compute per-stock thresholds scaled by volatility and horizon.

    Uses sqrt(T) scaling based on diffusion model of price movements.
    Adjusts for extreme volatility stocks at market tails.

    Args:
        volatility: Annualized volatility (e.g., 0.30 for 30%)
        horizon_days: Prediction horizon (7, 30, 90)
        volatility_percentile: Stock's rank in market volatility (0-1)
        weak_sigma: Multiplier for weak threshold (default 0.5)
        strong_sigma: Multiplier for strong threshold (default 1.5)

    Returns:
        (weak_threshold, strong_threshold) in return terms

    Example:
        For AAPL with 25% annual vol, 7-day horizon:
        - horizon_vol = 0.25 * sqrt(7/252) = 0.0417
        - weak = 0.0417 * 0.5 = 0.021 (2.1%)
        - strong = 0.0417 * 1.5 = 0.063 (6.3%)
    """
    # Scale volatility to horizon using sqrt(T) diffusion
    horizon_vol = volatility * math.sqrt(horizon_days / 252)

    # Base thresholds
    weak = horizon_vol * weak_sigma
    strong = horizon_vol * strong_sigma

    # Adjust for extreme volatility stocks
    if volatility_percentile > 0.9:
        weak *= 1.2
        strong *= 1.2
    elif volatility_percentile < 0.1:
        weak *= 0.8
        strong *= 0.8

    return weak, strong


def compute_volatility_percentile(
    stock_volatility: float,
    all_volatilities: pd.Series
) -> float:
    """
    Compute where this stock ranks in market volatility distribution.

    Args:
        stock_volatility: This stock's volatility
        all_volatilities: Series of volatilities for all stocks in universe

    Returns:
        Percentile (0-1), where 1 = most volatile
    """
    return float((all_volatilities < stock_volatility).mean())
