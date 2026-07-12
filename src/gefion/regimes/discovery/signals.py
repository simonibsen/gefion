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


class ModelSignalError(ValueError):
    """The model_predictions rung refuses: wrong namespace, missing
    materialization, vintage mixing, lookahead, or thin coverage."""


def resolve_model_signal_provenance(conn, signals: List[str]) -> Dict[str, Any]:
    """Resolve declared model-prediction signals to ONE model identity.

    Every signal must be a derived macro series whose market function reads
    only model-prediction features (the model-derived namespace). All signals
    must trace to the same model+version — two vintages in one hunt would be
    silent mixing. Returns {model_name, model_version, training_cutoff,
    horizons_days, input_features} where input_features is the model's FULL
    declared input list (the conservative entanglement rule, FR-1206).
    """
    import json as _json

    with create_span("discovery.signals.resolve_model_provenance",
                     n_signals=len(signals)) as span:
        idents: Dict[tuple, str] = {}
        horizons: set = set()
        fix = ("expose model signals with `gefion ml materialize-signals "
               "--model-name <m> --model-version <v>` then "
               "`gefion macro derive --series model_outlook_q50,"
               "model_confidence_width`")
        with conn.cursor() as cur:
            for sig in signals:
                fn_name = sig[len("macro_"):] if sig.startswith("macro_") else None
                row = None
                if fn_name:
                    cur.execute("SELECT inputs FROM feature_functions "
                                "WHERE name = %s AND scope = 'market'",
                                (fn_name,))
                    row = cur.fetchone()
                if row is None:
                    raise ModelSignalError(
                        f"signal {sig!r} is not a model-derived series — the "
                        f"model_predictions rung consumes derived macro series "
                        f"backed by model-prediction features only; {fix}")
                inputs = row[0]
                if isinstance(inputs, str):
                    inputs = _json.loads(inputs)
                feats = (inputs or {}).get("features") or []
                if not feats:
                    raise ModelSignalError(
                        f"signal {sig!r}: its market function declares no "
                        f"input features — not model-derived; {fix}")
                for feat in feats:
                    cur.execute("SELECT function_name, params "
                                "FROM feature_definitions WHERE name = %s",
                                (feat,))
                    frow = cur.fetchone()
                    params = frow[1] if frow else None
                    if isinstance(params, str):
                        params = _json.loads(params)
                    if (frow is None or frow[0] != "model_prediction"
                            or not params):
                        raise ModelSignalError(
                            f"signal {sig!r} reads {feat!r}, which is not a "
                            f"model-prediction feature — the rung refuses "
                            f"mixed or indicator-backed series; {fix}")
                    idents[(params["model_name"], params["model_version"])] =                         params["training_cutoff"]
                    horizons.add(params["horizon_days"])
            if len(idents) > 1:
                raise ModelSignalError(
                    f"declared signals trace to {len(idents)} different model "
                    f"vintages ({sorted(f'{n}:{v}' for n, v in idents)}) — one "
                    f"hunt, one vintage (silent mixing is how lookahead hides)")
            (mname, mver), cutoff = next(iter(idents.items()))
            cur.execute(
                """SELECT d.feature_names FROM ml_models m
                   JOIN ml_datasets d ON d.id = m.dataset_id
                   WHERE m.name = %s AND m.version = %s""", (mname, mver))
            drow = cur.fetchone()
        prov = {"model_name": mname, "model_version": mver,
                "training_cutoff": cutoff,
                "horizons_days": sorted(horizons),
                "input_features": sorted(drow[0]) if drow and drow[0] else []}
        set_attributes(span, model=f"{mname}:{mver}", cutoff=cutoff)
        return prov


def check_model_signal_window(conn, market: MarketData, signals: List[str],
                              provenance: Dict[str, Any],
                              coverage_floor: float = 0.95) -> Dict[str, Any]:
    """The rung's causality + coverage gate (FR-1205/1207), pre-registration.

    Lookahead: any signal value at or before the training cutoff can only
    mean corrupted materialization — refuse. Coverage: each signal must cover
    at least `coverage_floor` of the post-cutoff trading calendar up to the
    last evaluable day; a thin series refuses naming the fixing commands.
    Returns the auditable window record for the pre-registration.
    """
    cutoff = datetime.date.fromisoformat(provenance["training_cutoff"])
    end = max(market.dates())
    with conn.cursor() as cur:
        cur.execute("SELECT count(DISTINCT date) FROM stock_ohlcv "
                    "WHERE date > %s AND date <= %s", (cutoff, end))
        expected = cur.fetchone()[0]
    record = {"training_cutoff": str(cutoff), "window_end": str(end),
              "expected_days": expected, "coverage_floor": coverage_floor,
              "coverage": {}}
    for sig in signals:
        series = market.features[sig]
        first = series[0][0]
        if first <= cutoff:
            raise ModelSignalError(
                f"signal {sig!r} has values at or before the training cutoff "
                f"{cutoff} (first: {first}) — lookahead by construction; "
                f"re-materialize the signals from a clean backfill")
        have = sum(1 for d, _ in series if cutoff < d <= end)
        coverage = have / expected if expected else 0.0
        record["coverage"][sig] = round(coverage, 4)
        if coverage < coverage_floor:
            raise ModelSignalError(
                f"signal {sig!r} covers {coverage:.1%} of the {expected} "
                f"post-cutoff trading days (floor {coverage_floor:.0%}) — "
                f"fill the gap with `gefion ml predict-backfill "
                f"--model-name {provenance['model_name']} --model-version "
                f"{provenance['model_version']}` then `gefion macro derive`")
    return record


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
    an unknown feature.

    Branches on the feature's declared entity_table (spec 007): the symbol
    universe is a stocks concept and never applies to non-stock features; a
    single-entity series' daily median degenerates to the value itself.
    """
    cur.execute("SELECT id, entity_table FROM feature_definitions WHERE name = %s",
                (name,))
    found = cur.fetchone()
    if not found:
        raise LookupError(f"feature {name!r} is not defined")
    feature_id, entity_table = found
    if symbols is None or entity_table != "stocks":
        cur.execute(
            "SELECT date, percentile_cont(0.5) WITHIN GROUP (ORDER BY value) "
            "FROM computed_features WHERE feature_id = %s "
            "AND (%s::date IS NULL OR date <= %s::date) "
            "GROUP BY date ORDER BY date",
            (feature_id, max_date, max_date),
        )
    else:
        cur.execute(
            """SELECT cf.date, percentile_cont(0.5) WITHIN GROUP (ORDER BY cf.value)
               FROM computed_features cf JOIN stocks s ON s.id = cf.data_id
               WHERE cf.feature_id = %s AND s.symbol = ANY(%s)
                 AND (%s::date IS NULL OR cf.date <= %s::date)
               GROUP BY cf.date ORDER BY cf.date""",
            (feature_id, symbols, max_date, max_date),
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
