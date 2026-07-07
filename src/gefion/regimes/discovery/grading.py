"""Pluggable, forward-only trust grading (006, T036 — US6).

The hard admit/reject gate is decided once, at discovery time; TRUST is a
separate, accruing quantity (FR-122): the probation window is fold 1, every
scheduled re-test appends a `regime_trust_grades` row, and only data
genuinely AFTER the discovery window can confirm. The interface is the
enforcement (FR-122a): there is structurally no API that turns a backward
era-slice into a confirmation — backward slices are stored
`descriptive=true`, visible but never graded (the regime's fitted boundaries
saw that data).

v1 default scheme: walk-forward temporal folds. Alternative schemes (declared
market-structure eras, hybrids) plug in through `get_scheme`.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from gefion.observability import create_span, set_attributes

FOLD_CONFIRM_PVALUE = 0.10
DEFAULT_FOLD_LENGTH_DAYS = 30

# Regime-limited: an admitted edge that fails within its first folds is
# transient alpha — captured, flagged, never trusted as durable (FR-123).
EARLY_FOLDS = 2


class GradingError(ValueError):
    """Raised on an invalid grading operation."""


def get_scheme(name: str) -> "WalkForwardGrading":
    """Resolve a declared grading scheme (pre-registered per run)."""
    if name not in _SCHEMES:
        raise GradingError(f"unknown grading scheme {name!r} "
                           f"(available: {sorted(_SCHEMES)})")
    return _SCHEMES[name]


class WalkForwardGrading:
    """Walk-forward temporal folds; fold 1 is the probation window (R8)."""

    # -- registration ---------------------------------------------------------

    def register(self, conn, candidate_id: int,
                 fold_length_days: int = DEFAULT_FOLD_LENGTH_DAYS) -> None:
        """Register an ADMITTED edge for grading; records the declared fold
        length in the candidate's provenance."""
        from gefion.regimes.discovery import ledger

        with create_span("discovery.grading.register", candidate_id=candidate_id):
            cand = self._candidate(conn, candidate_id)
            if cand["verdict"] != "admitted":
                raise GradingError(
                    f"only admitted edges are graded; candidate {candidate_id} "
                    f"is {cand['verdict']!r}")
            ledger.update_provenance(
                conn, cand["run_id"], cand["candidate_hash"],
                {"grading": {"scheme": "walk_forward",
                             "fold_length_days": int(fold_length_days)}})

    # -- accrual ----------------------------------------------------------------

    def record_forward_result(self, conn, candidate_id: int, fold: int,
                              confirmed: bool,
                              detail: Optional[Dict[str, Any]] = None) -> None:
        """Append one forward fold outcome (fold 1 = the probation window)."""
        self._insert(conn, candidate_id, fold, confirmed, descriptive=False,
                     detail=detail)

    def record_descriptive(self, conn, candidate_id: int, fold: int,
                           outcome: bool,
                           detail: Optional[Dict[str, Any]] = None) -> None:
        """Store a BACKWARD era-slice: display context only, never graded —
        the regime's fitted boundaries saw that data."""
        self._insert(conn, candidate_id, fold, outcome, descriptive=True,
                     detail=detail)

    def _insert(self, conn, candidate_id, fold, confirmed, descriptive, detail):
        from psycopg.types.json import Json

        if fold < 1:
            raise GradingError(f"fold must be >= 1, got {fold}")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO regime_trust_grades
                       (candidate_id, fold, confirmed, descriptive, detail)
                   VALUES (%s, %s, %s, %s, %s)""",
                (candidate_id, fold, bool(confirmed), descriptive,
                 Json(detail) if detail is not None else None),
            )

    # -- fold evaluation (forward-only by construction) ---------------------------

    def evaluate_fold(self, conn, market, candidate_id: int, fold: int) -> Dict[str, Any]:
        """Re-test an admitted edge on its fold window — data genuinely after
        the discovery window, enforced here, not by caller discipline.

        Confirmation = every surviving (signal x bucket) test of the original
        admission shows the edge again (p <= 0.10) on the fold window; a
        low-power or missing re-test fails closed. The outcome is appended as
        a forward result and returned.
        """
        from gefion.regimes.discovery import edges, grammar, ledger
        from gefion.regimes.discovery.signals import FeatureSignalSource

        with create_span("discovery.grading.evaluate_fold",
                         candidate_id=candidate_id, fold=fold) as span:
            if fold < 1:
                raise GradingError(f"fold must be >= 1, got {fold}")
            cand = self._candidate(conn, candidate_id)
            if cand["verdict"] != "admitted":
                raise GradingError(f"candidate {candidate_id} is not admitted")
            if (cand["provenance"] or {}).get("kind") == "detector":
                raise GradingError(
                    "detector fold re-tests are not automated in v1 — evaluate "
                    "externally and record via record_forward_result")
            run = ledger.get_run(conn, cand["run_id"])
            space = run["search_space"]
            grading_cfg = (cand["provenance"] or {}).get("grading", {})
            fold_days = int(grading_cfg.get("fold_length_days",
                                            DEFAULT_FOLD_LENGTH_DAYS))

            discovery_end = datetime.date.fromisoformat(
                run["segregation"]["holdout_end"])
            start = discovery_end + datetime.timedelta(days=(fold - 1) * fold_days + 1)
            end = discovery_end + datetime.timedelta(days=fold * fold_days)
            in_window = [d for d in market.dates() if start <= d <= end]
            if not in_window:
                raise GradingError(
                    f"fold {fold} window {start}..{end} has no data yet — only "
                    "data genuinely after the discovery window can confirm")

            spec = grammar.Candidate(
                expression=cand["expression"],
                bucketing=(cand["provenance"] or {}).get(
                    "bucketing", {"labels": ["true", "false"], "method": "comparison"}),
                depth=(cand["provenance"] or {}).get("depth", 0),
                atom_features=tuple((cand["provenance"] or {}).get("atom_features", ())),
            )
            surviving = [t for t in (cand["results"] or {}).get("tests", [])
                         if t.get("survived")]
            if not surviving:
                raise GradingError("admitted candidate has no surviving tests on record")

            signals = sorted({t["signal"] for t in surviving})
            src = FeatureSignalSource(market, signals,
                                      align_window=int(space.get("align_window", 60)))
            retests = []
            confirmed = True
            for t in surviving:
                if t.get("bucket") is None:  # interaction-tier edge
                    retest = edges.tier1_interaction_test(
                        src, signal=t["signal"],
                        conditioning_feature=spec.atom_features[0],
                        start=start, end=end)
                else:
                    labels = edges.causal_labels(
                        spec, market, window=int(space.get("label_window", 60)))
                    bucket_tests = edges.tier2_bucket_tests(
                        src, signal=t["signal"], labels_by_date=labels,
                        start=start, end=end,
                        min_effective_n=int(space.get("min_effective_n", 20)))
                    retest = next((b for b in bucket_tests
                                   if b["bucket"] == t["bucket"]),
                                  {"pvalue": None, "bucket": t["bucket"],
                                   "signal": t["signal"], "reason": "bucket absent"})
                retests.append(retest)
                if retest["pvalue"] is None or retest["pvalue"] > FOLD_CONFIRM_PVALUE:
                    confirmed = False  # fail-closed: no evidence is not evidence

            detail = {"window": {"start": str(start), "end": str(end)},
                      "threshold": FOLD_CONFIRM_PVALUE, "tests": retests}
            self.record_forward_result(conn, candidate_id, fold, confirmed,
                                       detail=detail)
            set_attributes(span, confirmed=confirmed)
            return {"confirmed": confirmed, "fold": fold, "detail": detail}

    # -- the grade -----------------------------------------------------------------

    def grade(self, conn, candidate_id: int) -> Dict[str, Any]:
        """Aggregate forward rows only; descriptive slices reported separately."""
        with conn.cursor() as cur:
            cur.execute(
                """SELECT fold, confirmed, descriptive FROM regime_trust_grades
                   WHERE candidate_id = %s ORDER BY fold, descriptive""",
                (candidate_id,),
            )
            rows = cur.fetchall()
        forward = [(fold, ok) for fold, ok, descriptive in rows if not descriptive]
        confirmed = sum(1 for _, ok in forward if ok)
        return {
            "folds": len(forward),
            "confirmed": confirmed,
            "grade": (confirmed / len(forward)) if forward else None,
            "regime_limited": any(not ok for fold, ok in forward
                                  if fold <= EARLY_FOLDS),
            "descriptive_slices": sum(1 for *_, descriptive in rows if descriptive),
        }

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _candidate(conn, candidate_id: int) -> Dict[str, Any]:
        cols = ("id", "run_id", "candidate_hash", "expression", "tier",
                "provenance", "results", "verdict")
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(cols)} FROM regime_candidates WHERE id = %s",
                (candidate_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise GradingError(f"candidate {candidate_id} not found")
        return dict(zip(cols, row))


_SCHEMES = {"walk_forward": WalkForwardGrading()}
