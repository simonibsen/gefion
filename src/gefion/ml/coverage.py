"""Coverage-bias audit at ML dataset build (#191).

The dataset-build chokepoint is where features meet the modeling universe and
bias enters the model. Features split into **universal** (price-based, present
everywhere) and **data-gated** (fundamentals, sentiment, any source some
entities lack). A data-gated feature with uneven coverage lets a model learn a
DATA ARTIFACT masquerading as signal — the canonical case: fundamentals are
~99% covered on NASDAQ but 0% on NYSE, so "missing PE ratio" becomes a proxy
for "is-NYSE".

This module audits every feature's coverage when a dataset is built:

1. **Overall coverage** — fraction of (entity, date) grid rows where the
   feature is non-null.
2. **Coverage by cohort** — coverage within each ``exchange`` and ``sector``
   cohort; a large max-vs-min disparity IS the bias signal.
3. **Missing-not-at-random (MNAR)** — point-biserial correlation of the
   feature's missingness indicator (1 = missing) with the numeric label
   (forward return). Missingness that predicts the target is exploitable
   artifact.

Surfacing follows the grain (#144: observations never act):
  * per-feature coverage is recorded in the dataset provenance
    (``ml_datasets.universe`` JSONB — every model carries its coverage
    profile);
  * each flagged feature emits one OPEN ``system_observation``, idempotent
    across rebuilds (one per dataset+version / feature / bias-kind).

The audit is ADVISORY and NON-BLOCKING: the build proceeds regardless; a human
reviews the flags before training.
"""
from __future__ import annotations

import logging
from typing import (Any, Dict, Hashable, List, Mapping, Optional, Sequence,
                    Set, Tuple)

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

# --- tunable flagging constants (module-level knobs) ----------------------
MIN_COHORT_SIZE = 30
"""Ignore cohorts with fewer members (distinct entities) than this — noise."""

COVERAGE_DISPARITY_THRESHOLD = 0.30
"""max(cohort coverage) - min(cohort coverage) >= this → flag cohort bias."""

MNAR_LABEL_THRESHOLD = 0.10
"""|corr(missingness, label)| >= this → flag missing-not-at-random."""

# A feature is only cohort/MNAR-analysed when its overall coverage is neither
# trivially all-present nor trivially all-absent — outside this band there is
# no missingness variance to correlate and no disparity worth reporting.
PARTIAL_LOW = 0.05
PARTIAL_HIGH = 0.98

OBSERVER = "coverage_audit"
CATEGORY = "anomaly"  # a coverage artifact is a data anomaly (#144 categories)
COHORT_KINDS: Tuple[str, ...] = ("exchange", "sector")

GridKey = Tuple[str, Hashable]  # (symbol, date-ish)


# --------------------------------------------------------------------------
# Pure statistics + report computation (no DB, no pandas)
# --------------------------------------------------------------------------
def point_biserial(missing: Sequence[float],
                   label: Sequence[float]) -> Optional[float]:
    """Point-biserial correlation between a binary missingness indicator and a
    numeric label — mathematically the Pearson correlation of the two.

    Returns ``None`` when it is undefined: fewer than two paired points, or
    either side has zero variance (e.g. a fully-present feature, or a constant
    label).
    """
    import numpy as np

    m = np.asarray(list(missing), dtype=float)
    y = np.asarray(list(label), dtype=float)
    if m.size < 2 or y.size < 2 or m.size != y.size:
        return None
    finite = np.isfinite(m) & np.isfinite(y)
    m, y = m[finite], y[finite]
    if m.size < 2:
        return None
    if float(m.std()) == 0.0 or float(y.std()) == 0.0:
        return None
    r = float(np.corrcoef(m, y)[0, 1])
    if not np.isfinite(r):
        return None
    return r


def _cohort_coverage(
    kind_map: Mapping[str, Optional[str]],
    grid_keys: Sequence[GridKey],
    present: Set[GridKey],
    min_cohort_size: int,
) -> Tuple[Dict[str, float], Dict[str, int]]:
    """Coverage within each sized cohort of one kind.

    Cohort membership is keyed by the entity's cohort value (e.g. 'NYSE').
    A cohort is *sized* when it has >= ``min_cohort_size`` distinct members
    (entities) among the grid's entities. Rows whose entity has no cohort
    value (NULL exchange/sector) are excluded from that kind entirely.

    Returns (coverage-by-cohort, member-count-by-cohort) for sized cohorts.
    """
    rows_by_cohort: Dict[str, int] = {}
    present_by_cohort: Dict[str, int] = {}
    members_by_cohort: Dict[str, Set[str]] = {}
    for key in grid_keys:
        sym = key[0]
        cohort = kind_map.get(sym)
        if cohort is None:
            continue
        rows_by_cohort[cohort] = rows_by_cohort.get(cohort, 0) + 1
        members_by_cohort.setdefault(cohort, set()).add(sym)
        if key in present:
            present_by_cohort[cohort] = present_by_cohort.get(cohort, 0) + 1

    coverage: Dict[str, float] = {}
    sizes: Dict[str, int] = {}
    for cohort, n_rows in rows_by_cohort.items():
        n_members = len(members_by_cohort[cohort])
        if n_members < min_cohort_size:
            continue
        sizes[cohort] = n_members
        coverage[cohort] = present_by_cohort.get(cohort, 0) / n_rows
    return coverage, sizes


