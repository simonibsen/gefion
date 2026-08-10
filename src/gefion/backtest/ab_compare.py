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

import logging
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set

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
        "sharpe": float(arm.metrics.get("sharpe_ratio", 0.0)),
        "max_drawdown": float(arm.metrics.get("max_drawdown", 0.0)),
        "position_breadth": _position_breadth(arm.positions),
        "tail_richness": _tail_richness(arm.positions),
        "capacity_proxy": _capacity_proxy(arm.positions),
        "n_positions": len(arm.positions),
        "n_long": len(longs),
        "n_short": len(shorts),
    }


def compute_deltas(summary_a: Dict[str, Any],
                   summary_b: Dict[str, Any]) -> Dict[str, float]:
    """B − A for each numeric comparison metric (positive ⇒ B improved on it)."""
    return {
        key: float(summary_b.get(key, 0.0)) - float(summary_a.get(key, 0.0))
        for key in _ARM_METRIC_KEYS
    }


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
        return {"edge": 0.0, "n": 0, "total_pnl": 0.0}
    edges = [p["raw_return"] if p["side"] == "long" else -p["raw_return"]
             for p in restricted]
    total_pnl = sum(float(p.get("pnl", 0.0)) for p in restricted)
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

    return {
        "config": config.to_dict(),
        "arms": summaries,
        "deltas": deltas,
        "negative_transfer": negative_transfer,
        "attribution": attribution,
        "note": _REPORT_NOTE,
    }


def _fmt(value: float, pct: bool = False) -> str:
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
            lines.append(f"  {key.ljust(label_w)} {deltas.get(key, 0.0):+.4f}")

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
            "pnl": float(t.get("pnl", 0.0)),
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
) -> Dict[str, Any]:
    """Run the matched A/B (A/B/C) experiment and build the comparison report.

    The SAME ``config`` object is handed to every arm — the universe is the
    only per-arm input, so "matched config" is structural. ``arm_runner`` and
    ``universe_resolver`` are injectable so the heavy per-arm pipeline can be
    stubbed in tests.
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

        arm_results: Dict[str, ArmResult] = {}
        for spec in specs:
            arm_results[spec.label] = arm_runner(spec, config, conn)

        # Shared cross-section = the universe-A member set (A ⊆ B by design;
        # intersect to stay correct even if that ever stops holding).
        members_a = set(universe_resolver(conn, arm_a_universe))
        members_b = set(universe_resolver(conn, arm_b_universe))
        shared_members = members_a & members_b if members_b else members_a

        nt = negative_transfer_diagnostic(
            arm_results["A"], arm_results["B"], shared_members)

        report = build_ab_report(arm_results, config, nt, attribution=attribution)
        set_attributes(span, arms=len(arm_results),
                       diluted=nt["diluted"],
                       shared_members=len(shared_members))
        return report
