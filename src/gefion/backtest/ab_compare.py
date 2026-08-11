"""Universe A/B backtest harness (issue #197, epic #179).

A controlled A/B experiment that decides whether a *wider* opportunity set is
worth it: one pooled model trained + traded on universe A vs the SAME pipeline
on universe B, walk-forward out-of-sample, compared on REALIZED PORTFOLIO
outcomes.

This module ORCHESTRATES existing pieces — it does not reinvent
train/backtest:

- per-arm dataset build / pooled train / predict run through the existing CLI
  commands (``ml dataset-build --universe`` / ``ml train`` / ``ml predict``),
  exactly the ``gefion.ml.e2e`` composition pattern;
- the long/short-on-model-rank backtest reuses ``BacktestEngine`` +
  ``MLSignalStrategy`` (quantile, ``mode=long_short`` — buys the top q50 names,
  shorts the bottom) and ``load_price_data_for_backtest``;
- this module ADDS the matched-config controls, the realized-portfolio
  comparison metrics ``backtest run`` does not emit (breadth / tail richness /
  capacity), and the **negative-transfer diagnostic**.

Design (settled, from #197):

- **Arm A** = universe A. **Arm B** = universe B. Identical dates, split_spec,
  horizons, hyperparams, strategy params — the ONLY thing that differs is the
  universe (``MatchedConfig`` carries everything else and is shared by
  reference across arms, so "matched" is structural, not a check that can rot).
- **Arm C** (opt-in, ``attribution=True``) trains on universe B but trades the
  universe-A members only — isolates the data effect from the opportunity
  effect.
- The harness REPORTS the deltas + the negative-transfer verdict; it does NOT
  auto-decide. A human reads it (owner-gate philosophy).

The actual NASDAQ-vs-NASDAQ+NYSE run is gated on a real NYSE ingest (#179
phases 1-2); this is the harness, ready to run.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

# Metrics we surface per arm and diff A→B. Order = report/table column order.
_ARM_METRIC_KEYS = (
    "total_return",
    "annualized_return",
    "sharpe",
    "max_drawdown",
    "position_breadth",
    "tail_richness",
    "capacity_proxy",
)

# strategy_params keys run_arm forwards to MLSignalStrategy. Anything else is
# dropped (and, since #236, warned about) rather than silently ignored.
_STRATEGY_PARAM_KEYS = (
    "return_threshold", "downside_limit", "position_size",
    "max_positions", "rebalance_days", "selection",
)

_TRADING_DAYS_PER_YEAR = 252.0


# --------------------------------------------------------------------------- #
# Config + result containers.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MatchedConfig:
    """The controlled, shared config across every A/B arm.

    Deliberately carries NO universe field: the universe is the per-arm axis
    (passed separately to the arm runner). Freezing + dataclass equality make
    "only the universe differs" enforceable — the orchestrator hands the SAME
    instance to every arm.
    """

    start_date: date
    end_date: date
    split_spec: Dict[str, Any]        # walk-forward, OOS split (rides model store)
    horizons: List[int]               # label horizons, e.g. [7, 30]
    horizon_days: int                 # the traded horizon
    hyperparams: Dict[str, Any]       # pooled-model hyperparameters
    strategy_params: Dict[str, Any]   # long/short decile strategy params
    initial_capital: float = 100000.0
    # Per-horizon 5-class label thresholds for dataset-build (one each per
    # horizon). None => run_arm falls back to 2%/5%. Shared across arms.
    weak_thresholds: Optional[List[float]] = None
    strong_thresholds: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable echo of the matched config (for the report)."""
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "split_spec": self.split_spec,
            "horizons": list(self.horizons),
            "horizon_days": self.horizon_days,
            "hyperparams": self.hyperparams,
            "strategy_params": self.strategy_params,
            "initial_capital": self.initial_capital,
            "weak_thresholds": self.weak_thresholds,
            "strong_thresholds": self.strong_thresholds,
        }


@dataclass
class ArmSpec:
    """One arm of the experiment. The universe is the only per-arm input.

    ``trade_universe`` defaults to ``train_universe`` (Arms A/B). Arm C sets
    them apart: train on the wide universe, trade the narrow one.
    """

    label: str
    train_universe: str
    trade_universe: Optional[str] = None

    def __post_init__(self) -> None:
        if self.trade_universe is None:
            self.trade_universe = self.train_universe


