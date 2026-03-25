"""
Tests for breakout trading strategy.
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

from gefion.strategies.breakout import BreakoutStrategy


def test_strategy_initialization():
    """Test strategy can be initialized with parameters."""
    strategy = BreakoutStrategy(
        lookback_days=20,
        volume_threshold=1.5,
        position_size=0.25,
        max_positions=3,
    )

    assert strategy.lookback_days == 20
    assert strategy.volume_threshold == 1.5
    assert strategy.position_size == 0.25
    assert strategy.max_positions == 3


def test_empty_price_data():
    """Test strategy handles empty price data gracefully."""
    strategy = BreakoutStrategy()

    signals = strategy.generate_signals(
        current_date=date(2024, 1, 1),
        portfolio={},
        price_data=[],
        initial_cash=100000.0,
    )

    assert signals == []


def test_insufficient_data_for_breakout():
    """Test strategy returns no signals when insufficient data for breakout detection."""
    strategy = BreakoutStrategy(lookback_days=20)

    # Only 15 days of data, need 20+ for lookback
    price_data = []
    base_date = date(2024, 1, 1)
    for i in range(15):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 99.0,
            "volume": 1000000,
        })

    signals = strategy.generate_signals(
        current_date=base_date + timedelta(days=15),
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    assert signals == []


def test_upside_breakout_buy_signal():
    """Test strategy generates buy signal on upside breakout with volume confirmation."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
        position_size=0.3,
        max_positions=5,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-9: trading range 98-102
    for i in range(10):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 98.0,
            "volume": 1000000,
        })

    # Day 10: breakout above range with high volume
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=10),
        "close": 105.0,
        "high": 105.0,
        "low": 101.0,
        "volume": 1600000,  # 1.6x average volume
    })

    current_date = base_date + timedelta(days=11)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate buy signal for breakout
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) > 0

    aapl_signal = next((s for s in buy_signals if s["symbol"] == "AAPL"), None)
    assert aapl_signal is not None
    assert aapl_signal["shares"] > 0


def test_no_buy_without_volume_confirmation():
    """Test strategy does not buy on price breakout without volume confirmation."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-9: trading range
    for i in range(10):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 98.0,
            "volume": 1000000,
        })

    # Day 10: price breakout but low volume
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=10),
        "close": 105.0,
        "high": 105.0,
        "low": 101.0,
        "volume": 900000,  # Below average volume
    })

    current_date = base_date + timedelta(days=11)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should NOT generate buy signal (no volume confirmation)
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) == 0


def test_downside_breakout_sell_signal():
    """Test strategy generates sell signal on downside breakout."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-9: trading range 98-102
    for i in range(10):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 98.0,
            "volume": 1000000,
        })

    # Day 10: breakdown below range with high volume
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=10),
        "close": 95.0,
        "high": 99.0,
        "low": 95.0,
        "volume": 1600000,  # 1.6x average volume
    })

    current_date = base_date + timedelta(days=11)

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

    # Should generate sell signal for breakdown
    sell_signals = [s for s in signals if s["action"] == "sell"]
    assert len(sell_signals) > 0

    aapl_signal = next((s for s in sell_signals if s["symbol"] == "AAPL"), None)
    assert aapl_signal is not None
    assert aapl_signal["shares"] == 100  # Sell entire position


