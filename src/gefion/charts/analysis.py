"""
Analysis functions for computing chart insights.

Provides rich context for MCP tools by computing summaries and insights
from chart data that can be returned alongside the chart file path.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional
import statistics


def _compute_sma(closes: List[float], period: int) -> Optional[float]:
    """Compute simple moving average for given period."""
    if len(closes) >= period:
        return sum(closes[-period:]) / period
    return None


def _compute_returns(closes: List[float]) -> Dict[str, Optional[float]]:
    """Compute returns over various periods."""
    if not closes:
        return {}

    current = closes[-1]
    returns = {}

    # 1-day return
    if len(closes) >= 2:
        returns["1d"] = round(((current - closes[-2]) / closes[-2]) * 100, 2)

    # 5-day return
    if len(closes) >= 6:
        returns["5d"] = round(((current - closes[-6]) / closes[-6]) * 100, 2)

    # 1-month (~21 trading days)
    if len(closes) >= 22:
        returns["1mo"] = round(((current - closes[-22]) / closes[-22]) * 100, 2)

    # 3-month (~63 trading days)
    if len(closes) >= 64:
        returns["3mo"] = round(((current - closes[-64]) / closes[-64]) * 100, 2)

    # Period return (full data)
    if len(closes) >= 2:
        returns["period"] = round(((current - closes[0]) / closes[0]) * 100, 2)

    return returns


def _compute_volatility(closes: List[float], period: int = 20) -> Optional[float]:
    """Compute annualized volatility from daily returns."""
    if len(closes) < period + 1:
        return None

    # Daily returns for the period
    daily_returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(-period, 0)
    ]

    if len(daily_returns) < 2:
        return None

    std_dev = statistics.stdev(daily_returns)
    # Annualize (assuming 252 trading days)
    annualized = std_dev * (252 ** 0.5) * 100
    return round(annualized, 1)


def _get_ma_position(closes: List[float]) -> Dict[str, Any]:
    """Determine price position relative to moving averages."""
    if not closes:
        return {}

    current = closes[-1]
    position = {}

    sma_20 = _compute_sma(closes, 20)
    sma_50 = _compute_sma(closes, 50)
    sma_200 = _compute_sma(closes, 200)

    if sma_20:
        position["sma_20"] = round(sma_20, 2)
        position["above_sma_20"] = current > sma_20

    if sma_50:
        position["sma_50"] = round(sma_50, 2)
        position["above_sma_50"] = current > sma_50

    if sma_200:
        position["sma_200"] = round(sma_200, 2)
        position["above_sma_200"] = current > sma_200

    return position


def _analyze_trend_structure(ohlcv_data: List[Dict[str, Any]]) -> List[str]:
    """Analyze price trend structure (higher highs/lows, etc.)."""
    if len(ohlcv_data) < 20:
        return []

    insights = []
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]

    # Find swing highs and lows (local peaks/troughs over 5-day windows)
    swing_highs = []
    swing_lows = []
    for i in range(5, len(highs) - 5):
        # Swing high: higher than 5 bars before and after
        if highs[i] == max(highs[i-5:i+6]):
            swing_highs.append((i, highs[i]))
        # Swing low: lower than 5 bars before and after
        if lows[i] == min(lows[i-5:i+6]):
            swing_lows.append((i, lows[i]))

    # Analyze trend structure from recent swings
    if len(swing_highs) >= 2:
        recent_highs = swing_highs[-3:] if len(swing_highs) >= 3 else swing_highs[-2:]
        if all(recent_highs[i][1] > recent_highs[i-1][1] for i in range(1, len(recent_highs))):
            insights.append("Higher highs pattern: uptrend structure intact")
        elif all(recent_highs[i][1] < recent_highs[i-1][1] for i in range(1, len(recent_highs))):
            insights.append("Lower highs pattern: potential downtrend forming")

    if len(swing_lows) >= 2:
        recent_lows = swing_lows[-3:] if len(swing_lows) >= 3 else swing_lows[-2:]
        if all(recent_lows[i][1] > recent_lows[i-1][1] for i in range(1, len(recent_lows))):
            insights.append("Higher lows pattern: buyers defending higher levels")
        elif all(recent_lows[i][1] < recent_lows[i-1][1] for i in range(1, len(recent_lows))):
            insights.append("Lower lows pattern: sellers in control")

    return insights


def _find_support_resistance(ohlcv_data: List[Dict[str, Any]]) -> List[str]:
    """Identify key support and resistance levels."""
    if len(ohlcv_data) < 20:
        return []

    insights = []
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]
    current_price = closes[-1]

    # Find price clusters (areas where price has reversed multiple times)
    all_prices = highs + lows
    price_min, price_max = min(all_prices), max(all_prices)
    price_range = price_max - price_min

    if price_range == 0:
        return []

    # Create price buckets (divide range into 20 buckets)
    n_buckets = 20
    bucket_size = price_range / n_buckets
    buckets = [0] * n_buckets

    for h in highs:
        bucket = min(int((h - price_min) / bucket_size), n_buckets - 1)
        buckets[bucket] += 1
    for l in lows:
        bucket = min(int((l - price_min) / bucket_size), n_buckets - 1)
        buckets[bucket] += 1

    # Find significant levels (buckets with high frequency)
    avg_count = sum(buckets) / n_buckets
    significant_levels = []
    for i, count in enumerate(buckets):
        if count > avg_count * 1.5:
            level_price = price_min + (i + 0.5) * bucket_size
            significant_levels.append(level_price)

    # Identify nearest support and resistance
    supports = [l for l in significant_levels if l < current_price]
    resistances = [l for l in significant_levels if l > current_price]

    if supports:
        nearest_support = max(supports)
        distance_pct = (current_price - nearest_support) / current_price * 100
        insights.append(f"Support level: ${nearest_support:.2f} ({distance_pct:.1f}% below)")

    if resistances:
        nearest_resistance = min(resistances)
        distance_pct = (nearest_resistance - current_price) / current_price * 100
        insights.append(f"Resistance level: ${nearest_resistance:.2f} ({distance_pct:.1f}% above)")

    return insights


def _predict_crossovers(ohlcv_data: List[Dict[str, Any]]) -> List[str]:
    """Predict upcoming moving average crossovers."""
    if len(ohlcv_data) < 50:
        return []

    insights = []
    closes = [row["close"] for row in ohlcv_data]

    # Calculate current SMAs
    sma_20 = sum(closes[-20:]) / 20
    sma_50 = sum(closes[-50:]) / 50

    # Calculate SMAs from 5 days ago to measure rate of change
    if len(closes) >= 55:
        sma_20_5d_ago = sum(closes[-25:-5]) / 20
        sma_50_5d_ago = sum(closes[-55:-5]) / 50

        gap_now = sma_20 - sma_50
        gap_5d_ago = sma_20_5d_ago - sma_50_5d_ago
        gap_change_per_day = (gap_now - gap_5d_ago) / 5

        # Predict when gap will reach zero
        if gap_change_per_day != 0:
            days_to_cross = -gap_now / gap_change_per_day

            if 0 < days_to_cross < 30:
                cross_type = "Death Cross" if gap_now > 0 else "Golden Cross"
                sentiment = "bearish" if gap_now > 0 else "bullish"
                insights.append(f"⚠️ {cross_type} approaching in ~{int(days_to_cross)} trading days ({sentiment})")
            elif -5 < days_to_cross <= 0:
                # Just crossed
                cross_type = "Death Cross" if gap_now < 0 else "Golden Cross"
                insights.append(f"Recent {cross_type}: SMAs just crossed")

        # Report current gap status
        if abs(gap_now) < 5:
            insights.append(f"SMA20/50 gap narrowing: ${abs(gap_now):.2f} apart")

    # Check 200 SMA if available
    if len(closes) >= 200:
        sma_200 = sum(closes[-200:]) / 200
        gap_to_200 = closes[-1] - sma_200
        pct_from_200 = (gap_to_200 / sma_200) * 100

        if abs(pct_from_200) < 3:
            if gap_to_200 > 0:
                insights.append(f"Price testing 200-day MA from above (${sma_200:.2f})")
            else:
                insights.append(f"Price testing 200-day MA from below (${sma_200:.2f})")

    return insights


def _detect_price_patterns(ohlcv_data: List[Dict[str, Any]]) -> List[str]:
    """Detect common price patterns."""
    if len(ohlcv_data) < 30:
        return []

    insights = []
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]

    # Look for double top/bottom in last 30 bars
    recent_highs = highs[-30:]
    recent_lows = lows[-30:]

    # Find the two highest highs
    sorted_high_indices = sorted(range(len(recent_highs)), key=lambda i: recent_highs[i], reverse=True)
    if len(sorted_high_indices) >= 2:
        idx1, idx2 = sorted_high_indices[0], sorted_high_indices[1]
        high1, high2 = recent_highs[idx1], recent_highs[idx2]

        # Double top: two highs within 2% of each other, separated by at least 5 bars
        if abs(idx1 - idx2) >= 5 and abs(high1 - high2) / high1 < 0.02:
            # Check if there's a valley between them
            valley_start, valley_end = min(idx1, idx2), max(idx1, idx2)
            valley_low = min(recent_lows[valley_start:valley_end+1])
            if (high1 - valley_low) / high1 > 0.03:  # At least 3% dip between peaks
                current_vs_peaks = (closes[-1] - high1) / high1 * 100
                if current_vs_peaks < -2:
                    insights.append(f"Potential double top pattern near ${high1:.2f} (bearish)")

    # Find the two lowest lows
    sorted_low_indices = sorted(range(len(recent_lows)), key=lambda i: recent_lows[i])
    if len(sorted_low_indices) >= 2:
        idx1, idx2 = sorted_low_indices[0], sorted_low_indices[1]
        low1, low2 = recent_lows[idx1], recent_lows[idx2]

        # Double bottom: two lows within 2% of each other, separated by at least 5 bars
        if abs(idx1 - idx2) >= 5 and abs(low1 - low2) / low1 < 0.02:
            # Check if there's a peak between them
            peak_start, peak_end = min(idx1, idx2), max(idx1, idx2)
            peak_high = max(recent_highs[peak_start:peak_end+1])
            if (peak_high - low1) / low1 > 0.03:  # At least 3% rally between troughs
                current_vs_lows = (closes[-1] - low1) / low1 * 100
                if current_vs_lows > 2:
                    insights.append(f"Potential double bottom pattern near ${low1:.2f} (bullish)")

    # Consolidation/range-bound detection
    recent_range = max(recent_highs) - min(recent_lows)
    recent_avg = sum(closes[-20:]) / 20
    range_pct = (recent_range / recent_avg) * 100

    if range_pct < 8:
        insights.append(f"Tight consolidation: {range_pct:.1f}% range (breakout watch)")

    return insights


def _detect_notable_events(ohlcv_data: List[Dict[str, Any]]) -> List[str]:
    """Detect notable events in price/volume data for insights."""
    if len(ohlcv_data) < 10:
        return []

    events = []
    closes = [row["close"] for row in ohlcv_data]
    volumes = [row["volume"] for row in ohlcv_data]
    dates = [row["date"] for row in ohlcv_data]

    avg_volume = sum(volumes) / len(volumes)

    # Recent volume spikes (last 10 days)
    recent_spikes = []
    for i in range(-min(10, len(volumes)), 0):
        if volumes[i] > avg_volume * 2.0:
            daily_change = (closes[i] - closes[i-1]) / closes[i-1] * 100 if i > -len(closes) else 0
            recent_spikes.append((dates[i], volumes[i], daily_change))

    if recent_spikes:
        spike = recent_spikes[-1]  # Most recent
        direction = "up" if spike[2] >= 0 else "down"
        events.append(f"Volume spike on {spike[0]}: {spike[1]/1e6:.1f}M ({spike[1]/avg_volume:.1f}x avg) with {spike[2]:+.1f}% move")

    # Recent big moves (last 10 days)
    for i in range(-min(10, len(closes)), 0):
        if i > -len(closes):
            daily_change = (closes[i] - closes[i-1]) / closes[i-1] * 100
            if abs(daily_change) > 3.0:
                events.append(f"Significant move on {dates[i]}: {daily_change:+.1f}%")

    # MA crossovers (if we have enough data)
    if len(closes) >= 50:
        # Check last 10 days for crossovers
        for i in range(-min(10, len(closes) - 50), 0):
            idx = len(closes) + i
            if idx >= 50:
                sma_20_prev = sum(closes[idx-20:idx]) / 20
                sma_50_prev = sum(closes[idx-50:idx]) / 50
                sma_20_curr = sum(closes[idx-19:idx+1]) / 20
                sma_50_curr = sum(closes[idx-49:idx+1]) / 50

                if sma_20_prev < sma_50_prev and sma_20_curr >= sma_50_curr:
                    events.append(f"Golden Cross on {dates[idx]}: SMA20 crossed above SMA50 (bullish)")
                elif sma_20_prev > sma_50_prev and sma_20_curr <= sma_50_curr:
                    events.append(f"Death Cross on {dates[idx]}: SMA20 crossed below SMA50 (bearish)")

    return events


def compute_price_insights(
    ohlcv_data: List[Dict[str, Any]],
    features: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Compute comprehensive price summary and insights for MCP context.

    Args:
        ohlcv_data: Historical OHLCV data
        features: Optional feature data for additional insights

    Returns:
        Dict with:
            - description: Human-readable chart description
            - price_change: Percentage change over period
            - price_range: Dict with low, high, current
            - returns: Dict with 1d, 5d, 1mo, 3mo returns
            - moving_averages: Dict with SMA values and position
            - volatility: Annualized volatility percentage
            - volume_trend: Description of volume trend
            - insights: List of insight strings
    """
    if not ohlcv_data:
        return {
            "description": "No price data available",
            "price_change": 0.0,
            "price_range": {"low": 0, "high": 0, "current": 0},
            "returns": {},
            "moving_averages": {},
            "volatility": None,
            "volume_trend": "No data",
            "insights": [],
        }

    # Extract values
    closes = [row["close"] for row in ohlcv_data]
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]
    volumes = [row["volume"] for row in ohlcv_data]

    first_close = closes[0]
    last_close = closes[-1]
    price_change = ((last_close - first_close) / first_close) * 100

    period_high = max(highs)
    period_low = min(lows)

    # Compute returns
    returns = _compute_returns(closes)

    # Compute moving averages position
    ma_position = _get_ma_position(closes)

    # Compute volatility
    volatility = _compute_volatility(closes)

    # Volume trend (compare recent vs older)
    mid = len(volumes) // 2
    avg_volume = sum(volumes) / len(volumes) if volumes else 0
    if mid > 0:
        old_avg = sum(volumes[:mid]) / mid
        new_avg = sum(volumes[mid:]) / (len(volumes) - mid)
        volume_change = ((new_avg - old_avg) / old_avg) * 100 if old_avg > 0 else 0
        if volume_change > 10:
            volume_trend = f"Volume increased {volume_change:.1f}% in recent period"
        elif volume_change < -10:
            volume_trend = f"Volume decreased {abs(volume_change):.1f}% in recent period"
        else:
            volume_trend = "Volume stable"
    else:
        volume_trend = "Insufficient data for volume trend"

    # Generate insights
    insights = []

    # Price trend insight
    if price_change > 10:
        insights.append(f"Strong uptrend: +{price_change:.1f}% over the period")
    elif price_change > 5:
        insights.append(f"Moderate uptrend: +{price_change:.1f}% over the period")
    elif price_change < -10:
        insights.append(f"Strong downtrend: {price_change:.1f}% over the period")
    elif price_change < -5:
        insights.append(f"Moderate downtrend: {price_change:.1f}% over the period")
    else:
        insights.append(f"Consolidating: {price_change:+.1f}% over the period")

    # Recent momentum (5-day)
    if returns.get("5d") is not None:
        r5d = returns["5d"]
        if r5d > 5:
            insights.append(f"Strong recent momentum: +{r5d:.1f}% in 5 days")
        elif r5d < -5:
            insights.append(f"Weak recent momentum: {r5d:.1f}% in 5 days")

    # Moving average insights
    if ma_position.get("above_sma_20") is not None:
        if ma_position.get("above_sma_20") and ma_position.get("above_sma_50"):
            insights.append("Price above 20 & 50-day moving averages (bullish)")
        elif not ma_position.get("above_sma_20") and not ma_position.get("above_sma_50", True):
            insights.append("Price below 20 & 50-day moving averages (bearish)")

    if ma_position.get("above_sma_200") is not None:
        if ma_position["above_sma_200"]:
            insights.append("Price above 200-day MA (long-term uptrend)")
        else:
            insights.append("Price below 200-day MA (long-term downtrend)")

    # Volatility insight
    if volatility is not None:
        if volatility > 50:
            insights.append(f"High volatility: {volatility:.0f}% annualized")
        elif volatility > 30:
            insights.append(f"Elevated volatility: {volatility:.0f}% annualized")
        elif volatility < 15:
            insights.append(f"Low volatility: {volatility:.0f}% annualized")

    # 52-week high/low approximation (if we have enough data)
    if len(closes) > 200:
        high_52w = max(highs[-252:]) if len(highs) >= 252 else period_high
        low_52w = min(lows[-252:]) if len(lows) >= 252 else period_low
        if last_close >= high_52w * 0.98:
            insights.append(f"Trading near 52-week high (${high_52w:.2f})")
        elif last_close <= low_52w * 1.02:
            insights.append(f"Trading near 52-week low (${low_52w:.2f})")
    elif len(closes) > 20:
        if last_close >= period_high * 0.98:
            insights.append(f"At period high: ${period_high:.2f}")
        elif last_close <= period_low * 1.02:
            insights.append(f"At period low: ${period_low:.2f}")

    # Add technical insights if features provided
    if features:
        tech_signals = detect_technical_signals(ohlcv_data, features)
        insights.extend(tech_signals)

    # Technical analysis: crossover predictions (high priority)
    crossover_insights = _predict_crossovers(ohlcv_data)
    insights.extend(crossover_insights)

    # Technical analysis: support/resistance levels
    sr_insights = _find_support_resistance(ohlcv_data)
    insights.extend(sr_insights)

    # Technical analysis: trend structure
    trend_insights = _analyze_trend_structure(ohlcv_data)
    insights.extend(trend_insights)

    # Technical analysis: price patterns
    pattern_insights = _detect_price_patterns(ohlcv_data)
    insights.extend(pattern_insights)

    # Detect notable events (volume spikes, big moves, recent crossovers)
    notable_events = _detect_notable_events(ohlcv_data)
    if notable_events:
        insights.extend(notable_events[:2])  # Limit to top 2 events

    n_days = len(ohlcv_data)
    description = f"Candlestick chart with {n_days} trading days, 20/50/200 SMAs, and volume"

    return {
        "description": description,
        "price_change": round(price_change, 2),
        "price_range": {
            "low": round(period_low, 2),
            "high": round(period_high, 2),
            "current": round(last_close, 2),
        },
        "returns": returns,
        "moving_averages": ma_position,
        "volatility": volatility,
        "avg_volume": int(avg_volume) if avg_volume else 0,
        "volume_trend": volume_trend,
        "insights": insights,
    }