def compute_coverage_report(
    *,
    grid_labels: Mapping[GridKey, float],
    presence: Mapping[str, Set[GridKey]],
    cohorts: Mapping[str, Mapping[str, Optional[str]]],
    cohort_eligible: Optional[Mapping[str, bool]] = None,
    min_cohort_size: int = MIN_COHORT_SIZE,
    coverage_disparity_threshold: float = COVERAGE_DISPARITY_THRESHOLD,
    mnar_label_threshold: float = MNAR_LABEL_THRESHOLD,
) -> Dict[str, Any]:
    """Compute the per-feature coverage report over a dataset's grid.

    Args:
        grid_labels: the reference grid — one numeric label per distinct
            (entity, date) row. Its keys ARE the grid; coverage denominators
            come from it. The value is the numeric target (forward return)
            used for the MNAR correlation.
        presence: feature name -> the set of grid keys where the feature is
            non-null. A grid key absent from the set is a missing value.
        cohorts: cohort kind ('exchange'/'sector') -> {symbol: cohort value}.
            Missing/None cohort values are excluded from that kind.
        cohort_eligible: feature name -> whether cohort analysis applies
            (True for per-stock features; False for market/macro features,
            which have no exchange/sector cohort — cohort analysis is skipped
            for them). Defaults to True for every feature.

    Returns a JSON-serialisable report (see module docstring for shape).
    """
    cohort_eligible = cohort_eligible or {}
    grid_keys: List[GridKey] = list(grid_labels.keys())
    n_grid = len(grid_keys)

    features_out: Dict[str, Any] = {}
    flagged: List[Dict[str, Any]] = []

    for feature in sorted(presence.keys()):
        present = presence[feature]
        n_present = sum(1 for k in grid_keys if k in present)
        overall = (n_present / n_grid) if n_grid else 0.0
        eligible = cohort_eligible.get(feature, True)
        partial = PARTIAL_LOW < overall < PARTIAL_HIGH

        feat: Dict[str, Any] = {
            "overall": overall,
            "present": n_present,
            "total": n_grid,
            "scope": "stock" if eligible else "market",
            "by_cohort": {},
            "cohort_sizes": {},
            "mnar_label_corr": None,
            "flags": [],
        }

        # --- cohort coverage + disparity flag (per-stock features only) -----
        if eligible:
            for kind in COHORT_KINDS:
                kind_map = cohorts.get(kind) or {}
                if not kind_map:
                    continue
                coverage, sizes = _cohort_coverage(
                    kind_map, grid_keys, present, min_cohort_size)
                if not coverage:
                    continue
                feat["by_cohort"][kind] = coverage
                feat["cohort_sizes"][kind] = sizes
                if not partial or len(coverage) < 2:
                    continue
                low_cohort = min(coverage, key=coverage.get)
                high_cohort = max(coverage, key=coverage.get)
                disparity = coverage[high_cohort] - coverage[low_cohort]
                if disparity >= coverage_disparity_threshold:
                    flag = {
                        "kind": "cohort_bias",
                        "cohort_kind": kind,
                        "low": low_cohort,
                        "low_coverage": coverage[low_cohort],
                        "high": high_cohort,
                        "high_coverage": coverage[high_cohort],
                        "disparity": disparity,
                    }
                    feat["flags"].append(flag)
                    flagged.append({"feature": feature, **flag})

        # --- MNAR: missingness vs label (partial-coverage features only) ----
        if partial:
            missing_vec: List[float] = []
            label_vec: List[float] = []
            for key in grid_keys:
                label = grid_labels[key]
                if label is None:
                    continue
                missing_vec.append(0.0 if key in present else 1.0)
                label_vec.append(float(label))
            corr = point_biserial(missing_vec, label_vec)
            feat["mnar_label_corr"] = corr
            if corr is not None and abs(corr) >= mnar_label_threshold:
                flag = {"kind": "mnar", "label_corr": corr}
                feat["flags"].append(flag)
                flagged.append({"feature": feature, **flag})

        features_out[feature] = feat

    return {
        "params": {
            "min_cohort_size": min_cohort_size,
            "coverage_disparity_threshold": coverage_disparity_threshold,
            "mnar_label_threshold": mnar_label_threshold,
            "partial_low": PARTIAL_LOW,
            "partial_high": PARTIAL_HIGH,
        },
        "grid_rows": n_grid,
        "features": features_out,
        "flagged": flagged,
    }


