"""
ML Signal Strategy.

Generates trading signals based on ML model predictions (quantile regression
or trend classifier) stored in the database.

Look-ahead bias protection:
- Queries predictions from PREVIOUS day (D-1) to avoid look-ahead
- Predictions generated on day D are only used for trading on day D+1

This bridges the ML pipeline with the backtesting system.
"""
from __future__ import annotations

import os
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import psycopg

logger = logging.getLogger(__name__)


def get_predictions_for_date(
    db_url: str,
    model_name: str,
    model_version: str,
    prediction_date: date,
    horizon_days: int,
) -> Dict[str, Dict[str, float]]:
    """
    Fetch quantile predictions for a specific date.

    Args:
        db_url: Database connection URL
        model_name: Model name in ml_models table
        model_version: Model version
        prediction_date: Date predictions were GENERATED (not used)
        horizon_days: Prediction horizon (7, 30, 90)

    Returns:
        Dict mapping symbol -> {q10, q50, q90}
    """
    predictions = {}

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.symbol,
                           (p.prediction_values->>'q10')::NUMERIC,
                           (p.prediction_values->>'q50')::NUMERIC,
                           (p.prediction_values->>'q90')::NUMERIC
                    FROM predictions p
                    JOIN stocks s ON p.data_id = s.id
                    JOIN ml_models m ON p.model_id = m.id
                    WHERE p.prediction_type = 'quantile'
                      AND m.name = %s
                      AND m.version = %s
                      AND p.prediction_date = %s
                      AND p.horizon_days = %s
                      AND m.active = TRUE
                    ORDER BY (p.prediction_values->>'q50')::NUMERIC DESC
                    """,
                    (model_name, model_version, prediction_date, horizon_days),
                )

                for symbol, q10, q50, q90 in cur.fetchall():
                    predictions[symbol] = {
                        "q10": float(q10) if q10 else 0.0,
                        "q50": float(q50) if q50 else 0.0,
                        "q90": float(q90) if q90 else 0.0,
                    }
    except Exception as e:
        logger.debug(f"Error fetching predictions: {e}")
        pass

    return predictions


def get_classifier_predictions_for_date(
    db_url: str,
    model_name: str,
    model_version: str,
    prediction_date: date,
    horizon_days: int,
) -> Dict[str, Dict[str, Any]]:
    """
    Fetch classifier predictions for a specific date.

    Args:
        db_url: Database connection URL
        model_name: Model name in ml_models table
        model_version: Model version
        prediction_date: Date predictions were GENERATED
        horizon_days: Prediction horizon (7, 30, 90)

    Returns:
        Dict mapping symbol -> {predicted_class, p_strong_up, ..., margin}
    """
    predictions = {}

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.symbol,
                           p.prediction_values->>'predicted_class',
                           (p.prediction_values->>'p_strong_up')::NUMERIC,
                           (p.prediction_values->>'p_weak_up')::NUMERIC,
                           (p.prediction_values->>'p_neutral')::NUMERIC,
                           (p.prediction_values->>'p_weak_down')::NUMERIC,
                           (p.prediction_values->>'p_strong_down')::NUMERIC,
                           (p.prediction_values->>'margin')::NUMERIC
                    FROM predictions p
                    JOIN stocks s ON p.data_id = s.id
                    JOIN ml_models m ON p.model_id = m.id
                    WHERE p.prediction_type = 'trend_class'
                      AND m.name = %s
                      AND m.version = %s
                      AND p.prediction_date = %s
                      AND p.horizon_days = %s
                      AND m.active = TRUE
                    ORDER BY (p.prediction_values->>'margin')::NUMERIC DESC
                    """,
                    (model_name, model_version, prediction_date, horizon_days),
                )

                for row in cur.fetchall():
                    symbol = row[0]
                    predictions[symbol] = {
                        "predicted_class": row[1],
                        "p_strong_up": float(row[2]) if row[2] else 0.0,
                        "p_weak_up": float(row[3]) if row[3] else 0.0,
                        "p_neutral": float(row[4]) if row[4] else 0.0,
                        "p_weak_down": float(row[5]) if row[5] else 0.0,
                        "p_strong_down": float(row[6]) if row[6] else 0.0,
                        "margin": float(row[7]) if row[7] else 0.0,
                    }
    except Exception as e:
        logger.debug(f"Error fetching classifier predictions: {e}")
        pass

    return predictions


