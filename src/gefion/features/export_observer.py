"""Runtime observer (#162): proven-but-unexported machine feature functions.

A machine-generated feature function is born in the database and lives its
whole lifecycle there. Once it is ``active``, has survived probation, and has
either aged into durability (active >= N days) or is feeding a production
model, it has earned a place in version control: a restore, migration, or
accidental delete should not be able to erase proven alpha-generating code,
and unexported winners never get code review or diff history.

This observer only RECORDS. It writes exactly one OPEN ``system_observation``
per qualifying ``(function, version)`` that is not captured in the
``feature-functions/`` seed directory. Adoption is a human act — run
``feat-fx-export --functions <name>`` and commit. Observations never act
(the #144 philosophy); the owner gate stays human.

Qualifying conditions (ALL must hold):
  * ``feature_functions.status = 'active'``
  * machine-generated (``created_by IN ('cycle_runner','candidate-gate')`` or
    tagged ``ai-generated``)
  * the promoting experiment survived probation — ``promoted_at`` set,
    ``demoted_at`` NULL, ``results->'probation'->>'status' = 'passed'``
    (not merely applied)
  * active >= ``min_active_days`` days OR consumed by a production model
    (an ``active`` ``ml_models`` row whose dataset feature list resolves,
    through ``feature_definitions``, back to this function)
  * AND no matching export (name + version) exists in the seed directory
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

OBSERVER = "feature_export"
CATEGORY = "improvement"
DEFAULT_MIN_ACTIVE_DAYS = 30


def default_seed_dir() -> Path:
    """The repo/package ``feature-functions/`` directory — version control's
    view of which functions are captured. Mirrors db-init's resolution."""
    import gefion

    return Path(gefion.__file__).parent.parent.parent / "feature-functions"


def exported_pairs(seed_dir: Path) -> Set[Tuple[str, str]]:
    """Every ``(name, version)`` captured in the seed directory.

    Keyed by each JSON body's ``name`` + ``version``, never the filename:
    some committed seeds are bare-named (version only in the body), so a
    filename convention would miss them.
    """
    seed_dir = Path(seed_dir)
    if not seed_dir.exists():
        return set()
    pairs: Set[Tuple[str, str]] = set()
    for path in sorted(seed_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        name, version = doc.get("name"), doc.get("version")
        if name is not None and version is not None:
            pairs.add((str(name), str(version)))
    return pairs


def observation_text(name: str, version: str) -> str:
    """The observation text (shape fixed by #162)."""
    return (
        f"function {name} v{version} is a proven winner and is not captured "
        f"in version control — consider `feat-fx-export --functions {name}` "
        "+ commit."
    )


def find_unexported_proven_functions(
    conn,
    seed_dir: Optional[Path] = None,
    min_active_days: int = DEFAULT_MIN_ACTIVE_DAYS,
) -> List[Dict[str, Any]]:
    """Machine-generated, active, probation-survived functions that have
    aged or feed production, and are absent from version control.

    Read-only. Returns one dict per qualifying function with the evidence
    the observation records.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ff.name,
                   ff.version,
                   ff.created_by,
                   e.id AS experiment_id,
                   e.promoted_at,
                   (NOW() - e.promoted_at >= make_interval(days => %s))
                       AS aged_in,
                   EXISTS (
                       SELECT 1
                       FROM ml_models m
                       JOIN ml_datasets d ON d.id = m.dataset_id
                       JOIN feature_definitions fd
                           ON fd.name = ANY(d.feature_names)
                       WHERE m.active = TRUE
                         AND fd.function_name = ff.name
                   ) AS consumed_by_prod
            FROM feature_functions ff
            JOIN experiments e
                ON ('exp_' || (e.config->'feature_config'->>'function_name'))
                   = ff.name
            WHERE ff.status = 'active'
              AND (ff.created_by IN ('cycle_runner', 'candidate-gate')
                   OR 'ai-generated' = ANY(ff.tags))
              AND e.promoted_at IS NOT NULL
              AND e.demoted_at IS NULL
              AND (e.results->'probation'->>'status') = 'passed'
            """,
            (min_active_days,),
        )
        rows = cur.fetchall()

    exported = exported_pairs(seed_dir if seed_dir is not None
                              else default_seed_dir())

    qualifying: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for (name, version, created_by, exp_id, promoted_at,
         aged_in, consumed_by_prod) in rows:
        if not (aged_in or consumed_by_prod):
            continue
        if (name, version) in exported:
            continue
        if (name, version) in seen:  # defensive: one row per (name, version)
            continue
        seen.add((name, version))
        qualifying.append({
            "function": name,
            "version": version,
            "created_by": created_by,
            "experiment_id": exp_id,
            "promoted_at": promoted_at.isoformat() if promoted_at else None,
            "reason": "consumed_by_production_model" if consumed_by_prod
            else f"active_ge_{min_active_days}_days",
        })
    return qualifying


def _has_open_observation(conn, name: str, version: str) -> bool:
    """Idempotency guard: one OPEN observation per (function, version)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM system_observations
               WHERE observer = %s AND review_state = 'open'
                 AND evidence->>'function' = %s
                 AND evidence->>'version' = %s
               LIMIT 1""",
            (OBSERVER, name, version),
        )
        return cur.fetchone() is not None


def record_unexported_function_observations(
    conn,
    seed_dir: Optional[Path] = None,
    min_active_days: int = DEFAULT_MIN_ACTIVE_DAYS,
) -> List[int]:
    """Record an observation for each proven-but-unexported machine function.

    Idempotent: a repeated nightly run never creates a duplicate — at most
    one OPEN observation exists per (function, version). Returns the ids of
    observations created on THIS run (empty when nothing new qualifies).
    """
    from gefion import observations

    with create_span("features.export_observer.record",
                     min_active_days=min_active_days) as span:
        candidates = find_unexported_proven_functions(
            conn, seed_dir=seed_dir, min_active_days=min_active_days)

        created: List[int] = []
        for c in candidates:
            name, version = c["function"], c["version"]
            if _has_open_observation(conn, name, version):
                continue
            oid = observations.record(
                conn,
                observer=OBSERVER,
                category=CATEGORY,
                observation=observation_text(name, version),
                evidence=c,
                suggested_action=(
                    f"run `feat-fx-export --functions {name}` and commit the "
                    "exported JSON so the proven function is under version "
                    "control (code review + restore/migration safety)"),
            )
            created.append(oid)

        set_attributes(span, candidates=len(candidates), recorded=len(created))
        return created
