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
        from gefion.strategies.ml_signal import MLSignalStrategy
        assert MLSignalStrategy is not None

    def test_strategy_has_required_params(self):
        """Strategy should accept model selection parameters."""
        from gefion.strategies.ml_signal import MLSignalStrategy

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
        from gefion.strategies.ml_signal import MLSignalStrategy

        strategy = MLSignalStrategy()
        assert hasattr(strategy, "generate_signals")
        assert callable(strategy.generate_signals)

    def test_strategy_supports_classifier_mode(self):
        """Strategy should support classifier prediction mode."""
        from gefion.strategies.ml_signal import MLSignalStrategy

        strategy = MLSignalStrategy(
            prediction_type="classifier",
            trend_classes=["strong_up", "weak_up"],
            confidence_threshold=0.6,
        )

        assert strategy.prediction_type == "classifier"
        assert "strong_up" in strategy.trend_classes


class TestMLSignalStrategySignals:
    """Test signal generation logic."""

    @patch("gefion.strategies.ml_signal.get_predictions_for_date")
    def test_generates_buy_signal_for_positive_prediction(self, mock_get_preds):
        """Should generate buy signal when q50 exceeds threshold."""
        from gefion.strategies.ml_signal import MLSignalStrategy

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

    @patch("gefion.strategies.ml_signal.get_predictions_for_date")
    def test_generates_sell_signal_for_negative_prediction(self, mock_get_preds):
        """Should generate sell signal when q50 is below negative threshold."""
        from gefion.strategies.ml_signal import MLSignalStrategy

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

    @patch("gefion.strategies.ml_signal.get_predictions_for_date")
    def test_respects_max_positions(self, mock_get_preds):
        """Should not exceed max_positions limit."""
        from gefion.strategies.ml_signal import MLSignalStrategy

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

    @patch("gefion.strategies.ml_signal.get_predictions_for_date")
    def test_no_signals_when_no_predictions(self, mock_get_preds):
        """Should return empty list when no predictions available."""
        from gefion.strategies.ml_signal import MLSignalStrategy

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

    @patch("gefion.strategies.ml_signal.get_classifier_predictions_for_date")
    def test_buys_on_strong_up_prediction(self, mock_get_preds):
        """Should buy when classifier predicts strong_up with high confidence."""
        from gefion.strategies.ml_signal import MLSignalStrategy

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


class TestMLSignalUnifiedPredictionsTable:
    """Test that ML signal queries use the unified predictions table."""

    def test_get_predictions_queries_unified_table(self):
        """get_predictions_for_date should query from 'predictions' table."""
        import inspect
        from gefion.strategies.ml_signal import get_predictions_for_date

        source = inspect.getsource(get_predictions_for_date)
        assert "FROM predictions " in source or "FROM predictions\n" in source
        assert "prediction_type = 'quantile'" in source
        assert "quantile_predictions" not in source

    def test_get_classifier_predictions_queries_unified_table(self):
        """get_classifier_predictions_for_date should query from 'predictions' table."""
        import inspect
        from gefion.strategies.ml_signal import get_classifier_predictions_for_date

        source = inspect.getsource(get_classifier_predictions_for_date)
        assert "FROM predictions " in source or "FROM predictions\n" in source
        assert "prediction_type = 'trend_class'" in source
        assert "trend_class_predictions" not in source

    def test_quantile_predictions_extracts_jsonb_fields(self):
        """get_predictions_for_date should extract q10/q50/q90 from JSONB."""
        import inspect
        from gefion.strategies.ml_signal import get_predictions_for_date

        source = inspect.getsource(get_predictions_for_date)
        assert "prediction_values" in source

    def test_classifier_predictions_extracts_jsonb_fields(self):
        """get_classifier_predictions_for_date should extract class/probs from JSONB."""
        import inspect
        from gefion.strategies.ml_signal import get_classifier_predictions_for_date

        source = inspect.getsource(get_classifier_predictions_for_date)
        assert "prediction_values" in source


class TestMLSignalStrategyRegistration:
    """Test strategy registration in dispatcher."""

    def test_ml_signal_in_builtin_strategies(self):
        """ml_signal should be registered in BUILTIN_STRATEGIES."""
        from gefion.strategies.dispatcher import BUILTIN_STRATEGIES

        assert "ml_signal" in BUILTIN_STRATEGIES
        assert BUILTIN_STRATEGIES["ml_signal"]["class_name"] == "MLSignalStrategy"

    def test_ml_signal_has_correct_default_params(self):
        """ml_signal should have sensible default parameters."""
        from gefion.strategies.dispatcher import BUILTIN_STRATEGIES

        defaults = BUILTIN_STRATEGIES["ml_signal"]["default_params"]

        # Should have these defaults
        assert "model_name" in defaults
        assert "horizon_days" in defaults
        assert "return_threshold" in defaults
        assert "max_positions" in defaults


class TestMLSignalLookAheadProtection:
    """Test that strategy avoids look-ahead bias."""

    @patch("gefion.strategies.ml_signal.get_predictions_for_date")
    def test_queries_previous_day_predictions(self, mock_get_preds):
        """Strategy should query predictions from PREVIOUS day to avoid look-ahead."""
        from gefion.strategies.ml_signal import MLSignalStrategy
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

    def test_prediction_source_always_database(self):
        """Strategy should always use database mode (live mode removed)."""
        from gefion.strategies.ml_signal import MLSignalStrategy

        # Even if live is passed, should use database
        strategy = MLSignalStrategy(prediction_source="live")
        assert strategy.prediction_source == "database"

        strategy = MLSignalStrategy(prediction_source="database")
        assert strategy.prediction_source == "database"

        # Default should be database
        strategy = MLSignalStrategy()
        assert strategy.prediction_source == "database"
