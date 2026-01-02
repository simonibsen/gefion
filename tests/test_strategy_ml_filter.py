"""
Tests for ML Filter Strategy (hybrid approach).

Tests the strategy wrapper that filters signals from base strategies
using ML predictions.
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock


class TestMLFilterStrategyBasics:
    """Test MLFilterStrategy class structure."""

    def test_strategy_can_be_imported(self):
        """MLFilterStrategy should be importable."""
        from g2.strategies.ml_filter import MLFilterStrategy
        assert MLFilterStrategy is not None

    def test_strategy_wraps_base_strategy(self):
        """MLFilterStrategy should wrap a base strategy instance."""
        from g2.strategies.ml_filter import MLFilterStrategy

        # Create mock base strategy
        mock_base = MagicMock()
        mock_base.generate_signals.return_value = []

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
        )

        assert strategy.base_strategy is mock_base

    def test_factory_function_exists(self):
        """create_ml_filtered_strategy factory should exist."""
        from g2.strategies.ml_filter import create_ml_filtered_strategy
        assert callable(create_ml_filtered_strategy)


class TestMLFilterSignalFiltering:
    """Test signal filtering logic."""

    @patch("g2.strategies.ml_filter.get_predictions_for_date")
    def test_passes_buy_with_positive_prediction(self, mock_get_preds):
        """Buy signals should pass when ML confirms upside."""
        from g2.strategies.ml_filter import MLFilterStrategy

        mock_get_preds.return_value = {
            "AAPL": {"q10": 0.01, "q50": 0.05, "q90": 0.10},
        }

        mock_base = MagicMock()
        mock_base.generate_signals.return_value = [
            {"action": "buy", "symbol": "AAPL", "shares": 100},
        ]

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
            filter_mode="confirm",
            min_q50=0.0,
        )

        price_data = {"AAPL": [{"date": date(2024, 1, 1), "close": 100}]}
        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        assert len(signals) == 1
        assert signals[0]["action"] == "buy"
        assert "ML:" in signals[0]["reason"]

    @patch("g2.strategies.ml_filter.get_predictions_for_date")
    def test_blocks_buy_with_negative_prediction(self, mock_get_preds):
        """Buy signals should be blocked when ML predicts downside."""
        from g2.strategies.ml_filter import MLFilterStrategy

        mock_get_preds.return_value = {
            "AAPL": {"q10": -0.15, "q50": -0.05, "q90": 0.02},
        }

        mock_base = MagicMock()
        mock_base.generate_signals.return_value = [
            {"action": "buy", "symbol": "AAPL", "shares": 100},
        ]

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
            filter_mode="confirm",
            min_q50=0.0,
        )

        price_data = {"AAPL": [{"date": date(2024, 1, 1), "close": 100}]}
        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        # Buy signal should be filtered out
        assert len(signals) == 0

    @patch("g2.strategies.ml_filter.get_predictions_for_date")
    def test_always_passes_sell_signals(self, mock_get_preds):
        """Sell signals should always pass through (don't block exits)."""
        from g2.strategies.ml_filter import MLFilterStrategy

        # Positive prediction (but we still want to allow sells)
        mock_get_preds.return_value = {
            "AAPL": {"q10": 0.05, "q50": 0.10, "q90": 0.20},
        }

        mock_base = MagicMock()
        mock_base.generate_signals.return_value = [
            {"action": "sell", "symbol": "AAPL", "shares": 100},
        ]

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
        )

        price_data = {"AAPL": [{"date": date(2024, 1, 1), "close": 100}]}
        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={"AAPL": {"shares": 100}},
            price_data=price_data,
            initial_cash=100000,
        )

        # Sell should pass through
        assert len(signals) == 1
        assert signals[0]["action"] == "sell"


class TestMLFilterModes:
    """Test different filter modes."""

    @patch("g2.strategies.ml_filter.get_predictions_for_date")
    def test_confirm_mode_requires_prediction(self, mock_get_preds):
        """Confirm mode should skip signals without predictions."""
        from g2.strategies.ml_filter import MLFilterStrategy

        # No prediction for AAPL
        mock_get_preds.return_value = {}

        mock_base = MagicMock()
        mock_base.generate_signals.return_value = [
            {"action": "buy", "symbol": "AAPL", "shares": 100},
        ]

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
            filter_mode="confirm",
        )

        price_data = {"AAPL": [{"date": date(2024, 1, 1), "close": 100}]}
        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        # Should be filtered out (no prediction to confirm)
        assert len(signals) == 0

    @patch("g2.strategies.ml_filter.get_predictions_for_date")
    def test_veto_mode_allows_without_prediction(self, mock_get_preds):
        """Veto mode should allow signals without predictions."""
        from g2.strategies.ml_filter import MLFilterStrategy

        # No prediction for AAPL
        mock_get_preds.return_value = {}

        mock_base = MagicMock()
        mock_base.generate_signals.return_value = [
            {"action": "buy", "symbol": "AAPL", "shares": 100},
        ]

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
            filter_mode="veto",
        )

        price_data = {"AAPL": [{"date": date(2024, 1, 1), "close": 100}]}
        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        # Should pass through (no prediction to veto)
        assert len(signals) == 1


class TestMLFilterWithClassifier:
    """Test classifier-based filtering."""

    @patch("g2.strategies.ml_filter.get_classifier_predictions_for_date")
    def test_allows_bullish_class(self, mock_get_preds):
        """Should allow buy when classifier predicts bullish."""
        from g2.strategies.ml_filter import MLFilterStrategy

        mock_get_preds.return_value = {
            "AAPL": {
                "predicted_class": "strong_up",
                "p_strong_up": 0.7,
                "margin": 0.5,
            },
        }

        mock_base = MagicMock()
        mock_base.generate_signals.return_value = [
            {"action": "buy", "symbol": "AAPL", "shares": 100},
        ]

        strategy = MLFilterStrategy(
            base_strategy=mock_base,
            model_name="test",
            model_version="v1",
            prediction_type="classifier",
            allowed_classes=["strong_up", "weak_up"],
            min_confidence=0.5,
        )

        price_data = {"AAPL": [{"date": date(2024, 1, 1), "close": 100}]}
        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        assert len(signals) == 1


class TestMLFilterFactory:
    """Test the factory function."""

    def test_creates_filtered_strategy(self):
        """Factory should create working MLFilterStrategy."""
        from g2.strategies.ml_filter import create_ml_filtered_strategy
        from g2.strategies.momentum import MomentumStrategy

        filtered = create_ml_filtered_strategy(
            base_strategy_class=MomentumStrategy,
            base_params={"lookback_days": 10, "top_n": 3},
            ml_params={
                "model_name": "test",
                "model_version": "v1",
                "filter_mode": "confirm",
            },
        )

        assert filtered.base_strategy is not None
        assert isinstance(filtered.base_strategy, MomentumStrategy)
        assert filtered.model_name == "test"
        assert filtered.filter_mode == "confirm"
