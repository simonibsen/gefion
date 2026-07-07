"""Regime-discovery experiment type (006, T032 — US3, FR-109).

Adapts an agentic regime-discovery run into the experiment-cycle framework:
cycles can budget, propose, and (after HUMAN approval — risk class high,
never auto-approved) run discovery like any experiment. A "trial" is one
full discovery run; the cycle's candidate budget maps onto the run's
per-cycle candidate budget, and the honest gates all live inside the run
itself (nested segregation, inner screen, one flat FDR family, ledgers).

Note: a regime_discovery experiment deliberately earns NO cycle-level
holdout p-value — its admitted regimes carry their own, stricter gate.
`holdout_p_value` stays NULL, which fails closed at the cycle level; the
run's ledger (`gefion regime discover ledger <run>`) is the result.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg

from gefion.observability import create_span, set_attributes


class RegimeDiscoveryExperiment:
    """Evaluator: one params dict -> one seeded discovery run."""

    def __init__(self, name: str, config: Dict[str, Any], db_url: str):
        self.name = name
        self.config = config
        self.db_url = db_url

    # -- proposal-time helpers (pure; unit-testable without a DB) -----------

    def resolve_atoms(self, available_features: List[str]) -> List[Dict[str, Any]]:
        """Explicit atoms win; otherwise seed from the catalog principles
        named in the config — every seeded atom carries provenance to its
        principle (US3 acceptance 1)."""
        from gefion.experiments.principles import load_principles
        from gefion.regimes.discovery.grammar import seed_atoms_from_principles

        if self.config.get("atoms"):
            return list(self.config["atoms"])
        principle_ids = set(self.config.get("principle_ids") or [])
        if not principle_ids:
            return []
        principles = [p for p in load_principles() if p["id"] in principle_ids]
        return seed_atoms_from_principles(principles, available_features)

    def resolve_detectors(self, available_features: List[str]) -> List[Dict[str, Any]]:
        from gefion.experiments.principles import load_principles
        from gefion.regimes.discovery.detectors import seed_detectors_from_principles

        if "expressive" not in self.config.get("tiers", []):
            return []
        if self.config.get("detectors"):
            return list(self.config["detectors"])
        principle_ids = set(self.config.get("principle_ids") or [])
        if not principle_ids:
            return []
        principles = [p for p in load_principles() if p["id"] in principle_ids]
        return seed_detectors_from_principles(principles, available_features)

    def discovery_config(self, seed: int, atoms: Optional[List[Dict[str, Any]]] = None,
                         detectors: Optional[List[Dict[str, Any]]] = None):
        """The pre-registration this experiment declares (cycle budget ->
        candidate budget)."""
        from gefion.regimes.discovery.runner import DiscoveryConfig

        fresh = self.config.get("fresh_holdout")
        if isinstance(fresh, (list, tuple)) and len(fresh) == 2:
            import datetime
            fresh = (datetime.date.fromisoformat(str(fresh[0])),
                     datetime.date.fromisoformat(str(fresh[1])))
        return DiscoveryConfig(
            name=f"{self.name}-s{seed}",
            seed=seed,
            atoms=atoms if atoms is not None else list(self.config.get("atoms", [])),
            signals=list(self.config.get("signals", [])),
            depth=int(self.config.get("depth", 2)),
            budget=int(self.config.get("candidate_budget", 100)),
            tiers=tuple(self.config.get("tiers", ("interaction", "grammar"))),
            signal_source=self.config.get("signal_source", "features"),
            grading_scheme=self.config.get("grading_scheme", "walk_forward"),
            universe_filter=self.config.get("universe_filter"),
            horizon_days=int(self.config.get("horizon_days", 1)),
            holdout_weeks=int(self.config.get("holdout_weeks", 6)),
            fresh_holdout=fresh,
            freeform=list(self.config.get("freeform", [])),
            detectors=detectors if detectors is not None else [],
            reserve_justification=self.config.get("reserve_justification"),
            dataset_version=self.config.get("dataset", "dev"),
        )

    # -- trial execution ------------------------------------------------------

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run one seeded discovery; metrics are informational (the honest
        verdicts live in the run's own ledger)."""
        from gefion.regimes.discovery.runner import run_discovery
        from gefion.regimes.discovery.signals import load_market_data

        seed = int(params.get("seed", self.config.get("seed", 42)))
        with create_span("experiments.regime_discovery.evaluate",
                         experiment=self.name, seed=seed) as span:
            conn = psycopg.connect(self.db_url)
            conn.autocommit = True
            try:
                signals = list(self.config.get("signals", []))
                if not signals:
                    with conn.cursor() as cur:
                        cur.execute("SELECT name FROM feature_definitions "
                                    "WHERE active = true ORDER BY name")
                        signals = [r[0] for r in cur.fetchall()]
                    self.config["signals"] = signals
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM feature_definitions ORDER BY name")
                    available = [r[0] for r in cur.fetchall()]
                atoms = self.resolve_atoms(available)
                det = self.resolve_detectors(available)
                atom_features = sorted({a["feature"] for a in atoms}
                                       | {d["feature"] for d in det})
                market = load_market_data(
                    conn, sorted(set(signals) | set(atom_features)),
                    horizon_days=int(self.config.get("horizon_days", 1)),
                    dataset_version=self.config.get("dataset", "dev"),
                    optional_features=atom_features)
                summary = run_discovery(
                    conn, self.discovery_config(seed, atoms=atoms, detectors=det),
                    market)
            finally:
                conn.close()
            set_attributes(span, run_id=summary["run_id"],
                           n_admitted=summary["n_admitted"])
            return {
                "run_id": summary["run_id"],
                "n_candidates": summary["n_candidates"],
                "n_selected": summary["n_selected"],
                "family_size": summary["family_size"],
                "n_admitted": summary["n_admitted"],
            }
