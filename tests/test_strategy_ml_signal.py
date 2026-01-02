"""
Tests for ML Signal Strategy.

Tests the strategy that uses ML predictions (quantile or classifier)
to generate trading signals.
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock


class TestMLSignalStrategyBasics:
    """Test MLSignalStrategy class structure."""

    def test_strategy_can_be_imported(self):
        """MLSignalStrategy should be importable."""
        from g2.strategies.ml_signal import MLSignalStrategy
        assert MLSignalStrategy is not None

    def test_strategy_has_required_params(self):
        """Strategy should accept model selection parameters."""
        from g2.strategies.ml_signal import MLSignalStrategy

        strategy = MLSignalStrategy(
            model_name="test_model",
            model_version="v1",
            horizon_days=7,
            return_threshold=0.05,
        )

        assert strategy.model_name == "test_model"
        assert strategy.model_version == "v1"
        assert strategy.horizon_days == 7
        assert strategy.return_threshold == 0.05

    def test_strategy_has_generate_signals_method(self):
        """Strategy should have generate_signals method."""
        from g2.strategies.ml_signal import MLSignalStrategy

        strategy = MLSignalStrategy()
        assert hasattr(strategy, "generate_signals")
        assert callable(strategy.generate_signals)

    def test_strategy_supports_classifier_mode(self):
        """Strategy should support classifier prediction mode."""
        from g2.strategies.ml_signal import MLSignalStrategy

        strategy = MLSignalStrategy(
            prediction_type="classifier",
            trend_classes=["strong_up", "weak_up"],
            confidence_threshold=0.6,
        )

        assert strategy.prediction_type == "classifier"
        assert "strong_up" in strategy.trend_classes


class TestMLSignalStrategySignals:
    """Test signal generation logic."""

    @patch("g2.strategies.ml_signal.get_predictions_for_date")
    def test_generates_buy_signal_for_positive_prediction(self, mock_get_preds):
        """Should generate buy signal when q50 exceeds threshold."""
        from g2.strategies.ml_signal import MLSignalStrategy

        # Mock predictions: AAPL has high expected return
        mock_get_preds.return_value = {
            "AAPL": {"q10": 0.02, "q50": 0.08, "q90": 0.15},
            "MSFT": {"q10": -0.02, "q50": 0.01, "q90": 0.05},
        }

        strategy = MLSignalStrategy(
            model_name="test",
            model_version="v1",
            return_threshold=0.05,
            max_positions=5,
        )

        price_data = {
            "AAPL": [{"date": date(2024, 1, 1), "close": 100.0}],
            "MSFT": [{"date": date(2024, 1, 1), "close": 200.0}],
        }

        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        # Should have buy signal for AAPL (q50=0.08 > threshold=0.05)
        # Should NOT have signal for MSFT (q50=0.01 < threshold)
        buy_symbols = [s["symbol"] for s in signals if s["action"] == "buy"]
        assert "AAPL" in buy_symbols
        assert "MSFT" not in buy_symbols

    @patch("g2.strategies.ml_signal.get_predictions_for_date")
    def test_generates_sell_signal_for_negative_prediction(self, mock_get_preds):
        """Should generate sell signal when q50 is below negative threshold."""
        from g2.strategies.ml_signal import MLSignalStrategy

        # Mock: AAPL has negative outlook
        mock_get_preds.return_value = {
            "AAPL": {"q10": -0.15, "q50": -0.08, "q90": -0.02},
        }

        strategy = MLSignalStrategy(
            model_name="test",
            model_version="v1",
            return_threshold=0.05,
        )

        price_data = {
            "AAPL": [{"date": date(2024, 1, 1), "close": 100.0}],
        }

        # Portfolio holds AAPL
        portfolio = {"AAPL": {"shares": 100}}

        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio=portfolio,
            price_data=price_data,
            initial_cash=100000,
        )

        # Should sell AAPL due to negative outlook
        sell_signals = [s for s in signals if s["action"] == "sell"]
        assert len(sell_signals) == 1
        assert sell_signals[0]["symbol"] == "AAPL"

    @patch("g2.strategies.ml_signal.get_predictions_for_date")
    def test_respects_max_positions(self, mock_get_preds):
        """Should not exceed max_positions limit."""
        from g2.strategies.ml_signal import MLSignalStrategy

        # Many stocks with good predictions
        mock_get_preds.return_value = {
            f"STOCK{i}": {"q10": 0.05, "q50": 0.10, "q90": 0.20}
            for i in range(10)
        }

        strategy = MLSignalStrategy(
            model_name="test",
            model_version="v1",
            return_threshold=0.05,
            max_positions=3,
        )

        price_data = {
            f"STOCK{i}": [{"date": date(2024, 1, 1), "close": 100.0}]
            for i in range(10)
        }

        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        buy_signals = [s for s in signals if s["action"] == "buy"]
        assert len(buy_signals) <= 3

    @patch("g2.strategies.ml_signal.get_predictions_for_date")
    def test_no_signals_when_no_predictions(self, mock_get_preds):
        """Should return empty list when no predictions available."""
        from g2.strategies.ml_signal import MLSignalStrategy

        mock_get_preds.return_value = {}

        strategy = MLSignalStrategy()

        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data={"AAPL": [{"date": date(2024, 1, 1), "close": 100.0}]},
            initial_cash=100000,
        )

        assert signals == []


class TestMLSignalStrategyClassifier:
    """Test classifier-based signal generation."""

    @patch("g2.strategies.ml_signal.get_classifier_predictions_for_date")
    def test_buys_on_strong_up_prediction(self, mock_get_preds):
        """Should buy when classifier predicts strong_up with high confidence."""
        from g2.strategies.ml_signal import MLSignalStrategy

        mock_get_preds.return_value = {
            "AAPL": {
                "predicted_class": "strong_up",
                "p_strong_up": 0.7,
                "p_weak_up": 0.2,
                "p_neutral": 0.05,
                "p_weak_down": 0.03,
                "p_strong_down": 0.02,
                "margin": 0.5,
            },
        }

        strategy = MLSignalStrategy(
            model_name="test_classifier",
            model_version="v1",
            prediction_type="classifier",
            trend_classes=["strong_up"],
            confidence_threshold=0.6,
        )

        price_data = {
            "AAPL": [{"date": date(2024, 1, 1), "close": 100.0}],
        }

        signals = strategy.generate_signals(
            current_date=date(2024, 1, 1),
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        buy_symbols = [s["symbol"] for s in signals if s["action"] == "buy"]
        assert "AAPL" in buy_symbols


class TestMLSignalStrategyRegistration:
    """Test strategy registration in dispatcher."""

    def test_ml_signal_in_builtin_strategies(self):
        """ml_signal should be registered in BUILTIN_STRATEGIES."""
        from g2.strategies.dispatcher import BUILTIN_STRATEGIES

        assert "ml_signal" in BUILTIN_STRATEGIES
        assert BUILTIN_STRATEGIES["ml_signal"]["class_name"] == "MLSignalStrategy"

    def test_ml_signal_has_correct_default_params(self):
        """ml_signal should have sensible default parameters."""
        from g2.strategies.dispatcher import BUILTIN_STRATEGIES

        defaults = BUILTIN_STRATEGIES["ml_signal"]["default_params"]

        # Should have these defaults
        assert "model_name" in defaults
        assert "horizon_days" in defaults
        assert "return_threshold" in defaults
        assert "max_positions" in defaults


class TestMLSignalLookAheadProtection:
    """Test that strategy avoids look-ahead bias."""

    @patch("g2.strategies.ml_signal.get_predictions_for_date")
    def test_queries_previous_day_predictions(self, mock_get_preds):
        """Strategy should query predictions from PREVIOUS day to avoid look-ahead."""
        from g2.strategies.ml_signal import MLSignalStrategy
        from datetime import timedelta

        mock_get_preds.return_value = {}

        strategy = MLSignalStrategy(
            model_name="test",
            model_version="v1",
            prediction_source="database",  # Use stored predictions
        )

        current = date(2024, 1, 15)
        price_data = {"AAPL": [{"date": current, "close": 100.0}]}

        strategy.generate_signals(
            current_date=current,
            portfolio={},
            price_data=price_data,
            initial_cash=100000,
        )

        # Should have queried for previous day, not current day
        call_args = mock_get_preds.call_args
        queried_date = call_args[0][3]  # 4th positional arg is prediction_date
        expected_date = current - timedelta(days=1)
        assert queried_date == expected_date, \
            f"Expected to query {expected_date}, got {queried_date}"

    def test_has_prediction_source_parameter(self):
        """Strategy should have prediction_source parameter."""
        from g2.strategies.ml_signal import MLSignalStrategy

        strategy = MLSignalStrategy(prediction_source="live")
        assert strategy.prediction_source == "live"

        strategy = MLSignalStrategy(prediction_source="database")
        assert strategy.prediction_source == "database"


class TestMLSignalLivePredictions:
    """Test on-the-fly prediction computation."""

    def test_live_mode_computes_predictions(self):
        """Live mode should compute predictions from price data."""
        from g2.strategies.ml_signal import MLSignalStrategy
        from unittest.mock import MagicMock, patch
        import numpy as np

        # Create strategy with mock model
        strategy = MLSignalStrategy(
            model_name="test",
            model_version="v1",
            prediction_source="live",
            return_threshold=0.01,  # Lower threshold so q50=0.05 passes
        )

        # Mock the model loading and feature computation
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([[0.02, 0.05, 0.10]])  # q10, q50, q90
        strategy._loaded_model = mock_model
        strategy._model_loaded = True

        # Mock the feature computation to return valid features
        with patch.object(strategy, '_compute_features_from_prices') as mock_features:
            mock_features.return_value = [0.01, 0.02, 0.03, 0.02, 0.5, 0.01, 0.02]

            # Price data with enough history
            price_data = {
                "AAPL": [
                    {"date": date(2024, 1, i), "close": 100.0 + i, "volume": 1000000}
                    for i in range(1, 31)  # 30 days of data
                ],
            }

            signals = strategy.generate_signals(
                current_date=date(2024, 1, 30),
                portfolio={},
                price_data=price_data,
                initial_cash=100000,
            )

            # Model should have been called
            assert mock_model.predict.called
            # Should generate a buy signal since q50=0.05 > threshold=0.01
            assert len(signals) >= 1

    def test_live_mode_uses_point_in_time_data(self):
        """Live mode should only use data up to current_date (no look-ahead)."""
        from g2.strategies.ml_signal import MLSignalStrategy
        from unittest.mock import MagicMock, patch

        strategy = MLSignalStrategy(
            model_name="test",
            model_version="v1",
            prediction_source="live",
        )

        strategy._loaded_model = MagicMock()
        strategy._model_loaded = True

        # Track which dates were used in feature computation
        dates_used = []

        def capture_features(price_history):
            dates_used.extend([p["date"] for p in price_history])
            return None  # Return None to skip this symbol

        with patch.object(strategy, '_compute_features_from_prices', side_effect=capture_features):
            price_data = {
                "AAPL": [
                    {"date": date(2024, 1, i), "close": 100.0}
                    for i in range(1, 31)
                ],
            }

            strategy.generate_signals(
                current_date=date(2024, 1, 15),  # Mid-month
                portfolio={},
                price_data=price_data,
                initial_cash=100000,
            )

            # Should only have used dates up to Jan 15
            assert all(d <= date(2024, 1, 15) for d in dates_used)
