"""
ML Signal Strategy.

Generates trading signals based on ML model predictions (quantile regression
or trend classifier). Supports two prediction sources:

1. "database" - Uses pre-computed predictions stored in the database
   (queries previous day's predictions to avoid look-ahead bias)

2. "live" - Computes predictions on-the-fly using loaded model artifacts
   (simpler workflow, no need to pre-generate predictions)

This bridges the ML pipeline with the backtesting system.
"""
from __future__ import annotations

import os
import logging
from datetime import date, timedelta
from pathlib import Path
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
    except Exception as e:
        logger.debug(f"Error fetching classifier predictions: {e}")
        pass

    return predictions


class MLSignalStrategy:
    """
    Trading strategy based on ML model predictions.

    Supports two prediction sources:
    - "database": Uses stored predictions (queries previous day to avoid look-ahead)
    - "live": Computes predictions on-the-fly from price data

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
        prediction_source: str = "database",  # "database" or "live"
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
        # Model path for live predictions
        model_path: Optional[str] = None,
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
            prediction_source: "database" (stored predictions) or "live" (compute on-the-fly)
            return_threshold: Min expected return for buy (quantile mode)
            downside_limit: Max acceptable q10 downside (quantile mode)
            trend_classes: Classes that trigger buy (classifier mode)
            confidence_threshold: Min probability for action (classifier mode)
            position_size: Fraction of capital per position (0-1)
            max_positions: Maximum concurrent positions
            rebalance_days: Days between signal evaluation
            model_path: Path to model artifacts (for live predictions)
            db_url: Database connection URL
        """
        self.model_name = model_name
        self.model_version = model_version
        self.horizon_days = horizon_days
        self.prediction_type = prediction_type
        self.prediction_source = prediction_source

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

        # Model for live predictions
        self.model_path = model_path
        self._loaded_model = None
        self._model_loaded = False
        self._feature_names: Optional[List[str]] = None

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

        # Get predictions (from database or computed live)
        if self.prediction_source == "live":
            predictions = self._compute_live_predictions(price_data, current_date)
        else:
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

    def _compute_live_predictions(
        self,
        price_data: Dict[str, List[Dict[str, Any]]],
        current_date: date,
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute predictions on-the-fly from price data.

        Uses loaded model artifacts to generate fresh predictions.
        This avoids needing to pre-generate predictions for all historical dates.
        """
        # Lazy load model if not already loaded
        if not self._model_loaded:
            self._load_model()

        if self._loaded_model is None:
            logger.warning("No model loaded for live predictions")
            return {}

        predictions = {}

        try:
            for symbol, history in price_data.items():
                # Get data up to current date (point-in-time correct)
                relevant = [p for p in history if p["date"] <= current_date]
                if len(relevant) < 20:  # Need minimum history for features
                    continue

                # Compute features from price history
                features = self._compute_features_from_prices(relevant)
                if features is None:
                    continue

                # Generate prediction
                pred = self._loaded_model.predict([features])

                if self.prediction_type == "quantile":
                    # Assume model outputs [q10, q50, q90]
                    if len(pred[0]) >= 3:
                        predictions[symbol] = {
                            "q10": float(pred[0][0]),
                            "q50": float(pred[0][1]),
                            "q90": float(pred[0][2]),
                        }
                else:
                    # Classifier output
                    classes = ["strong_down", "weak_down", "neutral", "weak_up", "strong_up"]
                    probs = pred[0] if hasattr(pred[0], '__iter__') else [pred[0]]
                    max_idx = max(range(len(probs)), key=lambda i: probs[i])
                    predictions[symbol] = {
                        "predicted_class": classes[max_idx],
                        **{f"p_{c}": float(probs[i]) for i, c in enumerate(classes) if i < len(probs)},
                        "margin": float(max(probs) - sorted(probs)[-2]) if len(probs) > 1 else 1.0,
                    }

        except Exception as e:
            logger.warning(f"Error computing live predictions: {e}")

        return predictions

    def _load_model(self) -> None:
        """Load model artifacts for live predictions."""
        try:
            import joblib

            # Determine model path
            if self.model_path:
                model_dir = Path(self.model_path)
            else:
                # Default path based on model name/version
                model_dir = Path("models") / f"{self.model_name}_{self.model_version}_h{self.horizon_days}"

            model_file = model_dir / "model.joblib"
            metadata_file = model_dir / "metadata.json"

            if model_file.exists():
                self._loaded_model = joblib.load(model_file)
                logger.info(f"Loaded model from {model_file}")

                # Load feature names if available
                if metadata_file.exists():
                    import json
                    with open(metadata_file) as f:
                        metadata = json.load(f)
                        self._feature_names = metadata.get("feature_names", [])

                self._model_loaded = True
            else:
                logger.warning(f"Model file not found: {model_file}")

        except Exception as e:
            logger.warning(f"Failed to load model: {e}")
            self._model_loaded = True  # Don't retry

    def _compute_features_from_prices(
        self,
        price_history: List[Dict[str, Any]],
    ) -> Optional[List[float]]:
        """
        Compute basic features from price history for live predictions.

        This is a simplified feature set for demonstration.
        For production, use the full feature computation pipeline.
        """
        try:
            import numpy as np

            # Sort by date
            sorted_prices = sorted(price_history, key=lambda x: x["date"])
            closes = np.array([p["close"] for p in sorted_prices])

            if len(closes) < 20:
                return None

            # Basic features (simplified)
            features = []

            # Returns
            returns_1d = (closes[-1] - closes[-2]) / closes[-2]
            returns_5d = (closes[-1] - closes[-5]) / closes[-5] if len(closes) >= 5 else 0
            returns_20d = (closes[-1] - closes[-20]) / closes[-20] if len(closes) >= 20 else 0
            features.extend([returns_1d, returns_5d, returns_20d])

            # Volatility
            if len(closes) >= 20:
                volatility = np.std(np.diff(closes[-20:]) / closes[-21:-1])
            else:
                volatility = 0
            features.append(volatility)

            # Simple RSI approximation
            if len(closes) >= 14:
                gains = np.maximum(np.diff(closes[-15:]), 0)
                losses = np.maximum(-np.diff(closes[-15:]), 0)
                avg_gain = np.mean(gains)
                avg_loss = np.mean(losses)
                rs = avg_gain / avg_loss if avg_loss > 0 else 100
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 50
            features.append(rsi / 100)  # Normalize

            # Moving average ratios
            ma_5 = np.mean(closes[-5:]) if len(closes) >= 5 else closes[-1]
            ma_20 = np.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
            features.append(closes[-1] / ma_5 - 1)
            features.append(closes[-1] / ma_20 - 1)

            return features

        except Exception as e:
            logger.debug(f"Feature computation error: {e}")
            return None

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
