"""Discovery-run orchestration (006, T015).

The honest loop, in the only legal order: pre-register (search space with all
three seams + segregation boundaries) → enumerate → FREEZE the candidate set
(ledger status, the T4 guard) → evaluate on the outer holdout only → one flat
FDR call over every p-valued test → verdicts + ledgers. A run that cannot
prove segregation is recorded and marked invalid — no verdicts (FR-102).
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gefion.experiments.holdout import HoldoutManager
from gefion.experiments.statistical import apply_fdr
from gefion.observability import create_span, set_attributes
from gefion.regimes.definitions import RegimeDefinition, store_definition
from gefion.regimes.discovery import edges, grammar, ledger, universe
from gefion.regimes.discovery.segregation import (
    DiscoveryDataContext,
    MarketData,
    SegregationError,
)
from gefion.regimes.discovery.signals import FeatureSignalSource

# Discovery admits at a stricter rate than standard experiments (0.10): a
# discovered regime is a *claim mill* — its search volume is the risk — so the
# hard gate leans conservative (documented in docs/REGIMES.md).
DISCOVERY_FDR_RATE = 0.01

# Inner-screen threshold: a candidate must show this much evidence on INNER
# data before it is allowed to spend the outer holdout. The conjunction
# (inner screen AND outer FDR survival, on disjoint data) is what makes the
# zero-survivors-in-noise guarantee structural rather than seed luck.
INNER_SCREEN_PVALUE = 0.05

VALID_TIERS = ("interaction", "grammar", "expressive")


class DiscoveryError(ValueError):
    """Raised on an invalid discovery configuration."""


@dataclasses.dataclass
class DiscoveryConfig:
    """Everything a run pre-registers. Immutable once the run row is written."""

    name: str
    seed: int
    atoms: List[Dict[str, Any]]
    signals: List[str]
    depth: int = 2
    budget: int = 100
    tiers: Sequence[str] = ("interaction", "grammar")
    signal_source: str = "features"
    grading_scheme: str = "walk_forward"
    universe_filter: Optional[str] = None  # None -> default quality chain
    horizon_days: int = 1
    fdr_rate: float = DISCOVERY_FDR_RATE
    inner_screen: float = INNER_SCREEN_PVALUE
    min_effective_n: int = 20
    holdout_weeks: int = 6
    label_window: int = 60
    align_window: int = 60
    fresh_holdout: Optional[Tuple[datetime.date, datetime.date]] = None
    dataset_version: Optional[str] = None  # None -> from market data

    def validate(self) -> None:
        if not self.name or not self.signals:
            raise DiscoveryError("run name and a non-empty signal list are required")
        bad = [t for t in self.tiers if t not in VALID_TIERS]
        if bad:
            raise DiscoveryError(f"unknown tier(s): {bad}")
        if not self.tiers:
            raise DiscoveryError("at least one tier must be enabled")
        if self.budget < 1:
            raise DiscoveryError("budget must be >= 1")


def run_discovery(conn, config: DiscoveryConfig, market: MarketData) -> Dict[str, Any]:
    """Execute one discovery run end to end; returns a summary dict."""
    with create_span("discovery.runner.run", run_name=config.name,
                     seed=config.seed) as span:
        config.validate()
        dataset_version = config.dataset_version or market.dataset_version
        chain = universe.parse_filter_chain(config.universe_filter)
        atoms = grammar.validate_atoms(config.atoms)

        search_space = {
            "atoms": atoms,
            "depth": config.depth,
            "budget": config.budget,
            "tiers": list(config.tiers),
            "signal_source": config.signal_source,
            "grading_scheme": config.grading_scheme,
            "universe_filter": universe.describe_chain(chain),
            "signals": list(config.signals),
            "horizon_days": config.horizon_days,
            "fdr_rate": config.fdr_rate,
            "inner_screen": config.inner_screen,
            "min_effective_n": config.min_effective_n,
            "label_window": config.label_window,
            "align_window": config.align_window,
        }

        # -- segregation: prove it or record an invalid run (FR-102) ----------
        holdout = HoldoutManager(max_date=max(market.dates()),
                                 holdout_weeks=config.holdout_weeks)
        try:
            ctx = DiscoveryDataContext(market, holdout)
        except SegregationError as exc:
            run_id = ledger.create_run(conn, name=config.name, seed=config.seed,
                                       search_space=search_space,
                                       segregation={"error": str(exc)},
                                       dataset_version=dataset_version)
            ledger.set_status(conn, run_id, "invalid")
            raise

        # -- pre-register -------------------------------------------------------
        run_id = ledger.create_run(conn, name=config.name, seed=config.seed,
                                   search_space=search_space,
                                   segregation=ctx.boundaries(),
                                   dataset_version=dataset_version)
        set_attributes(span, run_id=run_id)

        try:
            summary = _enumerate_and_evaluate(
                conn, run_id, config, atoms, market, ctx, holdout)
        except Exception:
            ledger.set_status(conn, run_id, "invalid")
            raise
        set_attributes(span, family_size=summary["family_size"],
                       n_admitted=summary["n_admitted"])
        return summary


def _screen_atoms(conn, run_id: int, config: DiscoveryConfig,
                  atoms: List[Dict[str, Any]],
                  market: MarketData) -> List[Dict[str, Any]]:
    """Availability (FR-121) and entanglement (FR-114) screens, both recorded."""
    usable: List[Dict[str, Any]] = []
    signal_set = set(config.signals)
    for atom in atoms:
        feature = atom["feature"]
        if feature not in market.features:
            ledger.record_diagnostic(
                conn, run_id, kind="uncomputable_proposal",
                detail={"feature": feature, "atom": atom,
                        "reason": "feature not available in dataset"},
                sample_dependent=False)
        elif feature in signal_set:
            ledger.record_diagnostic(
                conn, run_id, kind="entangled",
                detail={"feature": feature, "atom": atom,
                        "reason": "atom conditions on a target signal"},
                sample_dependent=False)
        else:
            usable.append(atom)
    return usable


def _enumerate_and_evaluate(conn, run_id, config, atoms, market, ctx, holdout):
    usable = _screen_atoms(conn, run_id, config, atoms, market)

    # -- enumerate within budget (truncation is loud, never silent) ------------
    candidates: List[Dict[str, Any]] = []   # ledger rows
    eval_specs: List[grammar.Candidate] = []  # paired evaluation inputs
    if "interaction" in config.tiers:
        for cand in grammar.enumerate_candidates(usable, depth=1) if usable else []:
            candidates.append(_ledger_candidate(cand, "interaction"))
            eval_specs.append(cand)
    if "grammar" in config.tiers:
        for cand in grammar.enumerate_candidates(usable, depth=config.depth) if usable else []:
            candidates.append(_ledger_candidate(cand, "grammar"))
            eval_specs.append(cand)
    if "expressive" in config.tiers:
        raise NotImplementedError("expressive tier lands with the fresh-holdout reserve (US3)")

    enumerated = len(candidates)
    if enumerated > config.budget:
        ledger.record_diagnostic(
            conn, run_id, kind="budget_exhausted",
            detail={"enumerated": enumerated, "budget": config.budget,
                    "evaluated": config.budget},
            sample_dependent=False)
        candidates = candidates[: config.budget]
        eval_specs = eval_specs[: config.budget]

    cand_ids = ledger.record_candidates(conn, run_id, candidates)
    ledger.set_status(conn, run_id, "enumerated")  # FREEZE (T4 guard)

    # -- Stage A: DISCOVERY — screen on inner data only (the nested step) ------
    # A candidate must show evidence on the inner window (p <= inner_screen)
    # before it may spend the outer holdout. Selection uses data disjoint from
    # the judge, so it costs the outer test nothing; the conjunction is what
    # makes zero-survivors-in-noise structural.
    inner_market = ctx.inner_market()
    inner_src = FeatureSignalSource(inner_market, config.signals,
                                    align_window=config.align_window)
    inner_results: List[List[Dict[str, Any]]] = []
    selected: List[bool] = []
    for cand, spec in zip(candidates, eval_specs):
        tests = _candidate_tests(conn, run_id, config, inner_src, cand, spec,
                                 inner_market, start=None, end=None, stage="inner")
        inner_results.append(tests)
        pvalued = [t for t in tests if t["pvalue"] is not None]
        selected.append(any(t["pvalue"] <= config.inner_screen for t in pvalued))

    # -- Stage B: CONFIRMATION — outer holdout, selected candidates only -------
    src = FeatureSignalSource(market, config.signals,
                              align_window=config.align_window)
    all_tests: List[Dict[str, Any]] = []      # flat family, in deterministic order
    per_candidate: List[List[Dict[str, Any]]] = []
    for cand, spec, sel in zip(candidates, eval_specs, selected):
        tests: List[Dict[str, Any]] = []
        if sel:
            tests = _candidate_tests(conn, run_id, config, src, cand, spec, market,
                                     start=holdout.holdout_start_date,
                                     end=holdout.holdout_end_date, stage="outer")
        per_candidate.append(tests)
        all_tests.extend(tests)

    # -- ONE flat FDR family over every outer p-valued test (FR-104/108) -------
    valid = [t for t in all_tests if t["pvalue"] is not None]
    mask = apply_fdr([t["pvalue"] for t in valid], config.fdr_rate)
    for t, survived in zip(valid, mask):
        t["survived"] = bool(survived)
    for t in all_tests:
        t.setdefault("survived", False)  # refusals fail closed
    family_size = len(valid)
    ledger.set_family_size(conn, run_id, family_size)

    # -- verdicts + definitions -------------------------------------------------
    admitted: List[Dict[str, Any]] = []
    for cand, cand_id, sel, inner, tests in zip(
            candidates, cand_ids, selected, inner_results, per_candidate):
        results = {"inner": inner, "selected": sel, "tests": tests,
                   "inner_screen": config.inner_screen, "fdr_rate": config.fdr_rate}
        in_family = None
        if not sel:
            # discovery found nothing on inner data: no outer test was spent
            if all(t["pvalue"] is None for t in inner):
                verdict = "refused_low_power"
                in_family = False
            else:
                verdict, in_family = "rejected", False
        elif all(t["pvalue"] is None for t in tests):
            verdict = "refused_low_power"
        elif any(t["survived"] for t in tests):
            verdict = "admitted"
        else:
            verdict = "rejected"
        ledger.record_result(conn, cand_id, results=results, verdict=verdict,
                             in_family=in_family)
        if verdict == "admitted":
            name = _store_admitted(conn, run_id, config, cand)
            admitted.append({"candidate_id": cand_id,
                             "candidate_hash": cand["candidate_hash"],
                             "definition": name})

    ledger.set_status(conn, run_id, "evaluated")
    ledger.set_status(conn, run_id, "complete")
    return {
        "run_id": run_id,
        "status": "complete",
        "n_candidates": len(candidates),
        "n_selected": sum(selected),
        "family_size": family_size,
        "n_admitted": len(admitted),
        "admitted": admitted,
    }


def _candidate_tests(conn, run_id, config, src, cand, spec, market,
                     start, end, stage: str) -> List[Dict[str, Any]]:
    """All edge tests for one candidate over [start, end] on the given market
    view — tier-1 HAC interaction or tier-2 bucket tests. Refusals are
    recorded as sample-dependent diagnostics with their stage."""
    tests: List[Dict[str, Any]] = []
    if cand["tier"] == "interaction":
        for signal in config.signals:
            test = edges.tier1_interaction_test(
                src, signal=signal,
                conditioning_feature=cand["provenance"]["atom_features"][0],
                start=start, end=end)
            test["candidate_hash"] = cand["candidate_hash"]
            tests.append(test)
            if test["pvalue"] is None:
                ledger.record_diagnostic(
                    conn, run_id, kind="min_sample_refusal",
                    detail={"stage": stage, "signal": signal,
                            "candidate_hash": cand["candidate_hash"],
                            "n": test["n"], "floor": edges.MIN_INTERACTION_N},
                    sample_dependent=True)
    else:  # grammar: causal labels over this market view, tests in range
        labels = edges.causal_labels(spec, market, window=config.label_window)
        for signal in config.signals:
            for test in edges.tier2_bucket_tests(
                    src, signal=signal, labels_by_date=labels,
                    start=start, end=end,
                    min_effective_n=config.min_effective_n):
                test["candidate_hash"] = cand["candidate_hash"]
                tests.append(test)
                if test["pvalue"] is None:
                    ledger.record_diagnostic(
                        conn, run_id, kind="min_sample_refusal",
                        detail={"stage": stage, "signal": signal,
                                "bucket": test["bucket"],
                                "candidate_hash": cand["candidate_hash"],
                                "effective_n": test["effective_n"],
                                "floor": config.min_effective_n},
                        sample_dependent=True)
    return tests


def _ledger_candidate(cand: grammar.Candidate, tier: str) -> Dict[str, Any]:
    """A grammar.Candidate as a ledger row; the hash is tier-qualified because
    the same expression under a different hypothesis class is a different test."""
    return {
        "candidate_hash": f"{tier}:{grammar.candidate_hash(cand.expression)}",
        "expression": cand.expression,
        "tier": tier,
        "provenance": {"atom_features": list(cand.atom_features),
                       "depth": cand.depth,
                       "bucketing": cand.bucketing},
    }


def _store_admitted(conn, run_id: int, config: DiscoveryConfig,
                    cand: Dict[str, Any]) -> str:
    """An admitted candidate becomes an ordinary machine-origin regime (FR-110)."""
    short = cand["candidate_hash"].split(":")[-1][:8]
    name = f"disc-{config.name}-{short}"
    defn = RegimeDefinition(
        name=name, scope="market",
        expression=cand["expression"],
        bucketing=cand["provenance"]["bucketing"],
        origin="machine",
        descriptive_metadata={"discovery_run_id": run_id,
                              "tier": cand["tier"],
                              "candidate_hash": cand["candidate_hash"],
                              "seed": config.seed},
    )
    store_definition(conn, defn)
    return name