def compute_prediction_insights(
    predictions: List[Dict[str, Any]],
    current_price: float,
) -> Dict[str, Any]:
    """
    Compute prediction summary and insights.

    Args:
        predictions: Prediction data with q10, q50, q90
        current_price: Current stock price

    Returns:
        Dict with:
            - description: Human-readable description
            - predicted_median: Median prediction value
            - prediction_range: Dict with q10, q90
            - confidence_width: IQR as percentage
            - insights: List of insight strings
    """
    if not predictions:
        return {
            "description": "No predictions available",
            "predicted_median": current_price,
            "prediction_range": {"q10": current_price, "q90": current_price},
            "confidence_width": "0%",
            "insights": [],
        }

    # Use the most recent prediction
    pred = predictions[-1]
    q10 = pred["q10"]
    q50 = pred["q50"]
    q90 = pred["q90"]

    # Calculate implied return
    implied_return = ((q50 - current_price) / current_price) * 100

    # Confidence width (IQR as percentage of median)
    iqr = q90 - q10
    confidence_width = (iqr / q50) * 100 if q50 > 0 else 0

    insights = []

    # Direction insight
    if implied_return > 2:
        insights.append(f"Median prediction suggests {implied_return:.1f}% upside")
    elif implied_return < -2:
        insights.append(f"Median prediction suggests {abs(implied_return):.1f}% downside")
    else:
        insights.append("Median prediction suggests flat price action")

    # Confidence insight
    if confidence_width > 15:
        insights.append("Wide prediction bands indicate high uncertainty")
    elif confidence_width < 5:
        insights.append("Narrow prediction bands indicate high confidence")
    else:
        insights.append("Moderate prediction uncertainty")

    # Upside/downside targets
    insights.append(f"90th percentile target: ${q90:.2f}")
    insights.append(f"10th percentile target: ${q10:.2f}")

    description = f"Price prediction with q10/q50/q90 confidence bands"

    return {
        "description": description,
        "predicted_median": round(q50, 2),
        "prediction_range": {
            "q10": round(q10, 2),
            "q90": round(q90, 2),
        },
        "confidence_width": f"{confidence_width:.1f}% IQR",
        "insights": insights,
    }


