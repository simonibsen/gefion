"""Universe deletion door (spec 015, house pattern from #76).

Dry-run by default, dependency enumeration, refusal while referenced.
References are provenance stamps: datasets (ml_datasets.universe JSONB),
experiments (config JSONB), and discovery runs (search_space JSONB) that
recorded this universe's name. Provenance records are NEVER mutated — a
referenced universe refuses deletion (results must stay attributable).
The default universe refuses always (consumers resolve through it).
"""
from __future__ import annotations

from typing import Any, Dict

from gefion.observability import create_span, set_attributes
from gefion.universe.definitions import get_universe


def plan_universe_delete(conn, name: str) -> Dict[str, Any]:
    """Dry-run: the full blast radius, changing nothing."""
    with create_span("universe.deletion.plan", universe=name) as span:
        u = get_universe(conn, name)
        if u is None:
            raise ValueError(f"no universe named {name!r}")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM universe_exclusions "
                        "WHERE universe_id = %s", (u["id"],))
            intervals = cur.fetchone()[0]
            cur.execute(
                "SELECT name, version FROM ml_datasets "
                "WHERE universe ->> 'universe_name' = %s "
                "ORDER BY name, version", (name,))
            datasets = [f"{r[0]}:{r[1]}" for r in cur.fetchall()]
            cur.execute(
                "SELECT id FROM experiments "
                "WHERE config ->> 'universe' IS NOT NULL "
                "AND config -> 'universe' ->> 'universe_name' = %s "
                "ORDER BY id", (name,))
            experiments = [r[0] for r in cur.fetchall()]
            try:
                cur.execute(
                    "SELECT id FROM regime_discovery_runs "
                    "WHERE search_space -> 'universe' ->> 'universe_name' = %s "
                    "ORDER BY id", (name,))
                runs = [r[0] for r in cur.fetchall()]
            except Exception:
                runs = []
        blockers = []
        if u["is_default"]:
            blockers.append("this is the DEFAULT universe — set another "
                            "default first")
        if datasets:
            blockers.append(f"{len(datasets)} dataset(s) record this "
                            f"universe in provenance")
        if experiments:
            blockers.append(f"{len(experiments)} experiment(s) record this "
                            f"universe in provenance")
        if runs:
            blockers.append(f"{len(runs)} discovery run(s) record this "
                            f"universe in provenance")
        plan = {"universe": {"name": u["name"],
                             "fingerprint": u["fingerprint"],
                             "is_default": u["is_default"]},
                "exclusion_intervals": intervals,
                "dataset_references": datasets,
                "experiment_references": experiments,
                "discovery_run_references": runs,
                "blockers": blockers,
                "deletable": not blockers}
        set_attributes(span, intervals=intervals, n_blockers=len(blockers))
        return plan


def execute_universe_delete(conn, name: str) -> Dict[str, Any]:
    """Delete intervals (cascade) then the definition. Any blocker refuses —
    provenance is never orphaned or mutated."""
    with create_span("universe.deletion.execute", universe=name) as span:
        plan = plan_universe_delete(conn, name)
        if plan["blockers"]:
            raise ValueError(
                f"refusing to delete universe {name!r}: "
                + "; ".join(plan["blockers"]))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM universe_definitions WHERE name = %s",
                        (name,))
        conn.commit()
        set_attributes(span, deleted=True)
        return {"deleted": name,
                "exclusion_intervals": plan["exclusion_intervals"]}
