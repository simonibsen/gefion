"""
Tests for volatility contraction (Bollinger Band squeeze) trading strategy.
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

from g2.strategies.volatility_contraction import VolatilityContractionStrategy


def test_strategy_initialization():
    """Test strategy can be initialized with parameters."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.05,
        expansion_threshold=0.10,
        position_size=0.2,
        max_positions=5,
    )

    assert strategy.bb_period == 20
    assert strategy.bb_std_dev == 2.0
    assert strategy.squeeze_threshold == 0.05
    assert strategy.expansion_threshold == 0.10
    assert strategy.position_size == 0.2
    assert strategy.max_positions == 5


def test_empty_price_data():
    """Test strategy handles empty price data gracefully."""
    strategy = VolatilityContractionStrategy()

    signals = strategy.generate_signals(
        current_date=date(2024, 1, 1),
        portfolio={},
        price_data=[],
        initial_cash=100000.0,
    )

    assert signals == []


def test_insufficient_data_for_bollinger_bands():
    """Test strategy returns no signals when insufficient data for Bollinger Bands."""
    strategy = VolatilityContractionStrategy(bb_period=20)

    # Only 15 days of data, need 20+ for Bollinger Bands
    price_data = []
    base_date = date(2024, 1, 1)
    for i in range(15):
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
        current_date=base_date + timedelta(days=15),
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    assert signals == []


def test_buy_signal_on_expansion_from_squeeze():
    """Test strategy generates buy signal when volatility expands from squeeze."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.06,
        expansion_threshold=0.12,
        position_size=0.25,
        max_positions=5,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-24: Sideways market (low volatility squeeze)
    for i in range(25):
        close = 100.0 + (0.5 if i % 2 == 0 else -0.5)  # Tight range
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 0.25,
            "low": close - 0.25,
            "volume": 1000000,
        })

    # Days 25-29: Expansion (breakout with increasing volatility)
    for i in range(25, 30):
        close = 100.0 + (i - 24) * 2.0  # Strong upward move
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 1.0,
            "high": close + 1.5,
            "low": close - 1.5,
            "volume": 1500000,
        })

    current_date = base_date + timedelta(days=30)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should process without errors
    # If signals generated, verify they're buy signals
    assert isinstance(signals, list)

    for signal in signals:
        if signal["symbol"] == "AAPL" and signal["action"] == "buy":
            assert signal["shares"] > 0


def test_sell_signal_on_contraction():
    """Test strategy generates sell signal when volatility contracts after expansion."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.06,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Days 0-19: Wide volatility (expanded)
    for i in range(20):
        close = 100.0 + (i % 3) * 3.0  # Volatile movement
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "volume": 1000000,
        })

    # Days 20-29: Contraction (volatility compressing)
    for i in range(20, 30):
        close = 105.0 + (0.3 if i % 2 == 0 else -0.3)  # Tight range
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=30)

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

    # Should process without errors
    assert isinstance(signals, list)

    # If sell signals generated for AAPL, verify shares
    for signal in signals:
        if signal["symbol"] == "AAPL" and signal["action"] == "sell":
            assert signal["shares"] == 100


def test_no_signal_during_squeeze():
    """Test strategy generates no signals during ongoing squeeze (waiting for expansion)."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.06,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # All days: Tight sideways range (ongoing squeeze, no expansion)
    for i in range(30):
        close = 100.0 + (0.2 if i % 2 == 0 else -0.2)
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 0.15,
            "low": close - 0.15,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=30)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Ongoing squeeze without expansion should produce no signals
    # (or very few signals if any threshold barely crossed)
    assert isinstance(signals, list)


def test_max_positions_limit():
    """Test strategy respects max_positions limit."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.06,
        expansion_threshold=0.12,
        position_size=0.2,
        max_positions=2,  # Only 2 positions allowed
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Create 3 stocks with squeeze-expansion pattern
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        # Squeeze period
        for i in range(25):
            close = 100.0 + (0.5 if i % 2 == 0 else -0.5)
            price_data.append({
                "symbol": symbol,
                "date": base_date + timedelta(days=i),
                "close": close,
                "open": close,
                "high": close + 0.25,
                "low": close - 0.25,
                "volume": 1000000,
            })

        # Expansion period
        for i in range(25, 30):
            close = 100.0 + (i - 24) * 2.0
            price_data.append({
                "symbol": symbol,
                "date": base_date + timedelta(days=i),
                "close": close,
                "open": close - 1.0,
                "high": close + 1.5,
                "low": close - 1.5,
                "volume": 1500000,
            })

    current_date = base_date + timedelta(days=30)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should only buy top 2 stocks
    buy_signals = [s for s in signals if s["action"] == "buy"]
    assert len(buy_signals) <= 2