def compute_backtest_insights(
    equity_curve: List[Dict[str, Any]],
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute backtest summary and insights.

    Args:
        equity_curve: Equity curve data
        metrics: Optional pre-computed metrics

    Returns:
        Dict with:
            - description: Human-readable description
            - total_return: Percentage return
            - max_drawdown: Maximum drawdown percentage
            - sharpe_ratio: Sharpe ratio if available
            - insights: List of insight strings
    """
    if not equity_curve:
        return {
            "description": "No backtest data available",
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": None,
            "insights": [],
        }

    # Calculate metrics from equity curve
    initial_equity = equity_curve[0]["equity"]
    final_equity = equity_curve[-1]["equity"]
    total_return = ((final_equity - initial_equity) / initial_equity) * 100

    # Max drawdown
    max_drawdown = max(row["drawdown"] for row in equity_curve) * 100

    # Find worst drawdown date
    worst_dd_row = max(equity_curve, key=lambda x: x["drawdown"])
    worst_dd_date = worst_dd_row["date"]

    # Use provided metrics if available
    sharpe = metrics.get("sharpe_ratio") if metrics else None
    win_rate = metrics.get("win_rate") if metrics else None

    insights = []

    # Return insight
    if total_return > 0:
        insights.append(f"Strategy returned {total_return:.1f}% profit")
    else:
        insights.append(f"Strategy returned {abs(total_return):.1f}% loss")

    # Drawdown insight
    if max_drawdown > 20:
        insights.append(f"Maximum drawdown of {max_drawdown:.1f}% indicates high risk")
    elif max_drawdown > 10:
        insights.append(f"Moderate maximum drawdown of {max_drawdown:.1f}%")
    else:
        insights.append(f"Low maximum drawdown of {max_drawdown:.1f}%")

    insights.append(f"Worst drawdown occurred around {worst_dd_date}")

    # Sharpe insight if available
    if sharpe is not None:
        if sharpe > 1.5:
            insights.append(f"Excellent risk-adjusted return (Sharpe: {sharpe:.2f})")
        elif sharpe > 1.0:
            insights.append(f"Good risk-adjusted return (Sharpe: {sharpe:.2f})")
        elif sharpe > 0.5:
            insights.append(f"Moderate risk-adjusted return (Sharpe: {sharpe:.2f})")
        else:
            insights.append(f"Poor risk-adjusted return (Sharpe: {sharpe:.2f})")

    # Win rate if available
    if win_rate is not None:
        insights.append(f"Win rate: {win_rate:.1f}%")

    description = f"Backtest equity curve over {len(equity_curve)} trading days"

    return {
        "description": description,
        "total_return": round(total_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe_ratio": sharpe,
        "insights": insights,
    }


def detect_technical_signals(
    ohlcv_data: List[Dict[str, Any]],
    features: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """
    Detect notable technical patterns for insights.

    Args:
        ohlcv_data: Historical OHLCV data
        features: Feature data (RSI, MACD, etc.)

    Returns:
        List of insight strings like:
            - "RSI approaching overbought territory (68)"
            - "MACD showing bullish crossover"
            - "Price near 52-week high"
    """
    signals = []

    if not features:
        return signals

    # RSI signals
    for name, data in features.items():
        if "rsi" in name.lower() and data:
            latest_rsi = data[-1]["value"]
            if latest_rsi is not None:
                if latest_rsi >= 70:
                    signals.append(f"RSI in overbought territory ({latest_rsi:.1f})")
                elif latest_rsi >= 60:
                    signals.append(f"RSI approaching overbought ({latest_rsi:.1f})")
                elif latest_rsi <= 30:
                    signals.append(f"RSI in oversold territory ({latest_rsi:.1f})")
                elif latest_rsi <= 40:
                    signals.append(f"RSI approaching oversold ({latest_rsi:.1f})")

    # MACD signals
    for name, data in features.items():
        if "macd" in name.lower() and not "signal" in name.lower() and data:
            if len(data) >= 2:
                prev_macd = data[-2]["value"]
                curr_macd = data[-1]["value"]
                if prev_macd is not None and curr_macd is not None:
                    if prev_macd < 0 and curr_macd >= 0:
                        signals.append("MACD crossed above zero (bullish)")
                    elif prev_macd > 0 and curr_macd <= 0:
                        signals.append("MACD crossed below zero (bearish)")

    # Bollinger Band signals
    for name, data in features.items():
        if "bb_upper" in name.lower() and data and ohlcv_data:
            upper = data[-1]["value"]
            close = ohlcv_data[-1]["close"]
            if upper is not None and close >= upper:
                signals.append("Price at upper Bollinger Band")

    for name, data in features.items():
        if "bb_lower" in name.lower() and data and ohlcv_data:
            lower = data[-1]["value"]
            close = ohlcv_data[-1]["close"]
            if lower is not None and close <= lower:
                signals.append("Price at lower Bollinger Band")

    return signals
