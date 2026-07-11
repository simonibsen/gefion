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
import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gefion.experiments.holdout import HoldoutManager
from gefion.experiments.statistical import apply_fdr
from gefion.observability import create_span, set_attributes
from gefion.regimes.definitions import (
    RegimeDefinition,
    RegimeExpressionError,
    iter_leaves,
    store_definition,
    validate_expression,
)
from gefion.regimes.discovery import (
    detectors,
    edges,
    freshhold,
    grading,
    grammar,
    ledger,
    universe,
)
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

# The v1 scale ceiling made explicit (spec 010, FR-1009). v1's flat BH family
# is honest at these volumes (measured false-admission ~1/100 noise runs);
# beyond them the search itself must be modeled, so raising either cap
# requires passing selection-aware (SPA) re-verdicts on recent prior runs.
V1_MAX_BUDGET = 200
V1_MAX_DEPTH = 2

# How many of the most recent completed runs (same dataset version) must
# carry a passing latest SPA re-verdict to license an above-cap start.
_SPA_GATE_RUNS = 2


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
    fold_length_days: int = 30  # walk-forward grading fold width (declared)
    max_date: Optional[datetime.date] = None  # declared vintage (issue #68)
    holdout_weeks: int = 6
    label_window: int = 60
    align_window: int = 60
    fresh_holdout: Optional[Tuple[datetime.date, datetime.date]] = None
    freeform: Sequence[Dict[str, Any]] = ()   # expressive: agent-supplied ASTs
    detectors: Sequence[Dict[str, Any]] = ()  # expressive: {name, code, feature, provenance?}
    reserve_justification: Optional[str] = None
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
        if "expressive" in self.tiers and self.fresh_holdout is None:
            raise DiscoveryError(
                "expressive tier requires a declared fresh-holdout reserve "
                "(FR-119: no p-value for an unbounded, data-reusing search)")
        if (self.freeform or self.detectors) and "expressive" not in self.tiers:
            raise DiscoveryError(
                "freeform/detector candidates require the expressive tier")


def check_budget_gate(conn, config: DiscoveryConfig,
                      dataset_version: str) -> Optional[Dict[str, Any]]:
    """The SPA budget gate (FR-1009/1010, research R9): above-cap scale must
    be earned via BH/SPA coherence.

    Within the v1 caps, returns None — the gate does not exist for such runs.
    Above either cap, the _SPA_GATE_RUNS most recent completed runs with a
    non-empty family on the same dataset version (family-0 runs have nothing
    for SPA to test and are skipped) must each carry a latest re-verdict AND
    be coherent: zero admissions, or a SUPPORTED re-verdict (p ≤ level). The
    dangerous state is admissions the selection-aware test cannot back.
    Returns the auditable satisfaction record for the pre-registration, or
    raises DiscoveryError naming the gate and the satisfying command.
    """
    if config.budget <= V1_MAX_BUDGET and config.depth <= V1_MAX_DEPTH:
        return None
    with create_span("discovery.runner.budget_gate",
                     budget=config.budget, depth=config.depth,
                     dataset_version=dataset_version) as span:
        excess = (f"budget {config.budget} > {V1_MAX_BUDGET}"
                  if config.budget > V1_MAX_BUDGET
                  else f"depth {config.depth} > {V1_MAX_DEPTH}")
        recent = [r for r in ledger.list_runs(conn, status="complete")
                  if r["dataset_version"] == dataset_version
                  and (r["family_size"] or 0) > 0][:_SPA_GATE_RUNS]
        if len(recent) < _SPA_GATE_RUNS:
            raise DiscoveryError(
                f"budget gate: {excess} requires BH/SPA-coherent re-verdicts "
                f"on the {_SPA_GATE_RUNS} most recent completed runs with a "
                f"non-empty family (dataset version {dataset_version!r}), but "
                f"only {len(recent)} such run(s) exist — run within the v1 "
                f"caps first, then `gefion regime discover spa <run>`")
        failing = []
        reverdict_ids = []
        with conn.cursor() as cur:
            for r in recent:
                latest = ledger.latest_spa_reverdict(conn, r["id"])
                cur.execute(
                    "SELECT count(*) FROM regime_candidates "
                    "WHERE run_id = %s AND verdict = 'admitted'", (r["id"],))
                admitted = cur.fetchone()[0]
                if latest is None:
                    failing.append(
                        f"run {r['id']} '{r['name']}': SPA not yet run")
                elif admitted > 0 and not latest["passed"]:
                    failing.append(
                        f"run {r['id']} '{r['name']}': {admitted} admission(s) "
                        f"but the latest SPA is UNSUPPORTED "
                        f"(p={latest['p_consistent']:.4g} > level "
                        f"{latest['level']:g}) — BH admitted what SPA cannot "
                        f"distinguish from search luck")
                else:
                    reverdict_ids.append(latest["id"])
        if failing:
            raise DiscoveryError(
                f"budget gate: {excess} requires BH/SPA coherence (no "
                f"unsupported admissions) on each of the {_SPA_GATE_RUNS} "
                f"most recent completed non-empty runs (dataset version "
                f"{dataset_version!r}) — " + "; ".join(failing)
                + " — satisfy with `gefion regime discover spa <run>`")
        gate = {"gate": "spa", "runs": [r["id"] for r in recent],
                "reverdict_ids": reverdict_ids}
        set_attributes(span, gate_runs=gate["runs"],
                       gate_reverdicts=reverdict_ids)
        return gate


