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

from gefion.observability import create_span, set_attributes

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
    with create_span("strategies.ml_signal.get_predictions_for_date", model_name=model_name, prediction_date=str(prediction_date), horizon_days=horizon_days) as span:
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

        set_attributes(span, result_count=len(predictions))
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
    with create_span("strategies.ml_signal.get_classifier_predictions_for_date", model_name=model_name, prediction_date=str(prediction_date), horizon_days=horizon_days) as span:
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

        set_attributes(span, result_count=len(predictions))
        return predictions


class MLSignalStrategy:
    """
    Trading strategy based on ML model predictions.

    Uses predictions stored in the database. Queries PREVIOUS day's predictions
    to avoid look-ahead bias (predictions made on day D are used on day D+1).

    Supports two prediction types:
    - "quantile": Uses quantile regression predictions (q10, q50, q90)
    - "classifier": Uses trend class predictions (strong_up, weak_up, etc.)

    Candidate selection ("selection" param, #220):
    - "absolute" (default): filters candidates by an absolute threshold on
      each side (return_threshold for quantile; confidence_threshold + class
      membership for classifier). A directionally-biased model can starve one
      side under this mode -- see #220.
    - "rank": requires only sign agreement -- q50 > 0 / q50 < 0 for quantile,
      class membership (trend_classes / bearish down-classes) for classifier
      -- and takes the top max_positions by conviction on each side (q50 for
      quantile; margin/probability for classifier), so the book is balanced
      by construction. return_threshold (quantile) and confidence_threshold
      (classifier) are not applied as entry gates in this mode -- sign
      agreement is the floor. Thin days under-fill rather than trading
      against the signal's own sign. The exit/sell check is unaffected by
      `selection` in both prediction paths.
    """

    def __init__(
        self,
        model_name: str = "quantile",
        model_version: str = "latest",
        horizon_days: int = 7,
        prediction_type: str = "quantile",
        # Quantile strategy params
        return_threshold: Optional[float] = None,
        downside_limit: float = -0.05,
        # Classifier strategy params
        trend_classes: Optional[List[str]] = None,
        confidence_threshold: float = 0.5,
        # Position management
        position_size: float = 0.1,
        max_positions: int = 10,
        rebalance_days: int = 1,
        mode: str = "long_only",
        # Candidate selection: "absolute" (default) filters candidates by an
        # absolute return_threshold cutoff on each side, which a directionally
        # biased model can starve one side of. "rank" instead requires only
        # sign agreement (q50 > 0 for longs, q50 < 0 for shorts) and takes the
        # top max_positions by conviction on each side -- balanced by
        # construction. See #220.
        selection: str = "absolute",
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
            return_threshold: Min expected return for buy (quantile mode). In
                "absolute" selection this is the entry cutoff (default 0.02).
                In "rank" selection it's an optional extra magnitude floor on
                top of the sign check (default 0.0 -- sign alone suffices).
            downside_limit: Max acceptable q10 downside (quantile mode);
                applies to longs in both selection modes
            trend_classes: Classes that trigger buy (classifier mode)
            confidence_threshold: Min probability for action (classifier
                mode). Applies to entries (buy/short candidates) and to the
                exit/sell check in "absolute" selection. In "rank" selection
                it still gates exits, but entries drop it -- class membership
                is the sign floor, and the top max_positions by margin/
                probability are taken per side.
            position_size: Fraction of capital per position (0-1)
            max_positions: Maximum concurrent positions per side
            rebalance_days: Days between signal evaluation
            mode: "long_only" or "long_short"
            selection: "absolute" (default, today's behavior) or "rank" --
                see class docstring / #220
            db_url: Database connection URL
        """
        self.model_name = model_name
        self.model_version = model_version
        self.horizon_days = horizon_days
        self.prediction_type = prediction_type
        self.selection = selection

        # Quantile params. return_threshold's default depends on selection
        # mode: 0.02 keeps "absolute" byte-identical to pre-#220 behavior;
        # "rank" defaults to 0.0 since the sign check is the floor. An
        # explicit value always wins.
        if return_threshold is None:
            return_threshold = 0.0 if selection == "rank" else 0.02
        self.return_threshold = return_threshold
        self.downside_limit = downside_limit

        # Classifier params
        self.trend_classes = trend_classes or ["strong_up", "weak_up"]
        self.confidence_threshold = confidence_threshold

        # Position management
        self.position_size = position_size
        self.max_positions = max_positions
        self.rebalance_days = rebalance_days
        self.mode = mode

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

        # Find buy candidates. downside_limit always applies to longs (#220:
        # "stays as-is"). The entry floor itself differs by selection mode:
        # "absolute" keeps the pre-#220 magnitude cutoff; "rank" requires only
        # sign agreement (q50 > 0) plus the optional return_threshold floor.
        buy_candidates = []
        for symbol, pred in predictions.items():
            if symbol not in current_prices:
                continue
            if symbol in positions:
                continue
            if pred["q10"] < self.downside_limit:
                continue

            if self.selection == "rank":
                qualifies = pred["q50"] > 0 and pred["q50"] >= self.return_threshold
            else:
                qualifies = pred["q50"] >= self.return_threshold

            if qualifies:
                buy_candidates.append({
                    "symbol": symbol,
                    "q50": pred["q50"],
                    "q10": pred["q10"],
                    "q90": pred.get("q90", pred["q50"]),
                })

        buy_candidates.sort(key=lambda x: x["q50"], reverse=True)

        # max_positions is enforced PER SIDE (#210): count only existing longs
        # against the long budget (a short must not steal a long slot), and take
        # the highest-conviction longs up to the cap.
        existing_longs = sum(1 for sh in positions.values() if sh > 0)
        n_sells = len([s for s in signals if s["action"] == "sell"])
        long_slots = max(0, self.max_positions - existing_longs + n_sells)

        for candidate in buy_candidates[:long_slots]:
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

        # Short the most-bearish names (long_short only), capped at max_positions
        # PER SIDE (#210): rank by conviction and take the top short slots, rather
        # than shorting every bearish name (the ~2000-short book that degenerated
        # the A/B). Existing shorts consume the short budget.
        if self.mode == "long_short":
            if self.selection == "rank":
                short_candidates = [
                    {"symbol": s, "q50": p["q50"]}
                    for s, p in predictions.items()
                    if s not in positions and s in current_prices
                    and p["q50"] < 0 and p["q50"] <= -self.return_threshold
                ]
            else:
                short_candidates = [
                    {"symbol": s, "q50": p["q50"]}
                    for s, p in predictions.items()
                    if s not in positions and s in current_prices
                    and p["q50"] <= -self.return_threshold
                ]
            short_candidates.sort(key=lambda x: x["q50"])  # most negative first
            existing_shorts = sum(1 for sh in positions.values() if sh < 0)
            n_covers = len([s for s in signals if s["action"] == "cover"])
            short_slots = max(0, self.max_positions - existing_shorts + n_covers)

            for candidate in short_candidates[:short_slots]:
                symbol = candidate["symbol"]
                price = current_prices[symbol]
                shares = int((initial_cash * self.position_size) / price)
                if shares > 0:
                    signals.append({
                        "action": "short", "symbol": symbol, "shares": shares,
                        "reason": f"ML bearish short (q50={candidate['q50']:.2%})",
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

        # Find buy candidates. Class membership (trend_classes) is the sign
        # floor in both modes. "absolute" additionally hard-gates on
        # confidence_threshold; "rank" drops that gate for entries -- sign
        # agreement plus top-K by margin is the floor, same principle as the
        # quantile path's return_threshold treatment (#220). The exit/sell
        # check below is untouched in both modes (out of scope: #220 only
        # covers entry candidate selection).
        buy_candidates = []
        for symbol, pred in predictions.items():
            if symbol not in current_prices:
                continue
            if symbol in positions:
                continue

            if pred["predicted_class"] in self.trend_classes:
                prob = pred.get(f"p_{pred['predicted_class']}", 0)
                qualifies = (
                    True if self.selection == "rank"
                    else prob >= self.confidence_threshold
                )
                if qualifies:
                    buy_candidates.append({
                        "symbol": symbol,
                        "class": pred["predicted_class"],
                        "probability": prob,
                        "margin": pred.get("margin", 0),
                    })

        buy_candidates.sort(key=lambda x: x["margin"], reverse=True)

        # max_positions PER SIDE (#210): count only existing longs against the
        # long budget; take the highest-margin longs up to the cap.
        existing_longs = sum(1 for sh in positions.values() if sh > 0)
        n_sells = len([s for s in signals if s["action"] == "sell"])
        long_slots = max(0, self.max_positions - existing_longs + n_sells)

        for candidate in buy_candidates[:long_slots]:
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

        # Short the most-confident bearish names (long_short only), capped at
        # max_positions PER SIDE (#210): rank by probability, take the top slots.
        if self.mode == "long_short":
            short_candidates = []
            for symbol, pred in predictions.items():
                if symbol in positions or symbol not in current_prices:
                    continue
                if pred["predicted_class"] in bearish_classes:
                    prob = pred.get(f"p_{pred['predicted_class']}", 0)
                    qualifies = (
                        True if self.selection == "rank"
                        else prob >= self.confidence_threshold
                    )
                    if qualifies:
                        short_candidates.append(
                            {"symbol": symbol,
                             "cls": pred["predicted_class"], "prob": prob})
            short_candidates.sort(key=lambda x: x["prob"], reverse=True)
            existing_shorts = sum(1 for sh in positions.values() if sh < 0)
            n_covers = len([s for s in signals if s["action"] == "cover"])
            short_slots = max(0, self.max_positions - existing_shorts + n_covers)

            for candidate in short_candidates[:short_slots]:
                symbol = candidate["symbol"]
                price = current_prices[symbol]
                shares = int((initial_cash * self.position_size) / price)
                if shares > 0:
                    signals.append({
                        "action": "short", "symbol": symbol, "shares": shares,
                        "reason": f"ML bearish short ({candidate['cls']}, p={candidate['prob']:.2%})",
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
            # Portfolio.positions values are plain dicts ({"shares", "avg_price"}),
            # not objects — returning the raw dict crashes the engine's sell path
            return {
                sym: (pos.get("shares", 0) if isinstance(pos, dict)
                      else pos.shares if hasattr(pos, "shares") else pos)
                for sym, pos in portfolio.positions.items()
            }
        return {}