class MLSignalStrategy:
    """
    Trading strategy based on ML model predictions.

    Uses predictions stored in the database. Queries PREVIOUS day's predictions
    to avoid look-ahead bias (predictions made on day D are used on day D+1).

    Supports two prediction types:
    - "quantile": Uses quantile regression predictions (q10, q50, q90)
    - "classifier": Uses trend class predictions (strong_up, weak_up, etc.)
    """

    def __init__(
        self,
        model_name: str = "quantile",
        model_version: str = "latest",
        horizon_days: int = 7,
        prediction_type: str = "quantile",
        # Quantile strategy params
        return_threshold: float = 0.02,
        downside_limit: float = -0.05,
        # Classifier strategy params
        trend_classes: Optional[List[str]] = None,
        confidence_threshold: float = 0.5,
        # Position management
        position_size: float = 0.1,
        max_positions: int = 10,
        rebalance_days: int = 1,
        # Database
        db_url: Optional[str] = None,
        # Deprecated - kept for backwards compatibility
        prediction_source: str = "database",
        model_path: Optional[str] = None,
    ):
        """
        Initialize ML Signal Strategy.

        Args:
            model_name: Name of ML model in database
            model_version: Version of model to use
            horizon_days: Prediction horizon (7, 30, 90 days)
            prediction_type: "quantile" or "classifier"
            return_threshold: Min expected return for buy (quantile mode)
            downside_limit: Max acceptable q10 downside (quantile mode)
            trend_classes: Classes that trigger buy (classifier mode)
            confidence_threshold: Min probability for action (classifier mode)
            position_size: Fraction of capital per position (0-1)
            max_positions: Maximum concurrent positions
            rebalance_days: Days between signal evaluation
            db_url: Database connection URL
        """
        self.model_name = model_name
        self.model_version = model_version
        self.horizon_days = horizon_days
        self.prediction_type = prediction_type

        # Quantile params
        self.return_threshold = return_threshold
        self.downside_limit = downside_limit

        # Classifier params
        self.trend_classes = trend_classes or ["strong_up", "weak_up"]
        self.confidence_threshold = confidence_threshold

        # Position management
        self.position_size = position_size
        self.max_positions = max_positions
        self.rebalance_days = rebalance_days

        # Database
        self.db_url = db_url or os.environ.get(
            "DATABASE_URL",
            "postgresql://gefion:gefionpass@localhost:6432/gefion"
        )

        # Backwards compatibility - ignore these params but accept them
        self.prediction_source = "database"  # Always database
        _ = prediction_source, model_path  # Suppress unused warnings

        # State
        self.last_signal_date: Optional[date] = None

    def generate_signals(
        self,
        current_date: date,
        portfolio: Any,
        price_data: Dict[str, List[Dict[str, Any]]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate buy/sell signals based on ML predictions.

        Args:
            current_date: Current backtest date
            portfolio: Current portfolio (dict or Portfolio object)
            price_data: Dict mapping symbol -> list of price records
            initial_cash: Initial capital

        Returns:
            List of signal dicts with {action, symbol, shares, reason}
        """
        # Check if we should generate signals today
        if not self._should_evaluate(current_date):
            return []

        # Get current prices
        current_prices = self._get_current_prices(price_data, current_date)
        if not current_prices:
            return []

        # Get current positions
        positions = self._get_positions(portfolio)

        # Get predictions from database (using D-1 to avoid look-ahead)
        predictions = self._get_database_predictions(current_date)

        if not predictions:
            return []

        # Generate signals based on prediction type
        if self.prediction_type == "classifier":
            signals = self._generate_classifier_signals_from_predictions(
                predictions, positions, current_prices, initial_cash
            )
        else:
            signals = self._generate_quantile_signals_from_predictions(
                predictions, positions, current_prices, initial_cash
            )

        # Update state
        self.last_signal_date = current_date

        return signals

    def _get_database_predictions(self, current_date: date) -> Dict[str, Dict[str, Any]]:
        """
        Get predictions from database.

        IMPORTANT: Queries PREVIOUS day's predictions to avoid look-ahead bias.
        Predictions generated on day D should only be used for trading on day D+1.
        """
        # Use previous day's predictions to avoid look-ahead bias
        prediction_date = current_date - timedelta(days=1)

        if self.prediction_type == "classifier":
            return get_classifier_predictions_for_date(
                self.db_url,
                self.model_name,
                self.model_version,
                prediction_date,
                self.horizon_days,
            )
        else:
            return get_predictions_for_date(
                self.db_url,
                self.model_name,
                self.model_version,
                prediction_date,
                self.horizon_days,
            )

    def _generate_quantile_signals_from_predictions(
        self,
        predictions: Dict[str, Dict[str, float]],
        positions: Dict[str, int],
        current_prices: Dict[str, float],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """Generate signals using quantile predictions."""
        signals = []

        # Generate sell signals for held positions with bad outlook
        for symbol, shares in positions.items():
            if symbol in predictions:
                pred = predictions[symbol]
                if pred["q50"] < -self.return_threshold:
                    signals.append({
                        "action": "sell",
                        "symbol": symbol,
                        "shares": shares,
                        "reason": f"negative outlook (q50={pred['q50']:.2%})",
                    })

        # Find buy candidates
        buy_candidates = []
        for symbol, pred in predictions.items():
            if symbol not in current_prices:
                continue
            if symbol in positions:
                continue

            if pred["q50"] >= self.return_threshold:
                if pred["q10"] >= self.downside_limit:
                    buy_candidates.append({
                        "symbol": symbol,
                        "q50": pred["q50"],
                        "q10": pred["q10"],
                        "q90": pred.get("q90", pred["q50"]),
                    })

        buy_candidates.sort(key=lambda x: x["q50"], reverse=True)

        available_slots = self.max_positions - len(positions) + len(
            [s for s in signals if s["action"] == "sell"]
        )

        for candidate in buy_candidates[:available_slots]:
            symbol = candidate["symbol"]
            price = current_prices[symbol]
            position_value = initial_cash * self.position_size
            shares = int(position_value / price)

            if shares > 0:
                signals.append({
                    "action": "buy",
                    "symbol": symbol,
                    "shares": shares,
                    "reason": f"ML signal (q50={candidate['q50']:.2%}, q10={candidate['q10']:.2%})",
                })

        return signals

    def _generate_classifier_signals_from_predictions(
        self,
        predictions: Dict[str, Dict[str, Any]],
        positions: Dict[str, int],
        current_prices: Dict[str, float],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """Generate signals using classifier predictions."""
        signals = []
        bearish_classes = ["strong_down", "weak_down"]

        for symbol, shares in positions.items():
            if symbol in predictions:
                pred = predictions[symbol]
                if pred["predicted_class"] in bearish_classes:
                    prob = pred.get(f"p_{pred['predicted_class']}", 0)
                    if prob >= self.confidence_threshold:
                        signals.append({
                            "action": "sell",
                            "symbol": symbol,
                            "shares": shares,
                            "reason": f"bearish ({pred['predicted_class']}, p={prob:.2%})",
                        })

        buy_candidates = []
        for symbol, pred in predictions.items():
            if symbol not in current_prices:
                continue
            if symbol in positions:
                continue

            if pred["predicted_class"] in self.trend_classes:
                prob = pred.get(f"p_{pred['predicted_class']}", 0)
                if prob >= self.confidence_threshold:
                    buy_candidates.append({
                        "symbol": symbol,
                        "class": pred["predicted_class"],
                        "probability": prob,
                        "margin": pred.get("margin", 0),
                    })

        buy_candidates.sort(key=lambda x: x["margin"], reverse=True)

        available_slots = self.max_positions - len(positions) + len(
            [s for s in signals if s["action"] == "sell"]
        )

        for candidate in buy_candidates[:available_slots]:
            symbol = candidate["symbol"]
            price = current_prices[symbol]
            position_value = initial_cash * self.position_size
            shares = int(position_value / price)

            if shares > 0:
                signals.append({
                    "action": "buy",
                    "symbol": symbol,
                    "shares": shares,
                    "reason": f"ML classifier ({candidate['class']}, p={candidate['probability']:.2%})",
                })

        return signals

    def _should_evaluate(self, current_date: date) -> bool:
        """Check if we should evaluate signals today."""
        if self.last_signal_date is None:
            return True
        days_since = (current_date - self.last_signal_date).days
        return days_since >= self.rebalance_days

    def _get_current_prices(
        self, price_data: Dict[str, List[Dict[str, Any]]], current_date: date
    ) -> Dict[str, float]:
        """Extract current prices from price data."""
        prices = {}
        for symbol, history in price_data.items():
            relevant = [p for p in history if p["date"] <= current_date]
            if relevant:
                prices[symbol] = relevant[-1]["close"]
        return prices

    def _get_positions(self, portfolio: Any) -> Dict[str, int]:
        """Extract positions from portfolio (handles dict or Portfolio object)."""
        if isinstance(portfolio, dict):
            return {
                sym: pos.get("shares", 0) if isinstance(pos, dict) else pos
                for sym, pos in portfolio.items()
            }
        elif hasattr(portfolio, "positions"):
            return {
                sym: pos.shares if hasattr(pos, "shares") else pos
                for sym, pos in portfolio.positions.items()
            }
        return {}
