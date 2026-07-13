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


def due_folds(conn, today: Optional[datetime.date] = None) -> list:
    """Every fully-elapsed, ungraded fold across ALL admitted edges (#105).

    Due-ness anchors at the run's recorded holdout_end. `trust_bearing` is
    the vintage guard: True only when the fold window ends after the run
    EXECUTED — pre-execution windows (a --max-date run's post-vintage span)
    are procedure evidence, reported but never auto-graded as forward
    results. Folds with ANY recorded row (evidence, no-evidence, or
    descriptive) are not due; a no-evidence row is re-run only manually.
    """
    today = today or datetime.date.today()
    out = []
    with create_span("discovery.grading.due_folds") as span:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id, c.run_id, c.provenance,
                          r.segregation->>'holdout_end', r.created_at
                   FROM regime_candidates c
                   JOIN regime_discovery_runs r ON r.id = c.run_id
                   WHERE c.verdict = 'admitted'
                   ORDER BY c.id""")
            candidates = cur.fetchall()
            for cand_id, run_id, prov, holdout_end_s, created_at in candidates:
                fold_days = int(((prov or {}).get("grading") or {}).get(
                    "fold_length_days", DEFAULT_FOLD_LENGTH_DAYS))
                holdout_end = datetime.date.fromisoformat(holdout_end_s)
                executed = (created_at.date()
                            if hasattr(created_at, "date") else created_at)
                cur.execute("SELECT fold FROM regime_trust_grades "
                            "WHERE candidate_id = %s", (cand_id,))
                graded = {r[0] for r in cur.fetchall()}
                fold = 1
                while True:
                    end = holdout_end + datetime.timedelta(days=fold * fold_days)
                    if end >= today:
                        break
                    if fold not in graded:
                        start = holdout_end + datetime.timedelta(
                            days=(fold - 1) * fold_days + 1)
                        out.append({"candidate_id": cand_id, "run_id": run_id,
                                    "fold": fold, "window_start": start,
                                    "window_end": end,
                                    "trust_bearing": end > executed})
                    fold += 1
        set_attributes(span, due=len(out))
    return out


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
        """Register (or re-declare) grading for an ADMITTED edge.

        The grading config is a forward-looking declaration, so it may be
        re-declared — e.g. to widen folds that turned out too narrow to ever
        hold enough regime episodes (issue #67) — but only UNTIL real evidence
        exists: once any non-refused forward row is recorded, moving the fold
        boundaries would be selection, and the grid is locked.
        """
        from psycopg.types.json import Json

        with create_span("discovery.grading.register", candidate_id=candidate_id):
            cand = self._candidate(conn, candidate_id)
            if cand["verdict"] != "admitted":
                raise GradingError(
                    f"only admitted edges are graded; candidate {candidate_id} "
                    f"is {cand['verdict']!r}")
            if any(not row["refused"] for row in self._forward_rows(conn, candidate_id)):
                raise GradingError(
                    "grading grid is locked: forward evidence already exists for "
                    f"candidate {candidate_id} — fold boundaries cannot move after "
                    "outcomes have been seen")
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE regime_candidates
                       SET provenance = COALESCE(provenance, '{}'::jsonb) || %s::jsonb
                       WHERE id = %s""",
                    (Json({"grading": {"scheme": "walk_forward",
                                       "fold_length_days": int(fold_length_days)}}),
                     candidate_id),
                )

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
        """Append one grade row. Evidence rows are immutable; a no-evidence
        placeholder (detail.refused) may be replaced — e.g. after the grid is
        re-declared with wider folds (issue #67)."""
        from psycopg.types.json import Json

        if fold < 1:
            raise GradingError(f"fold must be >= 1, got {fold}")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, COALESCE(detail->>'refused', 'false') = 'true'
                   FROM regime_trust_grades
                   WHERE candidate_id = %s AND fold = %s AND descriptive = %s""",
                (candidate_id, fold, descriptive),
            )
            existing = cur.fetchone()
            if existing is not None:
                row_id, was_refused = existing
                if not was_refused:
                    raise GradingError(
                        f"fold {fold} already holds evidence for candidate "
                        f"{candidate_id} — grade rows are immutable")
                cur.execute(
                    """UPDATE regime_trust_grades
                       SET confirmed = %s, detail = %s, graded_at = NOW()
                       WHERE id = %s""",
                    (bool(confirmed), Json(detail) if detail is not None else None,
                     row_id),
                )
                return
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
            # Vintage guard: a fold window that ended before the RUN EXECUTED
            # lies in data the operator had already seen when declaring
            # --max-date — procedure evidence only. It records with the
            # descriptive flag: visible, never counted, never grid-locking.
            executed = run["created_at"]
            executed = executed.date() if hasattr(executed, "date") else executed
            descriptive = end <= executed
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

            detail = {"window": {"start": str(start), "end": str(end)},
                      "threshold": FOLD_CONFIRM_PVALUE, "tests": retests}
            evaluable = [t for t in retests if t["pvalue"] is not None]
            if not evaluable:
                # Absent evidence is not contradicting evidence (issue #67):
                # the fold window couldn't power a single re-test. Record a
                # replaceable no-evidence row, excluded from the grade.
                detail["refused"] = True
                self._insert(conn, candidate_id, fold, confirmed=False,
                             descriptive=descriptive, detail=detail)
                set_attributes(span, refused=True, descriptive=descriptive)
                return {"refused": True, "confirmed": None, "fold": fold,
                        "descriptive": descriptive, "detail": detail}

            # judged on the evaluable re-tests; unpowered ones stay visible
            confirmed = all(t["pvalue"] <= FOLD_CONFIRM_PVALUE for t in evaluable)
            if descriptive:
                detail["vintage_span"] = True
                self.record_descriptive(conn, candidate_id, fold, confirmed,
                                        detail=detail)
            else:
                self.record_forward_result(conn, candidate_id, fold, confirmed,
                                           detail=detail)
            set_attributes(span, confirmed=confirmed, descriptive=descriptive)
            return {"refused": False, "confirmed": confirmed, "fold": fold,
                    "descriptive": descriptive, "detail": detail}

    # -- the grade -----------------------------------------------------------------

    def grade(self, conn, candidate_id: int) -> Dict[str, Any]:
        """Aggregate forward EVIDENCE rows only: no-evidence (power-refused)
        folds are reported but never enter the denominator or the
        regime-limited flag — absent evidence is not weakness (issue #67).
        Descriptive slices reported separately."""
        rows = self._forward_rows(conn, candidate_id)
        evidence = [r for r in rows if not r["refused"]]
        confirmed = sum(1 for r in evidence if r["confirmed"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM regime_trust_grades "
                "WHERE candidate_id = %s AND descriptive",
                (candidate_id,),
            )
            descriptive = cur.fetchone()[0]
        return {
            "folds": len(evidence),
            "confirmed": confirmed,
            "grade": (confirmed / len(evidence)) if evidence else None,
            "regime_limited": any(not r["confirmed"] for r in evidence
                                  if r["fold"] <= EARLY_FOLDS),
            "no_evidence": len(rows) - len(evidence),
            "descriptive_slices": descriptive,
        }

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _forward_rows(conn, candidate_id: int):
        """Forward (non-descriptive) rows with their no-evidence flag."""
        with conn.cursor() as cur:
            cur.execute(
                """SELECT fold, confirmed,
                          COALESCE(detail->>'refused', 'false') = 'true'
                   FROM regime_trust_grades
                   WHERE candidate_id = %s AND NOT descriptive
                   ORDER BY fold""",
                (candidate_id,),
            )
            return [{"fold": fold, "confirmed": ok, "refused": refused}
                    for fold, ok, refused in cur.fetchall()]

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