# --------------------------------------------------------------------------
# Observation surfacing (#144) — idempotent, non-blocking
# --------------------------------------------------------------------------
def _bias_kind(flag: Mapping[str, Any]) -> str:
    """The dedup discriminator for one flag: one OPEN observation per
    (dataset+version, feature, bias-kind)."""
    if flag["kind"] == "cohort_bias":
        return f"cohort_bias:{flag['cohort_kind']}"
    return str(flag["kind"])


def _pct(x: float) -> str:
    return f"{100.0 * x:.0f}%"


def observation_text(name: str, version: str, feature: str,
                     flag: Mapping[str, Any]) -> str:
    """Human-readable observation text for one flagged feature."""
    head = f"dataset {name} v{version}: feature {feature}"
    if flag["kind"] == "cohort_bias":
        return (
            f"{head} coverage-biased — {flag['low']} {_pct(flag['low_coverage'])}"
            f" vs {flag['high']} {_pct(flag['high_coverage'])} "
            f"({flag['cohort_kind']})")
    if flag["kind"] == "mnar":
        return (f"{head} missing-not-at-random — missingness correlates with "
                f"the label (r={flag['label_corr']:.2f})")
    return f"{head} coverage anomaly"  # pragma: no cover - defensive


def _has_open_observation(conn, name: str, version: str, feature: str,
                          bias_kind: str) -> bool:
    """Idempotency guard: one OPEN observation per
    (dataset+version, feature, bias-kind)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM system_observations
               WHERE observer = %s AND review_state = 'open'
                 AND evidence->>'dataset' = %s
                 AND evidence->>'version' = %s
                 AND evidence->>'feature' = %s
                 AND evidence->>'bias_kind' = %s
               LIMIT 1""",
            (OBSERVER, name, version, feature, bias_kind))
        return cur.fetchone() is not None


def record_coverage_observations(conn, name: str, version: str,
                                 report: Mapping[str, Any]) -> List[int]:
    """Record one OPEN observation per flagged feature (#144).

    Idempotent: a rebuild never duplicates — at most one OPEN observation
    exists per (dataset+version, feature, bias-kind). Returns the ids created
    on THIS call (empty when everything flagged is already open).
    """
    from gefion import observations

    created: List[int] = []
    for flag in report.get("flagged", []):
        feature = flag["feature"]
        bias_kind = _bias_kind(flag)
        if _has_open_observation(conn, name, version, feature, bias_kind):
            continue
        evidence = {
            "dataset": name, "version": version, "feature": feature,
            "bias_kind": bias_kind, **{k: v for k, v in flag.items()
                                       if k != "feature"},
        }
        oid = observations.record(
            conn, observer=OBSERVER, category=CATEGORY,
            observation=observation_text(name, version, feature, flag),
            evidence=evidence,
            suggested_action=(
                "review before training — the model could learn this coverage "
                "artifact as signal. Backfill the missing source for the "
                "under-covered cohort, or exclude the feature from this "
                "dataset."))
        created.append(oid)
    return created


def record_coverage_provenance(conn, name: str, version: str,
                               report: Mapping[str, Any],
                               universe: Optional[Dict[str, Any]] = None) -> None:
    """Stamp the coverage report into the dataset's provenance.

    Writes ``report`` under a ``coverage`` key in ``ml_datasets.universe``
    (the existing provenance JSONB — no new DDL), read-modify-write and
    idempotent (a rebuild overwrites in place). Also mirrors it into the
    in-memory ``universe`` dict when provided, so the manifest file and any
    caller re-upsert carry the same profile.
    """
    from psycopg.types.json import Json

    if universe is not None:
        universe["coverage"] = report

    with conn.cursor() as cur:
        cur.execute("SELECT universe FROM ml_datasets "
                    "WHERE name = %s AND version = %s", (name, version))
        row = cur.fetchone()
        if row is None:
            return  # nothing registered yet — nothing to stamp
        current = dict(row[0] or {})
        current["coverage"] = report
        cur.execute("UPDATE ml_datasets SET universe = %s "
                    "WHERE name = %s AND version = %s",
                    (Json(current), name, version))
    conn.commit()


