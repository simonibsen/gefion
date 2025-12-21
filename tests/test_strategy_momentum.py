"""
TDD tests for momentum trading strategy.

These tests will initially fail and drive the implementation of
a simple momentum-following strategy.
"""
import pytest
from datetime import date


def test_momentum_strategy_initialization():
    """Test creating a momentum strategy with parameters."""
    from g2.strategies.momentum import MomentumStrategy

    strategy = MomentumStrategy(
        lookback_days=20,
        top_n=5,
        rebalance_days=5,
    )

    assert strategy.lookback_days == 20
    assert strategy.top_n == 5
    assert strategy.rebalance_days == 5


def test_calculate_momentum():
    """Test calculating momentum (percent return) over lookback period."""
    from g2.strategies.momentum import calculate_momentum

    # Price increased from 100 to 120 over period
    price_history = [
        {"date": date(2024, 1, 1), "close": 100.0},
        {"date": date(2024, 1, 5), "close": 105.0},
        {"date": date(2024, 1, 10), "close": 110.0},
        {"date": date(2024, 1, 15), "close": 115.0},
        {"date": date(2024, 1, 20), "close": 120.0},
    ]

    momentum = calculate_momentum(price_history, lookback_days=20)

    # (120 - 100) / 100 = 0.20 = 20%
    assert abs(momentum - 0.20) < 0.001


def test_calculate_momentum_insufficient_data():
    """Test that momentum uses available data when less than lookback period."""
    from g2.strategies.momentum import calculate_momentum

    # Only 2 days of data, but lookback is 20
    # Should calculate momentum over available data
    price_history = [
        {"date": date(2024, 1, 1), "close": 100.0},
        {"date": date(2024, 1, 2), "close": 105.0},
    ]

    momentum = calculate_momentum(price_history, lookback_days=20)

    # Should calculate 2-day momentum: (105 - 100) / 100 = 0.05
    assert abs(momentum - 0.05) < 0.001


def test_rank_stocks_by_momentum():
    """Test ranking stocks by momentum and selecting top N."""
    from g2.strategies.momentum import rank_stocks_by_momentum

    stock_momentums = {
        "AAPL": 0.15,   # Rank 2
        "MSFT": 0.20,   # Rank 1 (highest)
        "GOOGL": -0.05, # Rank 5 (negative)
        "TSLA": 0.10,   # Rank 3
        "NVDA": 0.08,   # Rank 4
    }

    top_stocks = rank_stocks_by_momentum(stock_momentums, top_n=3)

    # Should return top 3 by momentum
    assert top_stocks == ["MSFT", "AAPL", "TSLA"]


def test_momentum_strategy_signal_generation():
    """Test that strategy generates buy signals for top momentum stocks."""
    from g2.strategies.momentum import MomentumStrategy

    strategy = MomentumStrategy(lookback_days=5, top_n=2, rebalance_days=5)

    # Sample price data
    price_data = {
        "AAPL": [
            {"date": date(2024, 1, 1), "close": 100.0},
            {"date": date(2024, 1, 2), "close": 105.0},
            {"date": date(2024, 1, 3), "close": 110.0},
        ],
        "MSFT": [
            {"date": date(2024, 1, 1), "close": 200.0},
            {"date": date(2024, 1, 2), "close": 210.0},
            {"date": date(2024, 1, 3), "close": 220.0},
        ],
        "GOOGL": [
            {"date": date(2024, 1, 1), "close": 150.0},
            {"date": date(2024, 1, 2), "close": 148.0},
            {"date": date(2024, 1, 3), "close": 145.0},  # Negative momentum
        ],
    }

    # Mock portfolio with no positions
    portfolio = type('Portfolio', (), {'positions': {}})()

    signals = strategy.generate_signals(
        current_date=date(2024, 1, 3),
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should generate buy signals for MSFT (10% gain) and AAPL (10% gain)
    # Should NOT buy GOOGL (negative momentum)
    assert len(signals) == 2

    symbols = [s["symbol"] for s in signals]
    assert "AAPL" in symbols or "MSFT" in symbols
    assert "GOOGL" not in symbols

    # All signals should be buys
    for signal in signals:
        assert signal["action"] == "buy"
        assert signal["shares"] > 0


def test_momentum_strategy_rebalance_logic():
    """Test that strategy only rebalances on scheduled days."""
    from g2.strategies.momentum import MomentumStrategy

    strategy = MomentumStrategy(lookback_days=2, top_n=1, rebalance_days=5)

    price_data = {
        "AAPL": [
            {"date": date(2023, 12, 29), "close": 95.0},
            {"date": date(2023, 12, 30), "close": 98.0},
            {"date": date(2024, 1, 1), "close": 100.0},  # First signal day
            {"date": date(2024, 1, 2), "close": 105.0},
        ],
    }

    portfolio = type('Portfolio', (), {'positions': {}})()

    # First call - should generate signals (initial buy)
    # On Jan 1, we have 3 data points, positive momentum
    signals1 = strategy.generate_signals(
        current_date=date(2024, 1, 1),
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    assert len(signals1) > 0  # Generated signals on first day

    # Second call next day - should NOT rebalance (too soon)
    signals2 = strategy.generate_signals(
        current_date=date(2024, 1, 2),
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    assert len(signals2) == 0  # No rebalancing yet


def test_momentum_strategy_position_sizing():
    """Test that strategy sizes positions equally across top N stocks."""
    from g2.strategies.momentum import MomentumStrategy

    strategy = MomentumStrategy(lookback_days=5, top_n=3, rebalance_days=5)

    price_data = {
        "AAPL": [
            {"date": date(2024, 1, 1), "close": 100.0},
            {"date": date(2024, 1, 2), "close": 110.0},
        ],
        "MSFT": [
            {"date": date(2024, 1, 1), "close": 200.0},
            {"date": date(2024, 1, 2), "close": 220.0},
        ],
        "GOOGL": [
            {"date": date(2024, 1, 1), "close": 150.0},
            {"date": date(2024, 1, 2), "close": 165.0},
        ],
    }

    portfolio = type('Portfolio', (), {'positions': {}, 'cash': 90000.0})()

    signals = strategy.generate_signals(
        current_date=date(2024, 1, 2),
        portfolio=portfolio,
        price_data=price_data,
        initial_cash=100000.0,
    )

    # Should allocate equally: 30000 per stock (90% of 100k / 3 stocks)
    # AAPL @ 110: 30000 / 110 ≈ 272 shares
    # MSFT @ 220: 30000 / 220 ≈ 136 shares
    # GOOGL @ 165: 30000 / 165 ≈ 181 shares

    assert len(signals) == 3

    # Check that allocations are roughly equal in dollar terms
    for signal in signals:
        if signal["symbol"] == "AAPL":
            assert 250 <= signal["shares"] <= 300
        elif signal["symbol"] == "MSFT":
            assert 130 <= signal["shares"] <= 150
        elif signal["symbol"] == "GOOGL":
            assert 170 <= signal["shares"] <= 190
