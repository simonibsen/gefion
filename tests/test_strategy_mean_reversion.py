"""
Tests for mean reversion trading strategy.
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

from gefion.strategies.mean_reversion import MeanReversionStrategy


def test_strategy_initialization():
    """Test strategy can be initialized with parameters."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_overbought=70,
        rsi_period=14,
        position_size=0.1,
        max_positions=5,
    )

    assert strategy.rsi_oversold == 30
    assert strategy.rsi_overbought == 70
    assert strategy.rsi_period == 14
    assert strategy.position_size == 0.1
    assert strategy.max_positions == 5


def test_empty_price_data():
    """Test strategy handles empty price data gracefully."""
    strategy = MeanReversionStrategy()

    signals = strategy.generate_signals(
        current_date=date(2024, 1, 1),
        portfolio={},
        price_data=[],
        initial_cash=100000.0,
    )

    assert signals == []


def test_insufficient_data_for_rsi():
    """Test strategy returns no signals when insufficient data for RSI calculation."""
    strategy = MeanReversionStrategy(rsi_period=14)

    # Only 10 days of data, need 14+ for RSI
    price_data = []
    base_date = date(2024, 1, 1)
    for i in range(10):
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
        current_date=base_date + timedelta(days=10),
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    assert signals == []


def test_buy_signal_oversold():
    """Test strategy generates buy signals for oversold stocks."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_overbought=70,
        rsi_period=14,
        position_size=0.2,  # 20% per position
        max_positions=5,
    )

    # Create price data with strong downtrend (RSI will be low)
    price_data = []
    base_date = date(2024, 1, 1)

    # Start at 100, drop to 80 over 20 days (strong downtrend)
    for i in range(20):
        close = 100.0 - (i * 1.0)  # -1 per day
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close + 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=20)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate buy signal for oversold AAPL
    assert len(signals) > 0
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) > 0

    aapl_signal = next((s for s in buy_signals if s["symbol"] == "AAPL"), None)
    assert aapl_signal is not None
    assert aapl_signal["shares"] > 0


def test_sell_signal_overbought():
    """Test strategy generates sell signals for overbought stocks."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_overbought=70,
        rsi_period=14,
    )

    # Create price data with strong uptrend (RSI will be high)
    price_data = []
    base_date = date(2024, 1, 1)

    # Start at 100, rise to 120 over 20 days (strong uptrend)
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

    # Hold position in AAPL
    portfolio = {
        "AAPL": {"shares": 100, "avg_price": 100.0}
    }

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate sell signal for overbought AAPL
    sell_signals = [s for s in signals if s["action"] == "sell"]
    assert len(sell_signals) > 0

    aapl_signal = next((s for s in sell_signals if s["symbol"] == "AAPL"), None)
    assert aapl_signal is not None
    assert aapl_signal["shares"] == 100  # Sell entire position


def test_neutral_rsi_no_signals():
    """Test strategy generates no signals when RSI is neutral."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_overbought=70,
        rsi_period=14,
    )

    # Create price data with sideways movement (RSI ~50)
    price_data = []
    base_date = date(2024, 1, 1)

    for i in range(20):
        # Oscillate around 100
        close = 100.0 + (2.0 if i % 2 == 0 else -2.0)
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=20)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # No signals for neutral RSI
    assert len(signals) == 0


def test_max_positions_limit():
    """Test strategy respects max_positions limit."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_period=14,
        position_size=0.2,
        max_positions=2,  # Only 2 positions allowed
    )

    # Create 3 oversold stocks
    price_data = []
    base_date = date(2024, 1, 1)

    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        # Strong downtrend for all
        for i in range(20):
            close = 100.0 - (i * 1.0)
            price_data.append({
                "symbol": symbol,
                "date": base_date + timedelta(days=i),
                "close": close,
                "open": close + 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "volume": 1000000,
            })

    current_date = base_date + timedelta(days=20)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should only buy top 2 most oversold stocks
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) <= 2


def test_multiple_symbols_mixed_signals():
    """Test strategy handles multiple symbols with different RSI levels."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_overbought=70,
        rsi_period=14,
        position_size=0.2,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # AAPL: Strong downtrend (oversold)
    for i in range(20):
        close = 100.0 - (i * 1.0)
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close + 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    # MSFT: Strong uptrend (overbought)
    for i in range(20):
        close = 100.0 + (i * 1.0)
        price_data.append({
            "symbol": "MSFT",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    # GOOGL: Sideways (neutral)
    for i in range(20):
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

    current_date = base_date + timedelta(days=20)

    # Hold MSFT (will be sold as overbought)
    portfolio = {
        "MSFT": {"shares": 100, "avg_price": 100.0}
    }

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should buy AAPL (oversold) and sell MSFT (overbought)
    buy_signals = [s for s in signals if s["action"] == "buy"]
    sell_signals = [s for s in signals if s["action"] == "sell"]

    assert len(buy_signals) >= 1
    assert len(sell_signals) >= 1

    # Verify AAPL buy
    assert any(s["symbol"] == "AAPL" for s in buy_signals)

    # Verify MSFT sell
    assert any(s["symbol"] == "MSFT" for s in sell_signals)

    # No signal for GOOGL (neutral)
    assert not any(s["symbol"] == "GOOGL" for s in signals)


def test_position_sizing():
    """Test strategy correctly sizes positions based on position_size parameter."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_period=14,
        position_size=0.25,  # 25% of portfolio per position
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Oversold stock
    for i in range(20):
        close = 100.0 - (i * 1.0)
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close + 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=20)
    initial_cash = 100000.0

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=initial_cash,
    )

    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) > 0

    # Should allocate 25% of cash
    signal = buy_signals[0]
    current_price = 81.0  # Price on day 19 (100 - 19)
    expected_amount = initial_cash * 0.25
    actual_amount = signal["shares"] * current_price

    # Allow for rounding differences (up to 1 share worth)
    assert abs(actual_amount - expected_amount) < current_price
