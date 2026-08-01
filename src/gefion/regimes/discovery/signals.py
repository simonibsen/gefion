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


class StrategySignalError(ValueError):
    """The strategy_backtests rung refuses: wrong namespace, missing
    materialization, mixed fit vintages, lookahead, or thin coverage."""


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


def materialize_strategy_equity(conn, config_name: str, strategy_name: str,
                                equity_curve: List[tuple],
                                fit_cutoff: Optional[datetime.date] = None,
                                input_features: Optional[List[str]] = None) -> str:
    """Store a strategy backtest's equity curve as a market-level series (the
    strategy_backtests rung's input, issue #105).

    Reuses the existing macro/computed-features molds (zero DDL): the equity
    LEVEL series lands under a `macro_series` entity as feature
    `macro_strategy_<config>_equity`; the strategy identity + fit cutoff +
    traded-feature list ride the market `feature_functions` row's `inputs`
    (the rung's provenance chain). The equity->per-observation-return mapping
    is applied later, at discovery time, by StrategyBacktestSignalSource — so
    the stored series is a plain point-in-time level, never a peeked return.
    Returns the market feature name.
    """
    from psycopg.types.json import Json

    from gefion.db.ingest import ensure_feature_definitions
    from gefion.macro import catalog

    series_name = f"strategy_{config_name}_equity"
    feature_name = f"macro_{series_name}"
    prov = {"config": config_name, "strategy_name": strategy_name,
            "fit_cutoff": fit_cutoff.isoformat() if fit_cutoff else None,
            "input_features": sorted(input_features or [])}
    with create_span("discovery.signals.materialize_strategy_equity",
                     config=config_name, n_points=len(equity_curve)) as span:
        series_id = catalog.ensure_series(
            conn, name=series_name, provider="strategy", kind="backtest",
            cadence="daily",
            description=f"equity curve of strategy config {config_name!r}")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feature_functions
                       (name, version, status, language, function_body,
                        description, inputs, scope, created_by)
                   VALUES (%s, 'v1', 'active', 'python', %s, %s, %s, 'market',
                           'strategy-backtests-rung')
                   ON CONFLICT (name, version) DO UPDATE
                       SET inputs = EXCLUDED.inputs,
                           function_body = EXCLUDED.function_body""",
                (series_name,
                 "# strategy equity curve (materialized, not computed here)\n",
                 f"equity curve of strategy config {config_name!r}",
                 Json({"strategy": prov})))
        ids = ensure_feature_definitions(conn, [{
            "name": feature_name, "function_name": series_name,
            "params": None, "source_table": "stock_ohlcv",
            "source_column": "close", "store_table": "computed_features",
            "store_column": "value", "store_type": "double precision",
            "active": True, "entity_table": "macro_series",
        }])
        feature_id = ids[feature_name]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO computed_features (data_id, date, feature_id, value)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (feature_id, data_id, date)
                   DO UPDATE SET value = EXCLUDED.value""",
                [(series_id, d, feature_id, float(v)) for d, v in equity_curve])
        conn.commit()
        set_attributes(span, series_id=series_id, feature_id=feature_id)
        return feature_name


def resolve_strategy_signal_provenance(conn, signals: List[str]) -> Dict[str, Any]:
    """Resolve declared strategy-equity signals to their strategy identities.

    Every signal must be a strategy-derived market series
    (`macro_strategy_<config>_equity`) backed by a market feature_function
    carrying strategy provenance (see materialize_strategy_equity). One hunt,
    one fit vintage: distinct non-null fit cutoffs across the declared signals
    refuse (mixed in-sample windows are how lookahead hides). Returns
    {strategies, fit_cutoff, input_features} — the last feeds the conservative
    entanglement rule (an atom the strategy trades on conditions it on itself).
    """
    import json as _json

    with create_span("discovery.signals.resolve_strategy_provenance",
                     n_signals=len(signals)) as span:
        fix = ("materialize a strategy's equity curve as a market series with "
               "gefion.regimes.discovery.signals.materialize_strategy_equity "
               "(name macro_strategy_<config>_equity)")
        strategies: List[Dict[str, Any]] = []
        cutoffs: set = set()
        inputs: set = set()
        seen: set = set()
        with conn.cursor() as cur:
            for sig in signals:
                fn_name = sig[len("macro_"):] if sig.startswith("macro_") else None
                row = None
                if fn_name:
                    cur.execute("SELECT inputs FROM feature_functions "
                                "WHERE name = %s AND scope = 'market'", (fn_name,))
                    row = cur.fetchone()
                strat = None
                if row is not None:
                    payload = row[0]
                    if isinstance(payload, str):
                        payload = _json.loads(payload)
                    strat = (payload or {}).get("strategy")
                if not strat or not strat.get("config"):
                    raise StrategySignalError(
                        f"signal {sig!r} is not a strategy-derived series — the "
                        f"strategy_backtests rung consumes an equity-curve series "
                        f"only; {fix}")
                key = strat["config"]
                if key not in seen:
                    seen.add(key)
                    strategies.append({"config": strat["config"],
                                       "strategy_name": strat.get("strategy_name")})
                if strat.get("fit_cutoff"):
                    cutoffs.add(strat["fit_cutoff"])
                inputs.update(strat.get("input_features") or [])
        if len(cutoffs) > 1:
            raise StrategySignalError(
                f"declared strategy signals carry {len(cutoffs)} different fit "
                f"cutoffs ({sorted(cutoffs)}) — one hunt, one fit vintage "
                f"(mixed in-sample windows are how lookahead hides)")
        prov = {"strategies": strategies,
                "fit_cutoff": next(iter(cutoffs)) if cutoffs else None,
                "input_features": sorted(inputs)}
        set_attributes(span, n_strategies=len(strategies),
                       fit_cutoff=prov["fit_cutoff"] or "none")
        return prov