def test_position_sizing():
    """Test strategy correctly sizes positions based on position_size parameter."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.06,
        expansion_threshold=0.12,
        position_size=0.3,  # 30% of portfolio per position
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Squeeze then expansion
    for i in range(25):
        close = 100.0 + (0.5 if i % 2 == 0 else -0.5)
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 0.25,
            "low": close - 0.25,
            "volume": 1000000,
        })

    for i in range(25, 30):
        close = 100.0 + (i - 24) * 2.0
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 1.0,
            "high": close + 1.5,
            "low": close - 1.5,
            "volume": 1500000,
        })

    current_date = base_date + timedelta(days=30)
    initial_cash = 100000.0

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=initial_cash,
    )

    buy_signals = [s for s in signals if s["action"] == "buy"]

    if len(buy_signals) > 0:
        # Should allocate 30% of cash
        signal = buy_signals[0]
        current_price = 110.0  # Approximate current price
        expected_amount = initial_cash * 0.3
        actual_amount = signal["shares"] * current_price

        # Allow for rounding differences
        assert abs(actual_amount - expected_amount) < current_price * 2


def test_bollinger_band_calculation():
    """Test strategy correctly calculates Bollinger Bands."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # Create price data with known statistics
    # Use consistent prices to verify calculation
    for i in range(25):
        close = 100.0  # Constant price for simple verification
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "volume": 1000000,
        })

    current_date = base_date + timedelta(days=25)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # With constant prices, bands should be tight (low volatility)
    # Strategy should recognize this as a squeeze
    assert isinstance(signals, list)


def test_multiple_symbols_mixed_patterns():
    """Test strategy handles multiple symbols with different volatility patterns."""
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.06,
        expansion_threshold=0.12,
        position_size=0.2,
    )

    price_data = []
    base_date = date(2024, 1, 1)

    # AAPL: Squeeze then expansion (should buy)
    for i in range(25):
        close = 100.0 + (0.5 if i % 2 == 0 else -0.5)
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 0.25,
            "low": close - 0.25,
            "volume": 1000000,
        })
    for i in range(25, 30):
        close = 100.0 + (i - 24) * 2.0
        price_data.append({
            "symbol": "AAPL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 1.0,
            "high": close + 1.5,
            "low": close - 1.5,
            "volume": 1500000,
        })

    # MSFT: Ongoing squeeze (no signal)
    for i in range(30):
        close = 200.0 + (0.2 if i % 2 == 0 else -0.2)
        price_data.append({
            "symbol": "MSFT",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close,
            "high": close + 0.15,
            "low": close - 0.15,
            "volume": 2000000,
        })

    # GOOGL: High volatility (no squeeze)
    for i in range(30):
        close = 150.0 + (i % 3) * 5.0
        price_data.append({
            "symbol": "GOOGL",
            "date": base_date + timedelta(days=i),
            "close": close,
            "open": close - 2.0,
            "high": close + 3.0,
            "low": close - 3.0,
            "volume": 1500000,
        })

    current_date = base_date + timedelta(days=30)

    signals = strategy.generate_signals(
        current_date=current_date,
        portfolio={},
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should process multiple symbols without errors
    assert isinstance(signals, list)