def run_discovery(conn, config: DiscoveryConfig, market: MarketData) -> Dict[str, Any]:
    """Execute one discovery run end to end; returns a summary dict."""
    with create_span("discovery.runner.run", run_name=config.name,
                     seed=config.seed) as span:
        config.validate()
        dataset_version = config.dataset_version or market.dataset_version
        gate = check_budget_gate(conn, config, dataset_version)
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
            "fold_length_days": config.fold_length_days,
            **({"max_date": str(config.max_date)} if config.max_date else {}),
            "label_window": config.label_window,
            "align_window": config.align_window,
        }

        if gate is not None:
            search_space["gate"] = gate
        if config.freeform:
            search_space["freeform"] = list(config.freeform)
        if config.detectors:
            search_space["detectors"] = [
                {"name": d["name"], "feature": d["feature"],
                 "code_sha": hashlib.sha256(d["code"].encode("utf-8")).hexdigest()}
                for d in config.detectors]

        # -- declared vintage: the run must see exactly the world it declared --
        if config.max_date is not None:
            beyond = [d for d in market.dates() if d > config.max_date]
            if beyond:
                raise DiscoveryError(
                    f"vintage declared as {config.max_date} but the market data "
                    f"contains {len(beyond)} later date(s) (first: {min(beyond)}) — "
                    "load the data with the same max_date it declares")

        # -- segregation: prove it or record an invalid run (FR-102) ----------
        holdout = HoldoutManager(max_date=max(market.dates()),
                                 holdout_weeks=config.holdout_weeks)
        try:
            ctx = DiscoveryDataContext(market, holdout, reserve=config.fresh_holdout)
        except SegregationError as exc:
            run_id = ledger.create_run(conn, name=config.name, seed=config.seed,
                                       search_space=search_space,
                                       segregation={"error": str(exc)},
                                       dataset_version=dataset_version)
            ledger.set_status(conn, run_id, "invalid")
            raise

        # -- fresh-holdout reserve gate (single-use; justification recorded) ----
        segregation_record = ctx.boundaries()
        if config.fresh_holdout is not None:
            segregation_record["reserve"] = freshhold.require_reserve(
                conn, segregation_record,
                str(config.fresh_holdout[0]), str(config.fresh_holdout[1]),
                justification=config.reserve_justification)

        # -- pre-register -------------------------------------------------------
        run_id = ledger.create_run(conn, name=config.name, seed=config.seed,
                                   search_space=search_space,
                                   segregation=segregation_record,
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
        _enumerate_expressive(conn, run_id, config, market, candidates, eval_specs)

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
    detector_state: Dict[int, Dict[str, Any]] = {}  # index -> fit result / refusal
    for i, (cand, spec) in enumerate(zip(candidates, eval_specs)):
        if isinstance(spec, dict):  # detector candidate: fit + screens, inner only
            result = detectors.run_detector_candidate(
                ctx, spec["code"], spec["feature"], seed=config.seed)
            detector_state[i] = result
            if result["refusal"] is not None:
                ledger.record_diagnostic(
                    conn, run_id, kind=result["refusal"],
                    detail={"candidate_hash": cand["candidate_hash"],
                            **(result["detail"] or {})},
                    sample_dependent=(result["refusal"] != "noncausal"))
                inner_results.append([])
                selected.append(False)
                continue
            ledger.update_provenance(conn, run_id, cand["candidate_hash"],
                                     {"fitted_params": result["params"]})
            cand["provenance"]["fitted_params"] = result["params"]
            labels = dict(result["labels"])
            tests = _bucket_tests_for(conn, run_id, config, inner_src, cand,
                                      labels, start=None, end=None, stage="inner")
        else:
            tests = _candidate_tests(conn, run_id, config, inner_src, cand, spec,
                                     inner_market, start=None, end=None, stage="inner")
        inner_results.append(tests)
        pvalued = [t for t in tests if t["pvalue"] is not None]
        selected.append(any(t["pvalue"] <= config.inner_screen for t in pvalued))

    # -- Stage B: CONFIRMATION — selected candidates only. Interaction/grammar
    # confirm on the outer holdout; expressive candidates confirm on the
    # declared, single-use fresh-holdout reserve (FR-118a).
    src = FeatureSignalSource(market, config.signals,
                              align_window=config.align_window)
    all_tests: List[Dict[str, Any]] = []      # flat family, in deterministic order
    per_candidate: List[List[Dict[str, Any]]] = []
    reserve_spent = False
    for i, (cand, spec, sel) in enumerate(zip(candidates, eval_specs, selected)):
        tests: List[Dict[str, Any]] = []
        if sel:
            if cand["tier"] == "expressive":
                start, end = config.fresh_holdout
                reserve_spent = True
            else:
                start, end = holdout.holdout_start_date, holdout.holdout_end_date
            if isinstance(spec, dict):
                labels = detectors.apply_detector(
                    market, spec["code"], spec["feature"],
                    detector_state[i]["params"])
                tests = _bucket_tests_for(conn, run_id, config, src, cand,
                                          labels, start=start, end=end, stage="outer")
            else:
                tests = _candidate_tests(conn, run_id, config, src, cand, spec,
                                         market, start=start, end=end, stage="outer")
        per_candidate.append(tests)
        all_tests.extend(tests)
    if reserve_spent:
        freshhold.consume(conn, run_id)

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
    for i, (cand, cand_id, sel, inner, tests) in enumerate(zip(
            candidates, cand_ids, selected, inner_results, per_candidate)):
        results = {"inner": inner, "selected": sel, "tests": tests,
                   "inner_screen": config.inner_screen, "fdr_rate": config.fdr_rate}
        in_family = None
        refusal = (detector_state.get(i) or {}).get("refusal")
        if refusal is not None:
            # detector screens: degeneracy/instability/non-causality (SC-106)
            verdict = ("refused_degenerate" if refusal == "degenerate"
                       else "refused_unstable")
            results["detector_refusal"] = {"kind": refusal,
                                           "detail": detector_state[i]["detail"]}
        elif not sel:
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
            spec = eval_specs[i]
            if isinstance(spec, dict):
                name = _store_admitted_detector(conn, run_id, config, cand, spec,
                                                detector_state[i]["params"])
            else:
                name = _store_admitted(conn, run_id, config, cand)
            # every admitted edge is registered for forward trust grading
            # (SC-109); fold 1 is the probation window
            grading.get_scheme(config.grading_scheme).register(
                conn, cand_id, fold_length_days=config.fold_length_days)
            admitted.append({"candidate_id": cand_id,
                             "candidate_hash": cand["candidate_hash"],
                             "definition": name})

    ledger.set_status(conn, run_id, "evaluated")
    ledger.set_status(conn, run_id, "complete")

    # In-run SPA gate (#87): every completed non-empty run carries its own
    # selection-aware verdict, computed against the run's LIVE market data
    # (same process, same world — verification passes by construction) and
    # recorded append-only. The budget gate becomes self-sustaining; the
    # post-run `discover spa` command remains for re-checks after data
    # changes. Family-0 runs skip: nothing to test.
    spa_result = None
    if family_size > 0:
        from gefion.regimes.discovery import spa as dspa
        try:
            result = dspa.reverdict(conn, run_id, iterations=1000,
                                    seed=config.seed, market=market)
            result["verification"]["in_run"] = True
            ledger.record_spa_reverdict(conn, run_id, result)
            spa_result = {k: result[k] for k in
                          ("p_consistent", "p_lower", "p_upper", "level",
                           "passed", "family_size", "block_length")}
        except dspa.SpaRefusal as exc:
            # A refusal (e.g. expressive-tier reconstruction, v1 limitation)
            # never fails a completed run — it lands in the diagnostics
            # ledger, visible and structural, and the run simply carries no
            # in-run verdict (the budget gate will say "SPA not yet run").
            ledger.record_diagnostic(
                conn, run_id, "spa_inrun_refused", {"reason": str(exc)},
                sample_dependent=False)

    return {
        "run_id": run_id,
        "status": "complete",
        "n_candidates": len(candidates),
        "n_selected": sum(selected),
        "family_size": family_size,
        "n_admitted": len(admitted),
        "admitted": admitted,
        "spa": spa_result,
    }


def _enumerate_expressive(conn, run_id, config, market, candidates, eval_specs):
    """Expressive-tier candidates: agent-supplied free-form ASTs and sandboxed
    detector specs. Both pass availability/entanglement screens; malformed
    free-form expressions refuse the run loudly (they are caller input)."""
    signal_set = set(config.signals)
    for expr in config.freeform:
        try:
            validate_expression(expr)
        except RegimeExpressionError as exc:
            raise DiscoveryError(f"invalid freeform expression: {exc}") from exc
        refs = sorted({leaf["feature"] for leaf in iter_leaves(expr)
                       if leaf.get("feature")})
        missing = [r for r in refs if r not in market.features]
        if missing:
            ledger.record_diagnostic(
                conn, run_id, kind="uncomputable_proposal",
                detail={"expression": expr, "missing": missing},
                sample_dependent=False)
            continue
        entangled = [r for r in refs if r in signal_set]
        if entangled:
            ledger.record_diagnostic(
                conn, run_id, kind="entangled",
                detail={"expression": expr, "features": entangled,
                        "reason": "freeform expression conditions on a target signal"},
                sample_dependent=False)
            continue
        if expr.get("cmp") == "quantile":
            bucketing = {"labels": list(grammar.TERCILE_LABELS), "method": "tercile"}
        else:
            bucketing = {"labels": list(grammar.BOOLEAN_LABELS), "method": "comparison"}
        candidates.append({
            "candidate_hash": f"expressive:{grammar.candidate_hash(expr)}",
            "expression": expr,
            "tier": "expressive",
            "provenance": {"kind": "freeform", "atom_features": refs,
                           "bucketing": bucketing},
        })
        eval_specs.append(grammar.Candidate(
            expression=expr, bucketing=bucketing, depth=0,
            atom_features=tuple(refs)))

    for det in config.detectors:
        feature = det["feature"]
        if feature not in market.features:
            ledger.record_diagnostic(
                conn, run_id, kind="uncomputable_proposal",
                detail={"detector": det["name"], "feature": feature,
                        "reason": "feature not available in dataset"},
                sample_dependent=False)
            continue
        if feature in signal_set:
            ledger.record_diagnostic(
                conn, run_id, kind="entangled",
                detail={"detector": det["name"], "feature": feature,
                        "reason": "detector conditions on a target signal"},
                sample_dependent=False)
            continue
        code_sha = hashlib.sha256(det["code"].encode("utf-8")).hexdigest()
        expr = {"detector": {"name": det["name"], "feature": feature,
                             "code_sha": code_sha}}
        candidates.append({
            "candidate_hash": f"expressive:{grammar.candidate_hash(expr)}",
            "expression": expr,
            "tier": "expressive",
            "provenance": {"kind": "detector", "detector": det["name"],
                           "feature": feature, "code_sha": code_sha,
                           "principle_id": (det.get("provenance") or {}).get("principle_id"),
                           "bucketing": {"labels": ["high", "low"],
                                         "method": "detector"}},
        })
        eval_specs.append(det)  # a dict spec marks a detector candidate


def _bucket_tests_for(conn, run_id, config, src, cand, labels, start, end, stage):
    """Bucket tests for pre-computed labels (detector candidates)."""
    tests: List[Dict[str, Any]] = []
    for signal in config.signals:
        for test in edges.tier2_bucket_tests(
                src, signal=signal, labels_by_date=labels,
                start=start, end=end, min_effective_n=config.min_effective_n):
            test["candidate_hash"] = cand["candidate_hash"]
            tests.append(test)
            if test["pvalue"] is None:
                ledger.record_diagnostic(
                    conn, run_id, kind="min_sample_refusal",
                    detail={"stage": stage, "signal": signal, "bucket": test["bucket"],
                            "candidate_hash": cand["candidate_hash"],
                            "effective_n": test["effective_n"],
                            "floor": config.min_effective_n},
                    sample_dependent=True)
    return tests


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


def _store_admitted_detector(conn, run_id: int, config: DiscoveryConfig,
                             cand: Dict[str, Any], det: Dict[str, Any],
                             fitted_params: Dict[str, Any]) -> str:
    """An admitted detector becomes a feature_functions row plus a
    RegimeDefinition with a detector_function leaf (005 FR-019a, whose
    runtime this feature provides)."""
    short = cand["candidate_hash"].split(":")[-1][:8]
    fn_name = f"detector-{config.name}-{short}"
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions
                   (name, version, status, language, function_body, description,
                    created_by, checksum)
               VALUES (%s, 'v1', 'active', 'python', %s, %s, 'regime-discovery', %s)
               ON CONFLICT (name, version) DO UPDATE SET function_body = EXCLUDED.function_body
               RETURNING id""",
            (fn_name, det["code"],
             f"admitted regime-discovery detector (run {run_id})",
             cand["provenance"]["code_sha"]),
        )
        function_id = cur.fetchone()[0]
    name = f"disc-{config.name}-{short}"
    defn = RegimeDefinition(
        name=name, scope="market",
        expression={"leaf": "detector_function", "function_id": function_id,
                    "scope": "market"},
        bucketing=cand["provenance"]["bucketing"],
        origin="machine",
        descriptive_metadata={"discovery_run_id": run_id, "tier": "expressive",
                              "candidate_hash": cand["candidate_hash"],
                              "detector": det["name"],
                              "conditioning_feature": det["feature"],
                              "fitted_params": fitted_params,
                              "seed": config.seed},
    )
    store_definition(conn, defn)
    return name


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
