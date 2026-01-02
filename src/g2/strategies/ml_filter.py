"""
ML Filter Strategy - Hybrid Approach.

Wraps an existing trading strategy and filters its signals using ML predictions.
This combines traditional technical analysis with ML-based validation.

Use cases:
- Filter momentum signals through ML to avoid false breakouts
- Confirm mean reversion entries with ML upside prediction
- Veto trades with poor ML outlook while keeping strategy logic
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional, Type

from g2.strategies.ml_signal import get_predictions_for_date, get_classifier_predictions_for_date


class MLFilterStrategy:
    """
    Hybrid strategy that filters signals from a base strategy using ML predictions.

    Signal filtering logic:
    - BUY signals: Only pass through if ML confirms upside potential
    - SELL signals: Always pass through (don't block exits)

    This preserves the base strategy's entry logic while adding ML-based
    confirmation to reduce false signals.
    """

    def __init__(
        self,
        base_strategy: Any,
        model_name: str = "quantile",
        model_version: str = "latest",
        horizon_days: int = 7,
        filter_mode: str = "confirm",  # "confirm" or "veto"
        prediction_type: str = "quantile",
        # Quantile filter params
        min_q50: float = 0.0,  # Require positive expected return
        max_q10: float = -0.10,  # Block if q10 below this
        # Classifier filter params
        allowed_classes: Optional[List[str]] = None,
        min_confidence: float = 0.4,
        # Database
        db_url: Optional[str] = None,
    ):
        """
        Initialize ML Filter Strategy.

        Args:
            base_strategy: The underlying strategy instance to filter
            model_name: ML model name for predictions
            model_version: ML model version
            horizon_days: Prediction horizon (7, 30, 90)
            filter_mode: "confirm" (require positive ML) or "veto" (block negative ML)
            prediction_type: "quantile" or "classifier"
            min_q50: Minimum q50 to allow buy (quantile mode)
            max_q10: Block if q10 below this threshold (quantile mode)
            allowed_classes: Classes that allow buy (classifier mode)
            min_confidence: Minimum probability for class (classifier mode)
            db_url: Database connection URL
        """
        self.base_strategy = base_strategy
        self.model_name = model_name
        self.model_version = model_version
        self.horizon_days = horizon_days
        self.filter_mode = filter_mode
        self.prediction_type = prediction_type

        # Quantile params
        self.min_q50 = min_q50
        self.max_q10 = max_q10

        # Classifier params
        self.allowed_classes = allowed_classes or ["strong_up", "weak_up", "neutral"]
        self.min_confidence = min_confidence

        # Database
        self.db_url = db_url or os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

    def generate_signals(
        self,
        current_date: date,
        portfolio: Any,
        price_data: Dict[str, List[Dict[str, Any]]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate filtered signals.

        1. Get signals from base strategy
        2. Filter buy signals through ML predictions
        3. Pass through sell signals unchanged

        Args:
            current_date: Current backtest date
            portfolio: Current portfolio
            price_data: Historical price data
            initial_cash: Initial capital

        Returns:
            Filtered list of signals
        """
        # Get base strategy signals
        base_signals = self.base_strategy.generate_signals(
            current_date=current_date,
            portfolio=portfolio,
            price_data=price_data,
            initial_cash=initial_cash,
        )

        if not base_signals:
            return []

        # Get ML predictions for filtering
        if self.prediction_type == "classifier":
            predictions = get_classifier_predictions_for_date(
                self.db_url,
                self.model_name,
                self.model_version,
                current_date,
                self.horizon_days,
            )
        else:
            predictions = get_predictions_for_date(
                self.db_url,
                self.model_name,
                self.model_version,
                current_date,
                self.horizon_days,
            )

        # Filter signals
        filtered = []
        for signal in base_signals:
            # Always pass through sell signals
            if signal["action"] == "sell":
                filtered.append(signal)
                continue

            # Filter buy signals through ML
            symbol = signal["symbol"]

            if symbol not in predictions:
                # No prediction available - use filter mode to decide
                if self.filter_mode == "confirm":
                    # Confirm mode: require prediction, skip if missing
                    continue
                else:
                    # Veto mode: allow if no prediction to veto it
                    filtered.append(signal)
                    continue

            pred = predictions[symbol]

            # Apply filter based on prediction type
            if self.prediction_type == "classifier":
                passes = self._passes_classifier_filter(pred)
            else:
                passes = self._passes_quantile_filter(pred)

            if passes:
                # Add ML info to reason
                reason = signal.get("reason", "")
                ml_info = self._format_ml_info(pred)
                signal = {**signal, "reason": f"{reason} [ML: {ml_info}]"}
                filtered.append(signal)

        return filtered

    def _passes_quantile_filter(self, pred: Dict[str, float]) -> bool:
        """Check if prediction passes quantile filter."""
        q50 = pred.get("q50", 0)
        q10 = pred.get("q10", 0)

        if self.filter_mode == "confirm":
            # Confirm mode: require positive expected return
            return q50 >= self.min_q50 and q10 >= self.max_q10
        else:
            # Veto mode: only block if clearly negative
            return q10 >= self.max_q10

    def _passes_classifier_filter(self, pred: Dict[str, Any]) -> bool:
        """Check if prediction passes classifier filter."""
        predicted_class = pred.get("predicted_class", "neutral")
        class_prob = pred.get(f"p_{predicted_class}", 0)

        if self.filter_mode == "confirm":
            # Confirm mode: require bullish class with confidence
            return (
                predicted_class in self.allowed_classes
                and class_prob >= self.min_confidence
            )
        else:
            # Veto mode: block only bearish with confidence
            bearish = ["strong_down", "weak_down"]
            if predicted_class in bearish and class_prob >= self.min_confidence:
                return False
            return True

    def _format_ml_info(self, pred: Dict[str, Any]) -> str:
        """Format ML prediction for signal reason."""
        if self.prediction_type == "classifier":
            cls = pred.get("predicted_class", "?")
            prob = pred.get(f"p_{cls}", 0)
            return f"{cls}:{prob:.0%}"
        else:
            q50 = pred.get("q50", 0)
            return f"q50={q50:.1%}"


def create_ml_filtered_strategy(
    base_strategy_class: Type,
    base_params: Dict[str, Any],
    ml_params: Dict[str, Any],
) -> MLFilterStrategy:
    """
    Factory function to create an ML-filtered strategy.

    Args:
        base_strategy_class: The strategy class to wrap
        base_params: Parameters for the base strategy
        ml_params: Parameters for ML filtering

    Returns:
        MLFilterStrategy instance

    Example:
        from g2.strategies.momentum import MomentumStrategy
        from g2.strategies.ml_filter import create_ml_filtered_strategy

        filtered = create_ml_filtered_strategy(
            base_strategy_class=MomentumStrategy,
            base_params={"lookback_days": 20, "top_n": 5},
            ml_params={
                "model_name": "quantile_v1",
                "model_version": "20260101",
                "filter_mode": "confirm",
                "min_q50": 0.01,
            },
        )
    """
    base_strategy = base_strategy_class(**base_params)
    return MLFilterStrategy(base_strategy=base_strategy, **ml_params)