def check_strategy_signal_window(conn, market: MarketData, signals: List[str],
                                 provenance: Dict[str, Any],
                                 coverage_floor: float = 0.95) -> Dict[str, Any]:
    """The strategy rung's causality + coverage gate, at pre-registration.

    In-sample lookahead: if the strategy declares a fit cutoff, any equity
    value at or before it is in-sample by construction — refuse. Coverage:
    each signal must cover at least `coverage_floor` of the evaluable trading
    calendar (post-cutoff when a fit cutoff exists, else the full grid); a thin
    series refuses. Returns the auditable window record for pre-registration.
    """
    cutoff = (datetime.date.fromisoformat(provenance["fit_cutoff"])
              if provenance.get("fit_cutoff") else None)
    end = max(market.dates())
    with conn.cursor() as cur:
        if cutoff is not None:
            cur.execute("SELECT count(DISTINCT date) FROM stock_ohlcv "
                        "WHERE date > %s AND date <= %s", (cutoff, end))
        else:
            cur.execute("SELECT count(DISTINCT date) FROM stock_ohlcv "
                        "WHERE date <= %s", (end,))
        expected = cur.fetchone()[0]
    record = {"fit_cutoff": provenance.get("fit_cutoff"), "window_end": str(end),
              "expected_days": expected, "coverage_floor": coverage_floor,
              "coverage": {}}
    for sig in signals:
        series = market.features[sig]
        first = series[0][0]
        if cutoff is not None and first <= cutoff:
            raise StrategySignalError(
                f"signal {sig!r} has an equity value at or before the fit cutoff "
                f"{cutoff} (first: {first}) — in-sample lookahead by construction; "
                f"materialize the equity curve from the post-cutoff span only")
        have = sum(1 for d, _ in series
                   if (cutoff is None or d > cutoff) and d <= end)
        coverage = have / expected if expected else 0.0
        record["coverage"][sig] = round(coverage, 4)
        if coverage < coverage_floor:
            raise StrategySignalError(
                f"signal {sig!r} covers {coverage:.1%} of the {expected} "
                f"evaluable trading days (floor {coverage_floor:.0%}) — "
                f"re-materialize the strategy equity curve over the full span")
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


class StrategyBacktestSignalSource:
    """Per-observation edge records from a strategy's equity curve (#105).

    The signal is a strategy's equity LEVEL series (materialized market-side);
    `records()` maps it to per-observation strategy RETURNS aligned to the
    discovery observation grid and uses each return directly as the
    experimental_score — "does following this strategy earn here?", the honest
    question to ask of a strategy per regime. The mapping is causal by
    construction: r_t = equity_t / equity_{t-1} - 1 uses only equity at or
    before t, so no future point can enter an earlier return (no lookahead).
    Conditioning features are read raw from market data, exactly like the
    feature rung, so tier-1/tier-2 tests plug into the same downstream.
    """

    def __init__(self, market: MarketData, signals: List[str], align_window: int = 60):
        missing = [s for s in signals if s not in market.features]
        if missing:
            raise LookupError(f"strategy signal(s) not in market data: {missing}")
        self.market = market
        self.signals = list(signals)
        self.align_window = align_window  # unused (no trailing median); parity
        self._returns: Dict[str, Series] = {
            s: self._to_returns(market.features[s]) for s in signals}

    @staticmethod
    def _to_returns(levels: Series) -> Series:
        """Equity levels -> per-observation returns, causal (only past+present)."""
        out: Series = []
        prev: Optional[tuple] = None
        for d, lvl in sorted(levels):
            if prev is not None and prev[1] != 0:
                out.append((d, float(lvl) / float(prev[1]) - 1.0))
            prev = (d, lvl)
        return out

    def series(self, name: str) -> Series:
        """Return series for a strategy signal; raw market series otherwise."""
        if name in self._returns:
            return self._returns[name]
        if name not in self.market.features:
            raise LookupError(f"feature {name!r} not in market data")
        return self.market.features[name]

    def records(self, signal: str,
                start: Optional[datetime.date] = None,
                end: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
        """Per-observation records: {date, baseline_score, experimental_score}.

        experimental_score at t = the strategy's return over (t-1, t]; baseline
        is the do-nothing arm (0.0). No future equity enters r_t, so the paired
        conditional test asks "does the strategy earn in this regime?" without
        peeking past the observation date.
        """
        with create_span("discovery.signals.strategy_records", signal=signal) as span:
            if signal not in self._returns:
                self._returns[signal] = self._to_returns(self.series(signal))
            out: List[Dict[str, Any]] = []
            for d, r in self._returns[signal]:
                if start is not None and d < start:
                    continue
                if end is not None and d > end:
                    continue
                out.append({"date": d, "baseline_score": 0.0,
                            "experimental_score": float(r)})
            set_attributes(span, n_records=len(out))
            return out


def make_signal_source(signal_source: str, market: MarketData,
                       signals: List[str], align_window: int = 60):
    """Dispatch to the signal source for a declared rung (the pluggable seam).

    `features` and `model_predictions` share FeatureSignalSource (model signals
    are ordinary market series under guard); `strategy_backtests` uses the
    equity-curve source. Unknown sources are rejected upstream at the CLI.
    """
    if signal_source == "strategy_backtests":
        return StrategyBacktestSignalSource(market, signals, align_window=align_window)
    return FeatureSignalSource(market, signals, align_window=align_window)


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
