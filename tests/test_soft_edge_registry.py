"""Soft-edge registry drift checks (issue #155).

Gefion's code-as-data design uses deliberate non-foreign-key (non-FK)
edges: by-name references (`feature_definitions.function_name`),
expression-embedded names (regime expressions), JavaScript Object
Notation Binary (JSONB) provenance stamps, and the registry-routed
`computed_features.data_id`. Each is compensated by a validator or a
deletion-door scan — but until now the pattern relied on remembering to
add the compensation for each new soft edge.

These tests turn that discipline into enforcement, in the same style as
tests/test_docs_drift.py: the registry (`gefion.db.schema.SOFT_EDGES`)
declares every soft edge with its compensation; each declared dotted
path must import to a callable; and every deletion-door plan function in
the codebase must be claimed by at least one registry entry — so a new
deletion door (which every new soft edge gets, per the #76 house
pattern) without a registry entry is red CI.

A registered edge with `validator=None` is a DECLARED, documented gap
(`no_validator_reason` must say why) — visible in the registry, never
silent.
"""
from __future__ import annotations

import importlib
import inspect
import pathlib

import pytest

REPO = pathlib.Path(__file__).parent.parent

# The four soft edges named in issue #155 — the registry must always
# cover at least these (guards against the registry rotting away).
ISSUE_155_EDGES = {
    "feature_definitions.function_name",
    "regime_definitions.expression",
    "computed_features.data_id",
    "universe_name",  # JSONB provenance stamps
}


def _registry():
    from gefion.db.schema import SOFT_EDGES

    return SOFT_EDGES


def _edge_ids():
    return [e.name for e in _registry()]


def _resolve(dotted: str):
    """Import a dotted path to the object it names."""
    module, _, attr = dotted.rpartition(".")
    return getattr(importlib.import_module(module), attr)


class TestRegistryShape:
    def test_registry_nonempty(self):
        assert len(_registry()) >= 4, (
            "SOFT_EDGES must declare at least the four edges from #155"
        )

    def test_edge_names_unique(self):
        names = _edge_ids()
        assert len(names) == len(set(names)), "duplicate SoftEdge.name"

    @pytest.mark.parametrize("fragment", sorted(ISSUE_155_EDGES))
    def test_issue_named_edges_present(self, fragment):
        corpus = " | ".join(f"{e.from_ref} -> {e.to_ref}" for e in _registry())
        assert fragment in corpus, (
            f"issue-#155 soft edge {fragment!r} missing from SOFT_EDGES"
        )


class TestEveryEdgeCompensated:
    """Each entry needs an importable deletion door, and either an
    importable validator or an explicit documented reason for the gap."""

    @pytest.mark.parametrize("name", _edge_ids())
    def test_deletion_door_importable_and_callable(self, name):
        edge = next(e for e in _registry() if e.name == name)
        door = _resolve(edge.deletion_door)
        assert callable(door), f"{edge.deletion_door} is not callable"

    @pytest.mark.parametrize("name", _edge_ids())
    def test_validator_importable_or_gap_documented(self, name):
        edge = next(e for e in _registry() if e.name == name)
        if edge.validator is None:
            assert edge.no_validator_reason.strip(), (
                f"soft edge {edge.name!r} declares no validator and no "
                "reason — either point `validator` at a detection callable "
                "or document why the deletion door alone is sufficient"
            )
        else:
            fn = _resolve(edge.validator)
            assert callable(fn), f"{edge.validator} is not callable"


class TestEveryDeletionDoorRegistered:
    """The completeness hook: every plan_* function in a `deletion.py`
    module (the #76 house pattern) must be claimed by a registry entry.
    Adding a new soft edge means adding a deletion door — and this test
    makes shipping that door without registering the edge red CI."""

    @staticmethod
    def _all_plan_doors():
        doors = set()
        for path in sorted((REPO / "src" / "gefion").glob("*/deletion.py")):
            mod_name = f"gefion.{path.parent.name}.deletion"
            mod = importlib.import_module(mod_name)
            for fname, fn in inspect.getmembers(mod, inspect.isfunction):
                if fname.startswith("plan_") and fn.__module__ == mod_name:
                    doors.add(f"{mod_name}.{fname}")
        return doors

    def test_scan_finds_known_doors(self):
        doors = self._all_plan_doors()
        assert len(doors) >= 8, f"door scan broke — found only {doors}"

    def test_every_door_claimed_by_an_edge(self):
        claimed = {e.deletion_door for e in _registry()}
        unclaimed = sorted(self._all_plan_doors() - claimed)
        assert not unclaimed, (
            f"deletion doors not claimed by any SOFT_EDGES entry: "
            f"{unclaimed} — register the soft edge(s) they compensate in "
            "gefion.db.schema.SOFT_EDGES"
        )

    def test_every_claimed_door_exists_in_scan(self):
        doors = self._all_plan_doors()
        bogus = sorted({e.deletion_door for e in _registry()} - doors)
        assert not bogus, (
            f"SOFT_EDGES claims deletion doors that do not exist: {bogus}"
        )
