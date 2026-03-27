"""
TDD tests for walk-forward optimization module.

Tests written FIRST before implementation.
"""
import pytest
from datetime import date


class TestWalkForwardConfig:
    """Test WalkForwardConfig configuration."""

    def test_default_config_values(self):
        """Default config has reasonable values."""
        from gefion.backtest.optimization import WalkForwardConfig

        config = WalkForwardConfig()
        assert config.train_days == 252  # 1 year
        assert config.test_days == 63  # 1 quarter
        assert config.step_days == 63  # 1 quarter
        assert config.optimization_metric == "sharpe_ratio"

    def test_config_can_be_customized(self):
        """Config values can be customized."""
        from gefion.backtest.optimization import WalkForwardConfig

        config = WalkForwardConfig(
            train_days=126,
            test_days=21,
            step_days=21,
            optimization_metric="total_return",
        )
        assert config.train_days == 126
        assert config.test_days == 21
        assert config.step_days == 21
        assert config.optimization_metric == "total_return"


class TestWalkForwardWindow:
    """Test WalkForwardWindow data structure."""

    def test_window_has_required_fields(self):
        """Window has train and test date ranges."""
        from gefion.backtest.optimization import WalkForwardWindow

        window = WalkForwardWindow(
            train_start=date(2020, 1, 1),
            train_end=date(2020, 12, 31),
            test_start=date(2021, 1, 1),
            test_end=date(2021, 3, 31),
        )
        assert window.train_start == date(2020, 1, 1)
        assert window.train_end == date(2020, 12, 31)
        assert window.test_start == date(2021, 1, 1)
        assert window.test_end == date(2021, 3, 31)


class TestWindowGeneration:
    """Test walk-forward window generation."""

    def test_generate_windows_basic(self):
        """Generate windows creates correct date ranges."""
        from gefion.backtest.optimization import WalkForwardOptimizer, WalkForwardConfig

        config = WalkForwardConfig(
            train_days=252,
            test_days=63,
            step_days=63,
        )
        optimizer = WalkForwardOptimizer(config)

        # 2 years of data = approximately 504 trading days
        windows = optimizer.generate_windows(
            start_date=date(2020, 1, 1),
            end_date=date(2021, 12, 31),
        )

        assert len(windows) > 0
        # First window should start at the beginning
        assert windows[0].train_start == date(2020, 1, 1)

    def test_generate_windows_no_overlap_in_test(self):
        """Test periods don't overlap between windows."""
        from gefion.backtest.optimization import WalkForwardOptimizer, WalkForwardConfig

        config = WalkForwardConfig(
            train_days=252,
            test_days=63,
            step_days=63,
        )
        optimizer = WalkForwardOptimizer(config)

        windows = optimizer.generate_windows(
            start_date=date(2020, 1, 1),
            end_date=date(2022, 12, 31),
        )

        # Check that test periods don't overlap
        for i in range(len(windows) - 1):
            assert windows[i].test_end < windows[i + 1].test_start

    def test_generate_windows_insufficient_data(self):
        """Returns empty list if not enough data for single window."""
        from gefion.backtest.optimization import WalkForwardOptimizer, WalkForwardConfig

        config = WalkForwardConfig(
            train_days=252,
            test_days=63,
        )
        optimizer = WalkForwardOptimizer(config)

        # Only 3 months of data, not enough for 1 year train + quarter test
        windows = optimizer.generate_windows(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 3, 31),
        )

        assert len(windows) == 0


class TestParameterGrid:
    """Test parameter grid generation and iteration."""

    def test_grid_expansion(self):
        """Parameter grid expands correctly."""
        from gefion.backtest.optimization import expand_param_grid

        grid = {
            "lookback": [10, 20, 30],
            "threshold": [0.01, 0.02],
        }
        combinations = expand_param_grid(grid)

        # 3 lookbacks * 2 thresholds = 6 combinations
        assert len(combinations) == 6


class TestWalkForwardResult:
    """Test WalkForwardResult data structure."""

    def test_result_has_required_fields(self):
        """Result contains key metrics."""
        from gefion.backtest.optimization import WalkForwardResult

        result = WalkForwardResult(
            windows=[],
            best_params_per_window=[],
            in_sample_metrics=[],
            out_of_sample_metrics=[],
            aggregate_metrics={"sharpe_ratio": 1.5, "total_return": 0.15},
            overfitting_score=0.3,
        )
        assert result.aggregate_metrics["sharpe_ratio"] == 1.5
        assert result.overfitting_score == 0.3


class TestOverfittingDetection:
    """Test overfitting detection metrics."""

    def test_overfitting_score_calculation(self):
        """Overfitting score measures in-sample vs out-of-sample degradation."""
        from gefion.backtest.optimization import calculate_overfitting_score

        # In-sample performed much better than out-of-sample
        in_sample_returns = [0.20, 0.25, 0.22]  # High returns
        out_of_sample_returns = [0.05, 0.03, 0.04]  # Low returns

        score = calculate_overfitting_score(
            in_sample_returns, out_of_sample_returns
        )

        # Score should be high (significant degradation)
        assert score > 0.5

    def test_no_overfitting_low_score(self):
        """Good strategies have low overfitting score."""
        from gefion.backtest.optimization import calculate_overfitting_score

        # Similar performance in and out of sample
        in_sample_returns = [0.10, 0.12, 0.11]
        out_of_sample_returns = [0.09, 0.10, 0.10]

        score = calculate_overfitting_score(
            in_sample_returns, out_of_sample_returns
        )

        # Score should be low (consistent performance)
        assert score < 0.3


class TestOptimizationMetrics:
    """Test optimization metric calculations."""

    def test_supported_metrics(self):
        """Optimizer supports standard metrics."""
        from gefion.backtest.optimization import SUPPORTED_METRICS

        assert "sharpe_ratio" in SUPPORTED_METRICS
        assert "total_return" in SUPPORTED_METRICS
        assert "max_drawdown" in SUPPORTED_METRICS
        assert "calmar_ratio" in SUPPORTED_METRICS


class TestWalkForwardOptimizer:
    """Test full walk-forward optimization flow."""

    def test_optimizer_initialization(self):
        """Optimizer initializes with config."""
        from gefion.backtest.optimization import WalkForwardOptimizer, WalkForwardConfig

        config = WalkForwardConfig()
        optimizer = WalkForwardOptimizer(config)

        assert optimizer.config == config

    def test_optimizer_run_returns_result(self):
        """Running optimizer returns WalkForwardResult."""
        from gefion.backtest.optimization import (
            WalkForwardOptimizer,
            WalkForwardConfig,
            WalkForwardResult,
        )
        import pandas as pd

        config = WalkForwardConfig(
            train_days=60,  # Shorter for testing
            test_days=30,
            step_days=30,
        )
        optimizer = WalkForwardOptimizer(config)

        # Minimal mock data - just needs dates and prices
        dates = pd.date_range("2020-01-01", periods=200, freq="D")
        price_data = pd.DataFrame(
            {"AAPL": [100 + i * 0.1 for i in range(200)]},
            index=dates,
        )

        # Simple strategy factory that returns a dummy strategy
        def strategy_factory(params):
            class DummyStrategy:
                def __init__(self, params):
                    self.params = params

                def generate_signals(self, current_date, prices, portfolio):
                    return []

            return DummyStrategy(params)

        param_grid = {"threshold": [0.01, 0.02]}

        result = optimizer.run(
            price_data=price_data,
            strategy_factory=strategy_factory,
            param_grid=param_grid,
            symbols=["AAPL"],
        )

        assert isinstance(result, WalkForwardResult)
