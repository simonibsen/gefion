"""
Tests for moving average crossover trading strategy.
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

from gefion.strategies.ma_crossover import MovingAverageCrossoverStrategy


def test_strategy_initialization():
    """Test strategy can be initialized with parameters."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=10,
        slow_period=50,
        position_size=0.25,
        max_positions=3,
    )

    assert strategy.fast_period == 10
    assert strategy.slow_period == 50
    assert strategy.position_size == 0.25
    assert strategy.max_positions == 3


def test_empty_price_data():
    """Test strategy handles empty price data gracefully."""
    strategy = MovingAverageCrossoverStrategy()

    signals = strategy.generate_signals(
        current_date=date(2024, 1, 1),
        portfolio={},
        price_data=[],
        initial_cash=100000.0,
    )

    assert signals == []


def test_insufficient_data_for_moving_averages():
    """Test strategy returns no signals when insufficient data for MAs."""
    strategy = MovingAverageCrossoverStrategy(fast_period=10, slow_period=50)

    # Only 40 days of data, need 50+ for slow MA
    price_data = []
    base_date = date(2024, 1, 1)
    for i in range(40):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 1000000,
        })

    signals = strategy.generate_signals(
        current_date=base_date + timedelta(days=40),
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    assert signals == []


def test_golden_cross_buy_signal():
    """Test strategy generates buy signal on golden cross (fast MA crosses above slow MA)."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=5,
        slow_period=10,
        position_size=0.3,
        max_positions=5,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Create price pattern that causes golden cross on day 51
    # Days 0-49: flat at 100 (both MAs will be ~100)
    for i in range(50):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 1000000,
        })

    # Day 50: uptrend starts (crossover happens on day 50)
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=50),
        "close": 101.0,
        "open": 100.5,
        "high": 102.0,
        "low": 100.0,
        "volume": 1000000,
    })

    current_date = base_date + timedelta(days=51)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate buy signal for golden cross
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) > 0

    aapl_signal = next((s for s in buy_signals if s["symbol"] == "AAPL"), None)
    assert aapl_signal is not None
    assert aapl_signal["shares"] > 0


def test_death_cross_sell_signal():
    """Test strategy generates sell signal on death cross (fast MA crosses below slow MA)."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=5,
        slow_period=10,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-49: high price at 120 (both MAs will be ~120)
    for i in range(50):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 120.0,
            "open": 120.0,
            "high": 121.0,
            "low": 119.0,
            "volume": 1000000,
        })

    # Day 50: downtrend starts (crossover happens on day 50)
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=50),
        "close": 119.0,
        "open": 119.5,
        "high": 121.0,
        "low": 118.0,
        "volume": 1000000,
    })

    current_date = base_date + timedelta(days=51)

    # Hold position in AAPL
    portfolio = {
        "AAPL": {"shares": 100, "avg_price": 115.0}
    }

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate sell signal for death cross
    sell_signals = [s for s in signals if s["action"] == "sell"]
    assert len(sell_signals) > 0

    aapl_signal = next((s for s in sell_signals if s["symbol"] == "AAPL"), None)
    assert aapl_signal is not None
    assert aapl_signal["shares"] == 100  # Sell entire position


def test_no_signal_when_mas_aligned():
    """Test strategy generates no signals when MAs are already aligned correctly."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=5,
        slow_period=10,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Consistent uptrend: fast MA already above slow MA, no crossover
    for i in range(20):
        close = 100.0 + (i * 1.0)  # +1 per day
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=20)

    # Already holding position (fast > slow, no new signal)
    portfolio = {
        "AAPL": {"shares": 100, "avg_price": 105.0}
    }

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # No new signals since fast already above slow and we're already in position
    assert len(signals) == 0


def test_max_positions_limit():
    """Test strategy respects max_positions limit."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=5,
        slow_period=10,
        position_size=0.3,
        max_positions=2,  # Only 2 positions allowed
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Create 3 stocks with golden cross pattern
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        # Flat then uptrend
        for i in range(50):
            price_data.append({
                "symbol": symbol,
                "date": base_date + timedelta(days=i),
                "close": 100.0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "volume": 1000000,
            })

        # Day 50: uptrend starts
        price_data.append({
            "symbol": symbol,
            "date": base_date + timedelta(days=50),
            "close": 101.0,
            "open": 100.5,
            "high": 102.0,
            "low": 100.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=51)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should only buy top 2 stocks
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) <= 2


def test_multiple_symbols_mixed_signals():
    """Test strategy handles multiple symbols with different MA crossover states."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=5,
        slow_period=10,
        position_size=0.25,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # AAPL: Golden cross (flat then uptrend)
    for i in range(50):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 1000000,
        })
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=50),
        "close": 101.0,
        "open": 100.5,
        "high": 102.0,
        "low": 100.0,
        "volume": 1000000,
    })

    # MSFT: Death cross (high then downtrend)
    for i in range(50):
        price_data.append({
            "symbol": "MSFT",
            "date": base_date + timedelta(days=i),
            "close": 120.0,
            "open": 120.0,
            "high": 121.0,
            "low": 119.0,
            "volume": 1000000,
        })
    price_data.append({
        "symbol": "MSFT",
        "date": base_date + timedelta(days=50),
        "close": 119.0,
        "open": 119.5,
        "high": 121.0,
        "low": 118.0,
        "volume": 1000000,
    })

    # GOOGL: Sideways (no clear crossover)
    for i in range(51):
        close = 100.0 + (2.0 if i % 2 == 0 else -2.0)
        price_data.append({
            "symbol": "GOOGL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=51)

    # Hold MSFT (will be sold on death cross)
    portfolio = {
        "MSFT": {"shares": 100, "avg_price": 115.0}
    }

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should buy AAPL (golden cross) and sell MSFT (death cross)
    buy_signals = [s for s in signals if s["action"] == "buy"]
    sell_signals = [s for s in signals if s["action"] == "sell"]

    # Should have buy signal for AAPL
    assert any(s["symbol"] == "AAPL" for s in buy_signals)

    # Should have sell signal for MSFT
    assert any(s["symbol"] == "MSFT" for s in sell_signals)


def test_position_sizing():
    """Test strategy correctly sizes positions based on position_size parameter."""
    strategy = MovingAverageCrossoverStrategy(
        fast_period=5,
        slow_period=10,
        position_size=0.4,  # 40% of portfolio per position
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Golden cross stock
    for i in range(50):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 1000000,
        })
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=50),
        "close": 101.0,
        "open": 100.5,
        "high": 102.0,
        "low": 100.0,
        "volume": 1000000,
    })

    current_date = base_date + timedelta(days=51)
    initial_cash = 100000.0

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=initial_cash,
    )

    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) > 0

    # Should allocate 40% of cash
    signal = buy_signals[0]
    current_price = 101.0  # Price on day 50 (100 + 1*1)
    expected_amount = initial_cash * 0.4
    actual_amount = signal["shares"] * current_price

    # Allow for rounding differences (up to 1 share worth)
    assert abs(actual_amount - expected_amount) < current_price
