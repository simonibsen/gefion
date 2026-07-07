"""Pluggable signal sources for regime discovery (006, T014 — US1).

v1 ships `features` (FR-108a): active feature signals turned into
per-observation edge records — for each date, the forward return earned by
following the signal's causal direction (sign of the value against its
trailing median; no future data). `model_predictions` and
`strategy_backtests` are later rungs enabled by configuration through this
same seam.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from gefion.observability import create_span, set_attributes
from gefion.regimes.discovery.segregation import MarketData, Series


class FeatureSignalSource:
    """Per-observation edge records from market-level feature signals."""

    def __init__(self, market: MarketData, signals: List[str], align_window: int = 60):
        missing = [s for s in signals if s not in market.features]
        if missing:
            raise LookupError(f"signal feature(s) not in market data: {missing}")
        self.market = market
        self.signals = list(signals)
        self.align_window = align_window

    def series(self, name: str) -> Series:
        """Raw market-level series for any feature (signal or conditioning)."""
        if name not in self.market.features:
            raise LookupError(f"feature {name!r} not in market data")
        return self.market.features[name]

    def records(self, signal: str,
                start: Optional[datetime.date] = None,
                end: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
        """Per-observation records: {date, baseline_score, experimental_score}.

        experimental_score at t = sign(signal_t - trailing_median) x forward
        return at t — the return of following the signal, aligned causally
        (the trailing median uses values in (t - window, t] only). Baseline is
        the do-nothing arm (0.0), so the paired holdout test asks "does
        following this signal earn anything here?"
        """
        with create_span("discovery.signals.records", signal=signal) as span:
            series = self.series(signal)
            fwd = dict(self.market.forward_returns)
            values = [v for _, v in series]
            out: List[Dict[str, Any]] = []
            w = self.align_window
            for i, (d, v) in enumerate(series):
                if i < w - 1 or d not in fwd:
                    continue
                if start is not None and d < start:
                    continue
                if end is not None and d > end:
                    continue
                med = float(np.median(values[i - w + 1: i + 1]))
                out.append({
                    "date": d,
                    "baseline_score": 0.0,
                    "experimental_score": float(np.sign(v - med) * fwd[d]),
                })
            set_attributes(span, n_records=len(out))
            return out


def _feature_series(cur, name: str, symbols: Optional[List[str]],
                    max_date: Optional[datetime.date] = None) -> Series:
    """Market-level daily median of a feature, optionally over a declared
    symbol universe and up to a declared vintage date. Raises LookupError on
    an unknown feature."""
    cur.execute("SELECT id FROM feature_definitions WHERE name = %s", (name,))
    found = cur.fetchone()
    if not found:
        raise LookupError(f"feature {name!r} is not defined")
    if symbols is None:
        cur.execute(
            "SELECT date, percentile_cont(0.5) WITHIN GROUP (ORDER BY value) "
            "FROM computed_features WHERE feature_id = %s "
            "AND (%s::date IS NULL OR date <= %s::date) "
            "GROUP BY date ORDER BY date",
            (found[0], max_date, max_date),
        )
    else:
        cur.execute(
            """SELECT cf.date, percentile_cont(0.5) WITHIN GROUP (ORDER BY cf.value)
               FROM computed_features cf JOIN stocks s ON s.id = cf.data_id
               WHERE cf.feature_id = %s AND s.symbol = ANY(%s)
                 AND (%s::date IS NULL OR cf.date <= %s::date)
               GROUP BY cf.date ORDER BY cf.date""",
            (found[0], symbols, max_date, max_date),
        )
    return [(d, float(v)) for d, v in cur.fetchall() if v is not None]


def load_market_data(conn, feature_names: List[str], horizon_days: int = 1,
                     dataset_version: str = "dev",
                     symbols: Optional[List[str]] = None,
                     optional_features: Optional[List[str]] = None,
                     max_date: Optional[datetime.date] = None) -> MarketData:
    """Load market-level series from the DB for a real discovery run.

    Features are the cross-sectional daily median (robust to outliers — the
    005 lesson); forward returns are the market mean of each stock's
    close-to-close return `horizon_days` rows ahead. `symbols` restricts both
    to the declared universe-filter chain's survivors (FR-121a). Features in
    `optional_features` that are unknown/empty are skipped — the runner
    records them as uncomputable-proposal diagnostics; everything else raises
    LookupError (honest error, no silent empty run). `max_date` loads the
    world as of a past date — the vintage re-discovery enabler (issue #68).
    """
    optional = set(optional_features or [])
    with create_span("discovery.signals.load_market_data",
                     n_features=len(feature_names), horizon_days=horizon_days):
        features: Dict[str, Series] = {}
        with conn.cursor() as cur:
            for name in feature_names:
                try:
                    series = _feature_series(cur, name, symbols, max_date=max_date)
                except LookupError:
                    if name in optional:
                        continue
                    raise
                if not series:
                    if name in optional:
                        continue
                    raise LookupError(f"feature {name!r} has no computed data")
                features[name] = series
            symbol_clause = "" if symbols is None else "JOIN stocks s ON s.id = o.data_id"
            conditions = []
            if symbols is not None:
                conditions.append("s.symbol = ANY(%(symbols)s)")
            if max_date is not None:
                conditions.append("o.date <= %(max_date)s")
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cur.execute(
                f"""
                SELECT date, AVG(fwd) FROM (
                    SELECT o.date, o.close,
                           LEAD(o.close, %(horizon)s) OVER (PARTITION BY o.data_id ORDER BY o.date)
                               / NULLIF(o.close, 0) - 1 AS fwd
                    FROM stock_ohlcv o {symbol_clause} {where}
                ) t
                WHERE fwd IS NOT NULL GROUP BY date ORDER BY date
                """,
                {"horizon": horizon_days, "symbols": symbols, "max_date": max_date},
            )
            fwd = [(d, float(v)) for d, v in cur.fetchall() if v is not None]
        if not fwd:
            raise LookupError("no forward returns available (need OHLCV price data)")
        return MarketData(features=features, forward_returns=fwd,
                          dataset_version=dataset_version)
