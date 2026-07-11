"""Cross-Sectional Decile Strategy (the run-13 finding's proper vehicle).

Ranks the whole universe by each stock's own causal signal (stochastic-K
computed from its price history), longs the top decile and shorts the bottom
(long_short mode; long_only takes only the top). Optionally gated by a
regime: it acts ONLY while the market state matches `gate_bucket` and goes
flat otherwise — the direct exploitation shape for a discovery-admitted
conditional edge.

Risk posture, by construction rather than by parameter:
- every rebalance CLOSES the entire book before opening the new one (no
  position stacking — the failure mode that let an uncapped momentum book
  spiral to -$91M on $100k);
- gross exposure (long + |short|) is capped at allocation_pct x initial cash.

Parameters:
- signal_period: stochastic-K lookback (default 14)
- decile: fraction per side (default 0.10)
- rebalance_days: calendar days between rebalances (default 20 — the
  admitted edge's horizon)
- allocation_pct: gross exposure cap as a fraction of initial cash
- mode: long_only | long_short (spec 009 threading)
- gate_labels / gate_bucket: {date: label} regime labels + the bucket in
  which the strategy is allowed to hold a book
"""
from datetime import date
from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes


def calculate_stoch_k(price_history: List[Dict[str, Any]],
                      period: int = 14) -> Optional[float]:
    """Causal stochastic-K at the last point of `price_history`.

    %K = 100 * (close - lowest_low) / (highest_high - lowest_low) over the
    trailing `period` records. Returns None on insufficient history or a
    zero range (flat/garbage prices are skipped, never divided by)."""
    if not price_history or len(price_history) < period:
        return None
    window = sorted(price_history, key=lambda r: r["date"])[-period:]
    close = window[-1]["close"]
    low = min(r.get("low", r["close"]) for r in window)
    high = max(r.get("high", r["close"]) for r in window)
    if high <= low or close is None:
        return None
    return 100.0 * (close - low) / (high - low)


class CrossSectionalDecileStrategy:
    """Long top-decile / short bottom-decile by cross-sectional signal rank."""

    def __init__(
        self,
        signal_period: int = 14,
        decile: float = 0.10,
        rebalance_days: int = 20,
        allocation_pct: float = 0.90,
        mode: str = "long_only",
        gate_labels: Optional[Dict[Any, str]] = None,
        gate_bucket: Optional[str] = None,
    ):
        self.signal_period = signal_period
        self.decile = decile
        self.rebalance_days = rebalance_days
        self.allocation_pct = allocation_pct
        self.mode = mode
        self.gate_labels = gate_labels
        self.gate_bucket = gate_bucket
        self.last_rebalance: Optional[date] = None

    # -- gating ---------------------------------------------------------------
    def _in_state(self, current_date: date) -> bool:
        if self.gate_labels is None or self.gate_bucket is None:
            return True
        return self.gate_labels.get(current_date) == self.gate_bucket

    def _should_rebalance(self, current_date: date) -> bool:
        if self.last_rebalance is None:
            return True
        return (current_date - self.last_rebalance).days >= self.rebalance_days

    # -- signals ----------------------------------------------------------------
    def generate_signals(
        self,
        current_date: date,
        portfolio: Any,
        price_data: Dict[str, List[Dict[str, Any]]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        with create_span("strategies.cross_sectional.signals",
                         mode=self.mode) as span:
            if not self._should_rebalance(current_date):
                return []
            self.last_rebalance = current_date

            signals: List[Dict[str, Any]] = []

            # 1) close the ENTIRE existing book first (no stacking, ever)
            # (comparison harness passes a bare positions dict — house
            # convention, same as mean_reversion)
            positions = (portfolio.positions
                         if hasattr(portfolio, "positions") else portfolio)
            for symbol, pos in sorted(positions.items()):
                shares = pos["shares"]
                if shares > 0:
                    signals.append({"action": "sell", "symbol": symbol,
                                    "shares": shares})
                elif shares < 0:
                    signals.append({"action": "cover", "symbol": symbol,
                                    "shares": -shares})

            # 2) out of state: stay flat (the gate IS the strategy)
            if not self._in_state(current_date):
                set_attributes(span, in_state=False, n_signals=len(signals))
                return signals

            # 3) rank the universe by causal signal
            ranked: List[tuple] = []
            prices: Dict[str, float] = {}
            for symbol, history in price_data.items():
                relevant = [r for r in history if r["date"] <= current_date]
                if not relevant:
                    continue
                k = calculate_stoch_k(relevant, self.signal_period)
                price = relevant[-1]["close"]
                if k is None or not price or price <= 0:
                    continue                      # skip-and-move-on, never divide
                ranked.append((k, symbol))
                prices[symbol] = price
            if not ranked:
                set_attributes(span, in_state=True, n_signals=len(signals))
                return signals
            ranked.sort()
            n_side = max(1, int(len(ranked) * self.decile))
            longs = [sym for _, sym in ranked[-n_side:]]
            shorts = [sym for _, sym in ranked[:n_side]] \
                if self.mode == "long_short" else []

            # 4) equal-weight within the gross cap: allocation covers BOTH sides
            gross = initial_cash * self.allocation_pct
            per_position = gross / (len(longs) + len(shorts))
            for symbol in longs:
                shares = int(per_position / prices[symbol])
                if shares > 0:
                    signals.append({"action": "buy", "symbol": symbol,
                                    "shares": shares})
            for symbol in shorts:
                shares = int(per_position / prices[symbol])
                if shares > 0:
                    signals.append({"action": "short", "symbol": symbol,
                                    "shares": shares})

            set_attributes(span, in_state=True, n_longs=len(longs),
                           n_shorts=len(shorts), n_signals=len(signals))
            return signals