def test_no_signal_in_range():
    """Test strategy generates no signals when price stays in range."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-10: all within range
    for i in range(11):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 98.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=11)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # No signals when in range
    assert len(signals) == 0


def test_max_positions_limit():
    """Test strategy respects max_positions limit."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
        position_size=0.3,
        max_positions=2,  # Only 2 positions allowed
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Create 3 stocks with breakout pattern
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        # Trading range
        for i in range(10):
            price_data.append({
                "symbol": symbol,
                "date": base_date + timedelta(days=i),
                "close": 100.0,
                "high": 102.0,
                "low": 98.0,
                "volume": 1000000,
            })

        # Breakout with volume
        price_data.append({
            "symbol": symbol,
            "date": base_date + timedelta(days=10),
            "close": 105.0,
            "high": 105.0,
            "low": 101.0,
            "volume": 1600000,
        })

    current_date = base_date + timedelta(days=11)

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
    """Test strategy handles multiple symbols with different breakout states."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
        position_size=0.25,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # AAPL: Upside breakout
    for i in range(10):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 98.0,
            "volume": 1000000,
        })
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=10),
        "close": 105.0,
        "high": 105.0,
        "low": 101.0,
        "volume": 1600000,
    })

    # MSFT: Downside breakout
    for i in range(10):
        price_data.append({
            "symbol": "MSFT",
            "date": base_date + timedelta(days=i),
            "close": 200.0,
            "high": 202.0,
            "low": 198.0,
            "volume": 2000000,
        })
    price_data.append({
        "symbol": "MSFT",
        "date": base_date + timedelta(days=10),
        "close": 195.0,
        "high": 199.0,
        "low": 195.0,
        "volume": 3200000,
    })

    # GOOGL: In range (no breakout)
    for i in range(11):
        price_data.append({
            "symbol": "GOOGL",
            "date": base_date + timedelta(days=i),
            "close": 150.0,
            "high": 152.0,
            "low": 148.0,
            "volume": 1500000,
        })

    current_date = base_date + timedelta(days=11)

    # Hold MSFT (will be sold on breakdown)
    portfolio = {
        "MSFT": {"shares": 100, "avg_price": 200.0}
    }

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should buy AAPL (breakout) and sell MSFT (breakdown)
    buy_signals = [s for s in signals if s["action"] == "buy"]
    sell_signals = [s for s in signals if s["action"] == "sell"]

    # Verify AAPL buy
    assert any(s["symbol"] == "AAPL" for s in buy_signals)

    # Verify MSFT sell
    assert any(s["symbol"] == "MSFT" for s in sell_signals)

    # No signal for GOOGL (in range)
    assert not any(s["symbol"] == "GOOGL" for s in signals)


def test_engine_data_format():
    """Test strategy handles engine's Dict[str, List[Dict]] format.

    The backtest engine passes price_data as Dict[symbol -> list of price records],
    not as a flat list. This test ensures compatibility.
    """
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
        position_size=0.3,
    )

    base_date = date(2024, 1, 1)

    # Engine format: Dict[str, List[Dict]]
    price_data = {
        "AAPL": [
            {
                "symbol": "AAPL",
                "date": base_date + timedelta(days=i),
                "close": 100.0,
                "high": 102.0,
                "low": 98.0,
                "volume": 1000000,
            }
            for i in range(10)
        ] + [
            {
                "symbol": "AAPL",
                "date": base_date + timedelta(days=10),
                "close": 105.0,
                "high": 105.0,  # Breakout above 102
                "low": 101.0,
                "volume": 1600000,  # Volume confirmation
            }
        ],
    }

    current_date = base_date + timedelta(days=10)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate buy signal for AAPL breakout
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) > 0
    assert buy_signals[0]["symbol"] == "AAPL"


def test_position_sizing():
    """Test strategy correctly sizes positions based on position_size parameter."""
    strategy = BreakoutStrategy(
        lookback_days=10,
        volume_threshold=1.5,
        position_size=0.4,  # 40% of portfolio per position
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Trading range then breakout
    for i in range(10):
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": 100.0,
            "high": 102.0,
            "low": 98.0,
            "volume": 1000000,
        })
    price_data.append({
        "symbol": "AAPL",
        "date": base_date + timedelta(days=10),
        "close": 105.0,
        "high": 105.0,
        "low": 101.0,
        "volume": 1600000,
    })

    current_date = base_date + timedelta(days=11)
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
    current_price = 105.0
    expected_amount = initial_cash * 0.4
    actual_amount = signal["shares"] * current_price

    # Allow for rounding differences (up to 1 share worth)
    assert abs(actual_amount - expected_amount) < current_price