# --------------------------------------------------------------------------
# DB-backed orchestrator — assembles inputs from the DB + labels, records
# --------------------------------------------------------------------------
def _fetch_presence_and_scope(
    conn, symbols: Sequence[str], feature_names: Sequence[str],
    exclude_features: Optional[Sequence[str]], grid_dates: Set[str],
) -> Tuple[Dict[str, Set[GridKey]], Dict[str, bool]]:
    """Presence (non-null feature values) restricted to the grid, plus each
    feature's cohort-eligibility (per-stock vs market/macro scope).

    Feature selection mirrors the export: whitelist ``feature_names`` when
    given, else blacklist ``exclude_features``. A feature is cohort-eligible
    iff its ``feature_definitions.entity_table = 'stocks'`` — market/macro
    features (any other entity table) are per-date, not per-stock.
    """
    where = ["s.symbol = ANY(%s)", "cf.value IS NOT NULL"]
    params: List[Any] = [list(symbols)]
    if feature_names:
        where.append("fd.name = ANY(%s)")
        params.append(list(feature_names))
    elif exclude_features:
        where.append("fd.name != ALL(%s)")
        params.append(list(exclude_features))

    presence: Dict[str, Set[GridKey]] = {}
    eligible: Dict[str, bool] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT s.symbol, cf.date, fd.name, fd.entity_table
                FROM computed_features cf
                JOIN feature_definitions fd ON fd.id = cf.feature_id
                JOIN stocks s ON s.id = cf.data_id
                WHERE {' AND '.join(where)}""",
            tuple(params))
        for sym, date, fname, entity_table in cur:
            datekey = str(date)
            if datekey not in grid_dates:
                continue
            presence.setdefault(fname, set()).add((sym, datekey))
            eligible[fname] = (entity_table == "stocks")
    return presence, eligible


def _fetch_cohorts(conn, symbols: Sequence[str]
                   ) -> Dict[str, Dict[str, Optional[str]]]:
    """symbol -> exchange / sector maps for the grid's entities."""
    cohorts: Dict[str, Dict[str, Optional[str]]] = {
        "exchange": {}, "sector": {}}
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, exchange, sector FROM stocks "
                    "WHERE symbol = ANY(%s)", (list(symbols),))
        for sym, exchange, sector in cur:
            cohorts["exchange"][sym] = exchange
            cohorts["sector"][sym] = sector
    return cohorts


def audit_dataset_coverage(
    conn, *,
    name: str,
    version: str,
    symbols: Sequence[str],
    feature_names: Sequence[str],
    labels_df,
    exclude_features: Optional[Sequence[str]] = None,
    universe: Optional[Dict[str, Any]] = None,
    record: bool = True,
    min_cohort_size: int = MIN_COHORT_SIZE,
    coverage_disparity_threshold: float = COVERAGE_DISPARITY_THRESHOLD,
    mnar_label_threshold: float = MNAR_LABEL_THRESHOLD,
) -> Dict[str, Any]:
    """Audit a built dataset's per-feature coverage and surface any bias.

    Assembles the inputs from the DB (feature presence, exchange/sector
    cohorts, feature scope) and the in-memory ``labels_df`` (the reference
    grid + numeric target), computes the report, and — when ``record`` —
    stamps it into the dataset provenance and emits one idempotent OPEN
    observation per flagged feature.

    ``labels_df`` is a pandas DataFrame with at least ``symbol``, ``date`` and
    ``forward_return`` columns (as assembled by the dataset export). The grid
    is its distinct (symbol, date) rows; the numeric label per grid row is the
    mean forward return across horizons.

    Non-blocking and best-effort: returns the report; recording failures are
    the caller's to tolerate (the build must proceed regardless).
    """
    with create_span("ml.coverage.audit_dataset_coverage",
                     dataset=name, version=version) as span:
        if labels_df is None or len(labels_df) == 0 or not len(symbols):
            set_attributes(span, grid_rows=0, flagged=0)
            return {"grid_rows": 0, "features": {}, "flagged": []}

        # Reference grid: distinct (symbol, date) with a numeric label (mean
        # forward return across horizons). Dates normalised to strings so the
        # grid and the DB presence keys join reliably.
        df = labels_df.copy()
        df["date"] = df["date"].astype(str)
        grouped = df.groupby(["symbol", "date"])["forward_return"].mean()
        grid_labels: Dict[GridKey, float] = {
            (sym, date): float(val)
            for (sym, date), val in grouped.items()
        }
        grid_dates: Set[str] = {k[1] for k in grid_labels}

        presence, eligible = _fetch_presence_and_scope(
            conn, symbols, feature_names, exclude_features, grid_dates)
        cohorts = _fetch_cohorts(conn, symbols)

        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence=presence,
            cohorts=cohorts,
            cohort_eligible=eligible,
            min_cohort_size=min_cohort_size,
            coverage_disparity_threshold=coverage_disparity_threshold,
            mnar_label_threshold=mnar_label_threshold,
        )

        if record:
            record_coverage_provenance(conn, name, version, report,
                                       universe=universe)
            record_coverage_observations(conn, name, version, report)

        set_attributes(span, grid_rows=report["grid_rows"],
                       features=len(report["features"]),
                       flagged=len(report["flagged"]))
        return report