@dataclass
class ArmResult:
    """Realized-portfolio outcome of a single arm.

    ``positions`` is the realized ledger the comparison consumes — one record
    per opened position with::

        {date, symbol, side: 'long'|'short', raw_return, pnl, dollar_volume}

    where ``raw_return`` is the name's own forward return over the horizon
    (the raw material of the decile spread) and ``dollar_volume`` is close ×
    volume at entry (the capacity proxy).
    """

    label: str
    train_universe: str
    trade_universe: str
    metrics: Dict[str, Any]                 # from BacktestEngine (total_return, ...)
    equity_curve: List[Dict[str, Any]]      # [{date, equity}]
    positions: List[Dict[str, Any]]         # realized ledger (see above)
    n_trading_days: int                     # for annualization
    artifacts: Dict[str, Any] = field(default_factory=dict)  # dataset/model provenance


# --------------------------------------------------------------------------- #
# Per-arm summary + A→B deltas.
# --------------------------------------------------------------------------- #
def _annualized_return(total_return: float, n_trading_days: int) -> float:
    """Geometric annualization of a realized total return."""
    if n_trading_days <= 0:
        return total_return
    base = 1.0 + total_return
    if base <= 0:
        return -1.0  # total wipeout, cannot compound below -100%
    return base ** (_TRADING_DAYS_PER_YEAR / n_trading_days) - 1.0


def _position_breadth(positions: List[Dict[str, Any]]) -> float:
    """Average number of distinct names held per rebalance date."""
    if not positions:
        return 0.0
    by_date: Dict[Any, Set[str]] = defaultdict(set)
    for p in positions:
        by_date[p["date"]].add(p["symbol"])
    return sum(len(names) for names in by_date.values()) / len(by_date)


def _tail_richness(positions: List[Dict[str, Any]]) -> float:
    """Realized long-minus-short decile spread: how much the extremes pay.

    mean(raw_return | long) − mean(raw_return | short). An empty side
    contributes 0.
    """
    longs = [p["raw_return"] for p in positions if p["side"] == "long"]
    shorts = [p["raw_return"] for p in positions if p["side"] == "short"]
    mean_long = statistics.fmean(longs) if longs else 0.0
    mean_short = statistics.fmean(shorts) if shorts else 0.0
    return mean_long - mean_short


def _capacity_proxy(positions: List[Dict[str, Any]]) -> float:
    """Median dollar-volume (close × volume) of traded names — a liquidity /
    capacity proxy. Higher ⇒ the strategy trades more tradeable names."""
    vols = [p["dollar_volume"] for p in positions
            if p.get("dollar_volume") is not None]
    if not vols:
        return 0.0
    return float(statistics.median(vols))


def _none_if_nan(value: float) -> Optional[float]:
    """`calculate_metrics` reports an undefined Sharpe as ``float('nan')``
    (#248) -- a real float, so it's fine for internal arithmetic, but a bare
    NaN token is not valid JSON (Python's ``json`` module emits one anyway;
    strict parsers reject it, and PostgreSQL's own jsonb input function does
    too). Report-surfaced fields translate it to ``None`` at the boundary,
    same as `_shared_edge`'s `total_pnl` (#248 defect 1) -- explicitly
    absent, never a value that merely looks like a number.
    """
    return None if math.isnan(value) else value


def compute_arm_summary(arm: ArmResult) -> Dict[str, Any]:
    """Roll a realized ArmResult up to the comparison metrics (per #197)."""
    total_return = float(arm.metrics.get("total_return", 0.0))
    longs = [p for p in arm.positions if p["side"] == "long"]
    shorts = [p for p in arm.positions if p["side"] == "short"]
    return {
        "label": arm.label,
        "train_universe": arm.train_universe,
        "trade_universe": arm.trade_universe,
        "total_return": total_return,
        "annualized_return": _annualized_return(total_return, arm.n_trading_days),
        "sharpe": _none_if_nan(float(arm.metrics.get("sharpe_ratio", 0.0))),
        "max_drawdown": float(arm.metrics.get("max_drawdown", 0.0)),
        "position_breadth": _position_breadth(arm.positions),
        "tail_richness": _tail_richness(arm.positions),
        "capacity_proxy": _capacity_proxy(arm.positions),
        "n_positions": len(arm.positions),
        "n_long": len(longs),
        "n_short": len(shorts),
    }


