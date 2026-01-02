"""
ML Signal Strategy.

Generates trading signals based on ML model predictions (quantile regression
or trend classifier). Uses predictions stored in the database to make
buy/sell decisions.

This bridges the ML pipeline with the backtesting system.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

import psycopg


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
        prediction_date: Date to get predictions for
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
                    SELECT s.symbol, qp.q10, qp.q50, qp.q90
                    FROM quantile_predictions qp
                    JOIN stocks s ON qp.data_id = s.id
                    JOIN ml_models m ON qp.model_id = m.id
                    WHERE m.name = %s
                      AND m.version = %s
                      AND qp.prediction_date = %s
                      AND qp.horizon_days = %s
                      AND m.active = TRUE
                    ORDER BY qp.q50 DESC
                    """,
                    (model_name, model_version, prediction_date, horizon_days),
                )

                for symbol, q10, q50, q90 in cur.fetchall():
                    predictions[symbol] = {
                        "q10": float(q10) if q10 else 0.0,
                        "q50": float(q50) if q50 else 0.0,
                        "q90": float(q90) if q90 else 0.0,
                    }
    except Exception:
        # Return empty if DB error - strategy will generate no signals
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
        prediction_date: Date to get predictions for
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
                    SELECT s.symbol, tcp.predicted_class,
                           tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral,
                           tcp.p_weak_down, tcp.p_strong_down, tcp.margin
                    FROM trend_class_predictions tcp
                    JOIN stocks s ON tcp.data_id = s.id
                    JOIN ml_models m ON tcp.model_id = m.id
                    WHERE m.name = %s
                      AND m.version = %s
                      AND tcp.prediction_date = %s
                      AND tcp.horizon_days = %s
                      AND m.active = TRUE
                    ORDER BY tcp.margin DESC
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
    except Exception:
        pass

    return predictions


class MLSignalStrategy:
    """
    Trading strategy based on ML model predictions.

    Supports two prediction types:
    - "quantile": Uses quantile regression predictions (q10, q50, q90)
      Buys when q50 > return_threshold, sells when q50 < -return_threshold

    - "classifier": Uses trend class predictions (strong_up, weak_up, etc.)
      Buys when predicted_class in trend_classes and confidence > threshold

    This strategy connects the ML pipeline outputs to actual trading decisions.
    """

    def __init__(
        self,
        model_name: str = "quantile",
        model_version: str = "latest",
        horizon_days: int = 7,
        prediction_type: str = "quantile",
        # Quantile strategy params
        return_threshold: float = 0.02,
        downside_limit: float = -0.05,  # Max acceptable q10
        # Classifier strategy params
        trend_classes: Optional[List[str]] = None,
        confidence_threshold: float = 0.5,
        # Position management
        position_size: float = 0.1,
        max_positions: int = 10,
        rebalance_days: int = 1,
        # Database
        db_url: Optional[str] = None,
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
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

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

        # Generate signals based on prediction type
        if self.prediction_type == "classifier":
            signals = self._generate_classifier_signals(
                current_date, positions, current_prices, initial_cash
            )
        else:
            signals = self._generate_quantile_signals(
                current_date, positions, current_prices, initial_cash
            )

        # Update state
        self.last_signal_date = current_date

        return signals

    def _generate_quantile_signals(
        self,
        current_date: date,
        positions: Dict[str, int],
        current_prices: Dict[str, float],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """Generate signals using quantile predictions."""
        signals = []

        # Get predictions
        predictions = get_predictions_for_date(
            self.db_url,
            self.model_name,
            self.model_version,
            current_date,
            self.horizon_days,
        )

        if not predictions:
            return []

        # Generate sell signals for held positions with bad outlook
        for symbol, shares in positions.items():
            if symbol in predictions:
                pred = predictions[symbol]
                # Sell if expected return is negative
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
                continue  # Already held

            # Buy criteria: q50 above threshold and q10 above downside limit
            if pred["q50"] >= self.return_threshold:
                if pred["q10"] >= self.downside_limit:
                    buy_candidates.append({
                        "symbol": symbol,
                        "q50": pred["q50"],
                        "q10": pred["q10"],
                        "q90": pred["q90"],
                    })

        # Sort by expected return and take top candidates
        buy_candidates.sort(key=lambda x: x["q50"], reverse=True)

        # Respect position limits
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

    def _generate_classifier_signals(
        self,
        current_date: date,
        positions: Dict[str, int],
        current_prices: Dict[str, float],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """Generate signals using classifier predictions."""
        signals = []

        # Get predictions
        predictions = get_classifier_predictions_for_date(
            self.db_url,
            self.model_name,
            self.model_version,
            current_date,
            self.horizon_days,
        )

        if not predictions:
            return []

        # Define bearish classes for sell signals
        bearish_classes = ["strong_down", "weak_down"]

        # Generate sell signals for held positions with bearish outlook
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

        # Find buy candidates
        buy_candidates = []
        for symbol, pred in predictions.items():
            if symbol not in current_prices:
                continue
            if symbol in positions:
                continue

            # Buy if predicted class is bullish with high confidence
            if pred["predicted_class"] in self.trend_classes:
                prob = pred.get(f"p_{pred['predicted_class']}", 0)
                if prob >= self.confidence_threshold:
                    buy_candidates.append({
                        "symbol": symbol,
                        "class": pred["predicted_class"],
                        "probability": prob,
                        "margin": pred.get("margin", 0),
                    })

        # Sort by margin (confidence) and take top candidates
        buy_candidates.sort(key=lambda x: x["margin"], reverse=True)

        # Respect position limits
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