def compute_deltas(summary_a: Dict[str, Any],
                   summary_b: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """B − A for each numeric comparison metric (positive ⇒ B improved on it).

    Either side being absent (e.g. an undefined Sharpe, #248) makes the
    delta undefined too -- ``None``, not a value computed against a
    fabricated 0.0.
    """
    deltas: Dict[str, Optional[float]] = {}
    for key in _ARM_METRIC_KEYS:
        va, vb = summary_a.get(key, 0.0), summary_b.get(key, 0.0)
        deltas[key] = None if va is None or vb is None else float(vb) - float(va)
    return deltas


# --------------------------------------------------------------------------- #
# Negative-transfer diagnostic — the heart of the go/no-go.
# --------------------------------------------------------------------------- #
def _shared_edge(positions: List[Dict[str, Any]],
                 shared_members: Set[str]) -> Dict[str, Any]:
    """Realized directional edge captured on the shared (universe-A) names.

    Per position the captured edge is ``raw_return`` for a long and
    ``-raw_return`` for a short (a short profits when the name falls). The
    arm's edge on the shared names is the mean over those positions.
    """
    restricted = [p for p in positions if p["symbol"] in shared_members]
    if not restricted:
        return {"edge": 0.0, "n": 0, "total_pnl": None}
    edges = [p["raw_return"] if p["side"] == "long" else -p["raw_return"]
             for p in restricted]
    # Realized dollar pnl is only known for positions that carry it (see
    # `_build_positions_ledger`: opening trades don't, closing trades do).
    # A missing value on ANY restricted position means the total cannot be
    # trusted as complete, so it's reported absent rather than as a partial
    # sum passed off as the total (#248).
    known_pnls = [p["pnl"] for p in restricted if p.get("pnl") is not None]
    total_pnl = (sum(known_pnls) if len(known_pnls) == len(restricted)
                 else None)
    return {"edge": statistics.fmean(edges), "n": len(restricted),
            "total_pnl": total_pnl}


def negative_transfer_diagnostic(
    arm_a: ArmResult,
    arm_b: ArmResult,
    shared_members: Set[str],
) -> Dict[str, Any]:
    """Did the wider universe DILUTE the edge on the shared names?

    Restricts BOTH arms' realized positions to the shared universe-A members
    and compares the edge each captured there. If Arm B is worse on the SAME
    names, the wider universe diluted the NASDAQ edge (negative transfer) — the
    contingency #179 flagged as the real risk. Reported explicitly; never
    auto-actioned.
    """
    shared_members = set(shared_members)
    a = _shared_edge(arm_a.positions, shared_members)
    b = _shared_edge(arm_b.positions, shared_members)
    diluted = b["edge"] < a["edge"]
    if diluted:
        verdict = (
            "NEGATIVE TRANSFER: Arm B captured a weaker edge on the shared "
            f"{arm_a.trade_universe} names than Arm A "
            f"({b['edge']:.4f} vs {a['edge']:.4f}) — the wider universe "
            "diluted the edge. Weigh against B's breadth/capacity gains."
        )
    else:
        verdict = (
            "No dilution detected: Arm B held or improved the edge on the "
            f"shared {arm_a.trade_universe} names "
            f"({b['edge']:.4f} vs {a['edge']:.4f}) — transfer is non-negative "
            "on the shared cross-section."
        )
    return {
        "arm_a_edge": a["edge"],
        "arm_b_edge": b["edge"],
        "delta": b["edge"] - a["edge"],
        "diluted": diluted,
        "n_shared_a": a["n"],
        "n_shared_b": b["n"],
        "shared_member_count": len(shared_members),
        "arm_a_shared_pnl": a["total_pnl"],
        "arm_b_shared_pnl": b["total_pnl"],
        "verdict": verdict,
    }


# --------------------------------------------------------------------------- #
# Report assembly (JSON) + human-readable table.
# --------------------------------------------------------------------------- #
_REPORT_NOTE = (
    "This report compares realized portfolio outcomes; it does NOT auto-decide. "
    "A human weighs the A→B deltas against the negative-transfer verdict "
    "(owner-gate philosophy)."
)


def build_ab_report(
    arm_results: Dict[str, ArmResult],
    config: MatchedConfig,
    negative_transfer: Dict[str, Any],
    attribution: bool = False,
    checkpoint_provenance: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the JSON-serializable A/B comparison report."""
    summaries = {label: compute_arm_summary(arm)
                 for label, arm in arm_results.items()}

    deltas: Dict[str, Dict[str, float]] = {}
    if "A" in summaries and "B" in summaries:
        deltas["A_to_B"] = compute_deltas(summaries["A"], summaries["B"])
    if attribution and "A" in summaries and "C" in summaries:
        # Arm C shares Arm A's opportunity set → the delta isolates the DATA
        # effect (wider training data) from the opportunity effect.
        deltas["A_to_C"] = compute_deltas(summaries["A"], summaries["C"])

    report = {
        "config": config.to_dict(),
        "arms": summaries,
        "deltas": deltas,
        "negative_transfer": negative_transfer,
        "attribution": attribution,
        "note": _REPORT_NOTE,
    }
    if checkpoint_provenance is not None:
        # Per #234: which arms were reused from a checkpoint vs recomputed —
        # a reader of the report should be able to see this without digging
        # through logs.
        report["checkpoint_provenance"] = checkpoint_provenance
    return report


def _fmt(value: Optional[float], pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if pct:
        return f"{value * 100:.2f}%"
    return f"{value:.4f}"


def format_ab_report(report: Dict[str, Any]) -> str:
    """Render the report as a plain-text table for the human reader."""
    arms = report["arms"]
    labels = [lbl for lbl in ("A", "B", "C") if lbl in arms]

    rows_spec = [
        ("Train universe", lambda s: s["train_universe"], False),
        ("Trade universe", lambda s: s["trade_universe"], False),
        ("Total return", lambda s: _fmt(s["total_return"], pct=True), False),
        ("Annualized return", lambda s: _fmt(s["annualized_return"], pct=True), False),
        ("Sharpe", lambda s: _fmt(s["sharpe"]), False),
        ("Max drawdown", lambda s: _fmt(s["max_drawdown"], pct=True), False),
        ("Position breadth", lambda s: f"{s['position_breadth']:.1f}", False),
        ("Tail richness", lambda s: _fmt(s["tail_richness"], pct=True), False),
        ("Capacity proxy ($vol)", lambda s: f"{s['capacity_proxy']:,.0f}", False),
        ("# positions", lambda s: str(s["n_positions"]), False),
    ]

    label_w = 24
    col_w = 20
    lines: List[str] = []
    lines.append("=" * (label_w + col_w * len(labels)))
    lines.append("Universe A/B backtest comparison (issue #197)")
    lines.append("=" * (label_w + col_w * len(labels)))

    header = "Metric".ljust(label_w) + "".join(
        f"Arm {lbl}".ljust(col_w) for lbl in labels)
    lines.append(header)
    lines.append("-" * (label_w + col_w * len(labels)))
    for name, getter, _pct in rows_spec:
        line = name.ljust(label_w)
        for lbl in labels:
            line += str(getter(arms[lbl])).ljust(col_w)
        lines.append(line)

    # A→B delta column.
    deltas = report.get("deltas", {}).get("A_to_B")
    if deltas:
        lines.append("")
        lines.append("A→B deltas (Arm B − Arm A):")
        for key in _ARM_METRIC_KEYS:
            delta = deltas.get(key, 0.0)
            delta_str = "n/a" if math.isnan(delta) else f"{delta:+.4f}"
            lines.append(f"  {key.ljust(label_w)} {delta_str}")

    # Checkpoint provenance — which arms were reused vs recomputed (#234).
    cp = report.get("checkpoint_provenance")
    if cp:
        lines.append("")
        lines.append("Checkpoint provenance:")
        for lbl in labels:
            info = cp.get(lbl)
            if info is None:
                continue
            status = "reused checkpoint" if info.get("reused") else "recomputed"
            lines.append(f"  Arm {lbl}: {status} (key={info.get('key', '')[:12]})")

    # Negative-transfer verdict — the go/no-go signal.
    nt = report.get("negative_transfer", {})
    lines.append("")
    lines.append("Negative-transfer diagnostic (shared universe-A names):")
    lines.append(
        f"  Arm A edge {nt.get('arm_a_edge', 0.0):+.4f}   "
        f"Arm B edge {nt.get('arm_b_edge', 0.0):+.4f}   "
        f"delta {nt.get('delta', 0.0):+.4f}   "
        f"(n_shared={nt.get('n_shared_b', 0)})")
    lines.append(f"  {nt.get('verdict', '')}")

    lines.append("")
    lines.append(report.get("note", ""))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The real per-arm runner (reuse; integration-exercised, gated on NYSE ingest).
# --------------------------------------------------------------------------- #
def _default_universe_resolver(conn, name: str) -> Set[str]:
    """Resolve a universe name to its member symbol set (spec 015)."""
    from gefion.universe import universe_members

    return set(universe_members(conn, name))


def _run_cli(cmd: List[str]) -> None:
    """Run a gefion CLI command, raising on failure (e2e composition pattern)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "gefion.cli", *cmd],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CLI `{' '.join(cmd)}` failed: {result.stderr or result.stdout}")


def _build_positions_ledger(
    trades: List[Dict[str, Any]],
    price_data: List[Dict[str, Any]],
    horizon_days: int,
) -> List[Dict[str, Any]]:
    """Turn executed entry trades into the realized positions ledger.

    For each opening trade (``buy`` ⇒ long, ``short`` ⇒ short) records the
    name's own forward return over the horizon (the decile-spread raw material)
    and its entry-day dollar-volume (the capacity proxy), from the same price
    series the backtest ran on.
    """
    # Per-symbol sorted (date, close, volume) index.
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in price_data:
        by_symbol[row["symbol"]].append(row)
    for rows in by_symbol.values():
        rows.sort(key=lambda r: r["date"])

    def _forward_return(symbol: str, entry_date: date) -> Optional[float]:
        rows = by_symbol.get(symbol)
        if not rows:
            return None
        entry_close = None
        exit_close = None
        for r in rows:
            if r["date"] == entry_date:
                entry_close = r["close"]
            if entry_close is not None and r["date"] >= entry_date:
                # first bar at/after entry + horizon
                if (r["date"] - entry_date).days >= horizon_days:
                    exit_close = r["close"]
                    break
        if not entry_close or not exit_close or entry_close <= 0:
            return None
        return (exit_close - entry_close) / entry_close

    def _dollar_volume(symbol: str, entry_date: date) -> Optional[float]:
        for r in by_symbol.get(symbol, []):
            if r["date"] == entry_date:
                if r.get("close") and r.get("volume"):
                    return float(r["close"]) * float(r["volume"])
                return None
        return None

    ledger: List[Dict[str, Any]] = []
    for t in trades:
        action = t.get("action")
        if action not in ("buy", "short"):
            continue  # only opening trades define held positions
        side = "long" if action == "buy" else "short"
        raw_return = _forward_return(t["symbol"], t["date"])
        if raw_return is None:
            continue
        ledger.append({
            "date": t["date"],
            "symbol": t["symbol"],
            "side": side,
            "raw_return": raw_return,
            # Realized dollar pnl belongs to the CLOSING trade (sell/cover),
            # which this ledger doesn't retain -- an opening trade (buy/
            # short) never carries one. Explicitly absent, never a fabricated
            # 0.0 (#248).
            "pnl": None,
            "dollar_volume": _dollar_volume(t["symbol"], t["date"]),
        })
    return ledger


def run_arm(spec: ArmSpec, config: MatchedConfig, conn=None) -> ArmResult:
    """Run one arm end-to-end: dataset-build → pooled train → predict → backtest.

    Reuses the existing CLI commands (dataset-build/train/predict) and the
    existing ``BacktestEngine`` + ``MLSignalStrategy`` long/short-on-q50 path;
    only the realized-portfolio ledger enrichment is new. Integration-exercised
    (needs a populated DB); the unit tests stub this runner.
    """
    import datetime

    from gefion.backtest.data_loader import load_price_data_for_backtest
    from gefion.backtest.engine import BacktestEngine
    from gefion.strategies.ml_signal import MLSignalStrategy

    db_url = os.getenv("DATABASE_URL")
    with create_span("backtest.ab_compare.run_arm",
                     label=spec.label,
                     train_universe=spec.train_universe,
                     trade_universe=spec.trade_universe) as span:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ds_name = f"ab_{spec.label.lower()}_{spec.train_universe}"
        ds_version = stamp
        model_name = f"ab_{spec.label.lower()}_{spec.train_universe}_model"
        model_version = stamp
        horizons_csv = ",".join(str(h) for h in config.horizons)
        # dataset-build requires one weak+strong class threshold PER horizon
        # (it has no default and errors otherwise). Matched across arms; fall
        # back to 2% weak / 5% strong moves when the config doesn't set them.
        weak = config.weak_thresholds or [0.02] * len(config.horizons)
        strong = config.strong_thresholds or [0.05] * len(config.horizons)
        weak_csv = ",".join(str(w) for w in weak)
        strong_csv = ",".join(str(s) for s in strong)

        # 1) Build dataset over the TRAIN universe (pooled — all members).
        _run_cli([
            "ml", "dataset-build",
            "--name", ds_name, "--version", ds_version,
            "--universe", spec.train_universe,
            "--start-date", config.start_date.isoformat(),
            "--end-date", config.end_date.isoformat(),
            "--horizons", horizons_csv,
            "--weak-thresholds", weak_csv,
            "--strong-thresholds", strong_csv,
            "--export", "--force",
        ])

        # 2) Train the pooled model with the matched hyperparameters.
        train_cmd = [
            "ml", "train",
            "--dataset-name", ds_name, "--dataset-version", ds_version,
            "--model-name", model_name, "--model-version", model_version,
            "--algorithm", config.hyperparams.get("algorithm", "xgboost"),
        ]
        for flag, key in (("--learning-rate", "learning_rate"),
                          ("--n-estimators", "n_estimators"),
                          ("--max-depth", "max_depth"),
                          ("--min-child-weight", "min_child_weight"),
                          ("--subsample", "subsample"),
                          ("--colsample-bytree", "colsample_bytree"),
                          ("--reg-alpha", "reg_alpha"),
                          ("--reg-lambda", "reg_lambda")):
            if key in config.hyperparams:
                train_cmd.extend([flag, str(config.hyperparams[key])])
        _run_cli(train_cmd)

        # 3) Predict across the TRADE universe over the backtest window.
        trade_members = sorted(_default_universe_resolver(conn, spec.trade_universe)) \
            if conn is not None else None
        predict_cmd = [
            "ml", "predict",
            "--model-name", model_name, "--model-version", model_version,
            "--start-date", config.start_date.isoformat(),
            "--end-date", config.end_date.isoformat(),
        ]
        if trade_members:
            predict_cmd.extend(["--symbols", ",".join(trade_members)])
        _run_cli(predict_cmd)

        # 4) Backtest long/short on q50 rank, restricted to the trade universe.
        price_data = load_price_data_for_backtest(
            db_url,
            symbols=trade_members,
            start_date=config.start_date,
            end_date=config.end_date,
            universe=None if trade_members else spec.trade_universe,
        )
        dropped = [k for k in config.strategy_params
                   if k not in _STRATEGY_PARAM_KEYS]
        if dropped:
            logger.warning(
                "run_arm: dropping unsupported strategy_params keys: %s",
                dropped)
            set_attributes(span, dropped_strategy_params=dropped)

        strat = MLSignalStrategy(
            model_name=model_name,
            model_version=model_version,
            horizon_days=config.horizon_days,
            prediction_type="quantile",
            mode="long_short",
            db_url=db_url,
            **{k: v for k, v in config.strategy_params.items()
               if k in _STRATEGY_PARAM_KEYS},
        )

        def strategy_fn(current_date, portfolio, prices):
            return strat.generate_signals(
                current_date, portfolio, prices, config.initial_capital)

        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy_fn,
            initial_cash=config.initial_capital,
            start_date=config.start_date,
            end_date=config.end_date,
            mode="long_short",
        )
        bt = engine.run()
        ledger = _build_positions_ledger(
            bt.get("trades", []), price_data, config.horizon_days)

        set_attributes(span, trade_count=len(bt.get("trades", [])),
                       position_count=len(ledger),
                       total_return=bt.get("metrics", {}).get("total_return", 0.0))

        return ArmResult(
            label=spec.label,
            train_universe=spec.train_universe,
            trade_universe=spec.trade_universe,
            metrics=bt.get("metrics", {}),
            equity_curve=bt.get("equity_curve", []),
            positions=ledger,
            n_trading_days=len(bt.get("equity_curve", [])),
            artifacts={"dataset": f"{ds_name}:{ds_version}",
                       "model": f"{model_name}:{model_version}"},
        )


# --------------------------------------------------------------------------- #
# Per-arm checkpoint + resume (issue #234).
#
# A failed arm-B attempt should not force a ~1h arm-A rebuild. Each arm is
# keyed by a sha256 hash of everything that can affect its result — arm
# label, universe NAMES, the RESOLVED universe MEMBER SETS, and the full
# MatchedConfig (which itself carries the date range and model/strategy
# params). Membership is included, not just the name, because
# `universe_members` resolves point-in-time (effective-dated exclusions,
# spec 015) — the member set under a fixed name can drift day to day, and a
# multi-day retry sequence (the exact scenario this feature targets) can
# span such a drift. The key is deliberately conservative: when in doubt
# about whether an input matters, it goes in the hash, because a spurious
# rebuild costs an hour and a spurious reuse costs a wrong verdict on a
# multi-week investigation. Checkpoints are plain JSON files, one per key,
# next to the rest of gefion's run/session state.
# --------------------------------------------------------------------------- #
def default_checkpoint_dir() -> Path:
    """Where per-arm checkpoints live.

    ``GEFION_AB_CHECKPOINT_DIR`` overrides; otherwise ``~/.gefion/`` (the same
    home used for conversation history / UI errors / charts).
    """
    env_dir = os.getenv("GEFION_AB_CHECKPOINT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".gefion" / "ab_compare_checkpoints"


def compute_arm_key(
        spec: ArmSpec, config: MatchedConfig,
        train_universe_members: Iterable[str],
        trade_universe_members: Iterable[str]) -> str:
    """Stable sha256 hash of everything that affects this arm's result.

    Includes the arm label (two arms with identical universes/config but
    different labels are still tracked separately), both universe NAMES, the
    RESOLVED member set of each universe, and the full ``MatchedConfig``
    (which carries the date range and model/strategy params) so any change
    to a matched input forces a rebuild.

    The resolved member set matters because universe membership is resolved
    point-in-time (``universe_members`` defaults ``as_of`` to today and joins
    effective-dated exclusions) — it is not a fixed function of the name.
    Keying on the name alone would let a checkpoint silently outlive a
    membership change (e.g. a data-quality exclusion landing between runs),
    reusing yesterday's member set under today's name. Callers must resolve
    membership via the same ``universe_resolver`` used to run the arm.
    """
    payload = {
        "label": spec.label,
        "train_universe": spec.train_universe,
        "trade_universe": spec.trade_universe,
        "train_universe_members": sorted(train_universe_members),
        "trade_universe_members": sorted(trade_universe_members),
        "config": config.to_dict(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _checkpoint_path(checkpoint_dir: Path, key: str) -> Path:
    return checkpoint_dir / f"{key}.json"


def _serialize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """JSON-safe copy of a positions/equity-curve row list (dates -> ISO)."""
    out = []
    for row in rows:
        row = dict(row)
        if isinstance(row.get("date"), date):
            row["date"] = row["date"].isoformat()
        out.append(row)
    return out


def _deserialize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        row = dict(row)
        if isinstance(row.get("date"), str):
            row["date"] = date.fromisoformat(row["date"])
        out.append(row)
    return out


def _arm_result_to_dict(result: ArmResult) -> Dict[str, Any]:
    return {
        "label": result.label,
        "train_universe": result.train_universe,
        "trade_universe": result.trade_universe,
        "metrics": result.metrics,
        "equity_curve": _serialize_rows(result.equity_curve),
        "positions": _serialize_rows(result.positions),
        "n_trading_days": result.n_trading_days,
        "artifacts": result.artifacts,
    }


def _arm_result_from_dict(data: Dict[str, Any]) -> ArmResult:
    return ArmResult(
        label=data["label"],
        train_universe=data["train_universe"],
        trade_universe=data["trade_universe"],
        metrics=data["metrics"],
        equity_curve=_deserialize_rows(data["equity_curve"]),
        positions=_deserialize_rows(data["positions"]),
        n_trading_days=data["n_trading_days"],
        artifacts=data.get("artifacts", {}),
    )


def load_arm_checkpoint(checkpoint_dir: Path, key: str) -> Optional[ArmResult]:
    """Load a checkpointed ArmResult, or None on a miss.

    A checkpoint that exists but fails to parse is treated as a miss (never
    a crash, never a guess) — conservative in the same direction as the key
    itself: rebuild rather than risk acting on unreadable state.
    """
    path = _checkpoint_path(checkpoint_dir, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return _arm_result_from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        logger.warning(
            "ab_compare: checkpoint %s is unreadable (%s) — rebuilding",
            path, exc)
        return None


def save_arm_checkpoint(checkpoint_dir: Path, key: str, result: ArmResult) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(checkpoint_dir, key)
    path.write_text(json.dumps(_arm_result_to_dict(result), indent=2,
                               sort_keys=True, default=str))


# --------------------------------------------------------------------------- #
# Orchestrator.
# --------------------------------------------------------------------------- #
def run_ab_compare(
    arm_a_universe: str,
    arm_b_universe: str,
    config: MatchedConfig,
    conn=None,
    attribution: bool = False,
    arm_runner: Callable[[ArmSpec, MatchedConfig, Any], ArmResult] = run_arm,
    universe_resolver: Callable[[Any, str], Set[str]] = _default_universe_resolver,
    resume: bool = True,
    checkpoint_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the matched A/B (A/B/C) experiment and build the comparison report.

    The SAME ``config`` object is handed to every arm — the universe is the
    only per-arm input, so "matched config" is structural. ``arm_runner`` and
    ``universe_resolver`` are injectable so the heavy per-arm pipeline can be
    stubbed in tests.

    Per #234: each arm is checkpointed under a hash of everything that can
    affect its result (see ``compute_arm_key``). With ``resume=True``
    (default) a checkpointed arm is loaded and ``arm_runner`` is skipped for
    it — so a failed arm B no longer forces a ~1h arm-A rebuild. Pass
    ``resume=False`` to force every arm to recompute (still refreshes the
    checkpoints). The report's ``checkpoint_provenance`` records, per arm,
    whether it was reused.
    """
    with create_span("backtest.ab_compare",
                     arm_a=arm_a_universe, arm_b=arm_b_universe,
                     attribution=attribution) as span:
        specs = [
            ArmSpec("A", train_universe=arm_a_universe),
            ArmSpec("B", train_universe=arm_b_universe),
        ]
        if attribution:
            # Train on the wide universe, trade the narrow one (data effect).
            specs.append(ArmSpec("C", train_universe=arm_b_universe,
                                 trade_universe=arm_a_universe))

        ckpt_dir = Path(checkpoint_dir) if checkpoint_dir is not None \
            else default_checkpoint_dir()

        # Universe membership is resolved point-in-time (spec 015's
        # effective-dated exclusions), not a fixed function of the name — so
        # it must be resolved fresh on every run, BEFORE the checkpoint
        # lookup, and folded into the key (see compute_arm_key). Cache per
        # name within this run: several specs can share a universe name
        # (Arm A's train/trade, or Arm C reusing Arm B's train universe).
        resolved_members: Dict[str, List[str]] = {}

        def _resolve(name: str) -> List[str]:
            if name not in resolved_members:
                resolved_members[name] = sorted(universe_resolver(conn, name))
            return resolved_members[name]

        arm_results: Dict[str, ArmResult] = {}
        checkpoint_provenance: Dict[str, Dict[str, Any]] = {}
        for spec in specs:
            train_members = _resolve(spec.train_universe)
            trade_members = _resolve(spec.trade_universe)
            key = compute_arm_key(spec, config, train_members, trade_members)
            cached = load_arm_checkpoint(ckpt_dir, key) if resume else None
            if cached is not None:
                logger.info(
                    "ab_compare: reusing checkpointed arm %s (key=%s)",
                    spec.label, key[:12])
                result = cached
                reused = True
            else:
                result = arm_runner(spec, config, conn)
                save_arm_checkpoint(ckpt_dir, key, result)
                logger.info(
                    "ab_compare: computed arm %s and saved checkpoint (key=%s)",
                    spec.label, key[:12])
                reused = False
            arm_results[spec.label] = result
            checkpoint_provenance[spec.label] = {"reused": reused, "key": key}

        # Shared cross-section = the universe-A member set (A ⊆ B by design;
        # intersect to stay correct even if that ever stops holding). Both
        # universes were already resolved above (Arm A/B's train_universe
        # equal arm_a_universe/arm_b_universe by construction).
        members_a = set(_resolve(arm_a_universe))
        members_b = set(_resolve(arm_b_universe))
        shared_members = members_a & members_b if members_b else members_a

        nt = negative_transfer_diagnostic(
            arm_results["A"], arm_results["B"], shared_members)

        report = build_ab_report(arm_results, config, nt, attribution=attribution,
                                 checkpoint_provenance=checkpoint_provenance)
        set_attributes(span, arms=len(arm_results),
                       diluted=nt["diluted"],
                       shared_members=len(shared_members),
                       arms_reused=sum(1 for p in checkpoint_provenance.values()
                                       if p["reused"]))
        return report
