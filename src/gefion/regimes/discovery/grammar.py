"""Bounded compositional grammar for regime discovery (006, T006).

The pre-registered primitive library (atoms) and its deterministic, EXACT
enumeration to composition depth K. Exactness is load-bearing: the realized
candidate count is the input to the FDR family denominator (FR-104/120), so
enumeration must be reproducible, canonical (AND(a,b) == AND(b,a)), and
refuse — never silently truncate — beyond the hard depth cap.

Candidates are ordinary spec-005 RegimeExpression ASTs, so a discovered
regime is storable/chartable/sliceable with zero new machinery (R1).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from itertools import combinations
from typing import Any, Dict, List

from gefion.observability import create_span, set_attributes
from gefion.regimes.definitions import COMPARATORS

# Raising the depth cap beyond 2 is gated on the FR-108 data-snooping-robust
# bootstrap fast-follow: compositional search grows ~2^M and flat BH over the
# realized family is only defensibly honest at v1's capped volumes.
HARD_DEPTH_CAP = 2

TERCILE_LABELS = ["low", "mid", "high"]
BOOLEAN_LABELS = ["true", "false"]

_BOOLEAN_CMPS = tuple(c for c in COMPARATORS if c not in ("in", "quantile"))


class GrammarError(ValueError):
    """Raised when an atom library or requested enumeration is invalid."""


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One enumerated candidate regime: a 005 AST plus discovery metadata."""

    expression: Dict[str, Any]
    bucketing: Dict[str, Any]
    depth: int
    atom_features: tuple  # conditioning features used (entanglement/availability checks)
    principles: tuple = ()  # seeding principle ids (provenance, FR-106)


# --- atom library -----------------------------------------------------------

def validate_atoms(atoms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate an atom library and return it in canonical (sorted) order.

    An atom is either a quantile form ({"feature", "form": "tercile"}) or a
    boolean comparison ({"feature", "cmp", "value"}). Duplicates are refused:
    a library with hidden repeats would double-count the search space.
    """
    with create_span("discovery.grammar.validate_atoms", n_atoms=len(atoms or [])):
        if not atoms:
            raise GrammarError("atom library must be non-empty")
        for atom in atoms:
            feature = atom.get("feature")
            if not isinstance(feature, str) or not feature.strip():
                raise GrammarError(f"atom requires a non-empty feature: {atom!r}")
            if "form" in atom:
                if atom["form"] != "tercile":
                    raise GrammarError(f"unknown atom form: {atom['form']!r}")
            elif "cmp" in atom:
                if atom["cmp"] not in _BOOLEAN_CMPS:
                    raise GrammarError(f"unknown atom comparator: {atom['cmp']!r}")
                if not isinstance(atom.get("value"), (int, float)):
                    raise GrammarError(f"atom comparison requires a numeric value: {atom!r}")
            else:
                raise GrammarError(f"atom is neither a form nor a comparison: {atom!r}")

        ordered = sorted(atoms, key=_atom_key)
        for a, b in zip(ordered, ordered[1:]):
            if _atom_key(a) == _atom_key(b):
                raise GrammarError(f"duplicate atom in library: {a!r}")
        return ordered


def _atom_key(atom: Dict[str, Any]) -> str:
    return json.dumps(atom, sort_keys=True)


def atom_is_boolean(atom: Dict[str, Any]) -> bool:
    """Boolean atoms compose (AND/OR); tercile atoms are 3-bucket leaves only."""
    return "cmp" in atom


def atom_leaf(atom: Dict[str, Any]) -> Dict[str, Any]:
    """The 005 comparison leaf for an atom (market scope in v1)."""
    if atom_is_boolean(atom):
        return {"leaf": "comparison", "feature": atom["feature"],
                "cmp": atom["cmp"], "value": atom["value"], "scope": "market"}
    return {"leaf": "comparison", "feature": atom["feature"],
            "cmp": "quantile", "value": "tercile", "scope": "market"}


# --- canonical form and hashing --------------------------------------------

def canonicalize(expression: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical AST form: AND/OR children sorted by their canonical JSON."""
    if "op" in expression and expression["op"] in ("AND", "OR"):
        children = [canonicalize(c) for c in expression["children"]]
        children.sort(key=lambda c: json.dumps(c, sort_keys=True))
        return {"op": expression["op"], "children": children}
    return expression


def candidate_hash(expression: Dict[str, Any]) -> str:
    """Content hash (canonical-JSON SHA-256) — ledger identity, dedup, resume."""
    canonical = json.dumps(canonicalize(expression), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- enumeration ------------------------------------------------------------

def enumerate_candidates(atoms: List[Dict[str, Any]], depth: int) -> List[Candidate]:
    """Deterministically enumerate every candidate up to composition depth K.

    depth 1: one candidate per atom. depth 2: every AND and OR over pairs of
    distinct BOOLEAN atoms (tercile atoms don't type-check under boolean ops).
    The returned count IS the search-space size — no sampling, no truncation.
    """
    with create_span("discovery.grammar.enumerate", depth=depth) as span:
        if depth < 1:
            raise GrammarError(f"composition depth must be >= 1, got {depth}")
        if depth > HARD_DEPTH_CAP:
            raise GrammarError(
                f"composition depth {depth} exceeds the hard cap {HARD_DEPTH_CAP} "
                "(raising it is gated on the FR-108 bootstrap fast-follow)")

        ordered = validate_atoms(atoms)
        out: List[Candidate] = []
        for atom in ordered:
            bucketing = ({"labels": list(BOOLEAN_LABELS), "method": "comparison"}
                         if atom_is_boolean(atom)
                         else {"labels": list(TERCILE_LABELS), "method": "tercile"})
            out.append(Candidate(expression=atom_leaf(atom), bucketing=bucketing,
                                 depth=1, atom_features=(atom["feature"],),
                                 principles=_atom_principles(atom)))

        if depth >= 2:
            booleans = [a for a in ordered if atom_is_boolean(a)]
            for a, b in combinations(booleans, 2):
                for op in ("AND", "OR"):
                    expr = canonicalize(
                        {"op": op, "children": [atom_leaf(a), atom_leaf(b)]})
                    out.append(Candidate(
                        expression=expr,
                        bucketing={"labels": list(BOOLEAN_LABELS), "method": "comparison"},
                        depth=2,
                        atom_features=tuple(sorted({a["feature"], b["feature"]})),
                        principles=tuple(sorted(set(_atom_principles(a)
                                                    + _atom_principles(b)))),
                    ))

        set_attributes(span, n_candidates=len(out))
        return out


def _atom_principles(atom: Dict[str, Any]) -> tuple:
    pid = (atom.get("provenance") or {}).get("principle_id")
    return (pid,) if pid else ()


# --- principle seeding (T031) -------------------------------------------------

def match_features(requirement: str, available: List[str]) -> List[str]:
    """Feature names matching a principle data-requirement token.

    A requirement like ``volatility_realized`` matches ``realized_vol_20``:
    every underscore-part's 3-char stem must appear in the feature name.
    Deterministic (sorted) and deliberately conservative — an unmatched
    requirement seeds nothing (the availability inventory rejects fabrication).
    """
    stems = [part[:3] for part in requirement.lower().split("_") if part]
    return sorted(f for f in available if all(stem in f.lower() for stem in stems))


def seed_atoms_from_principles(principles: List[Dict[str, Any]],
                               available_features: List[str]) -> List[Dict[str, Any]]:
    """Build a bounded atom library from catalog principles (US3).

    Only `features.*` data requirements seed atoms, and only when they match
    a feature that actually exists — uncomputable proposals are rejected at
    the source (FR-121). Each atom carries provenance to its principle.
    """
    with create_span("discovery.grammar.seed_from_principles",
                     n_principles=len(principles)) as span:
        atoms: List[Dict[str, Any]] = []
        seeded: set = set()
        for principle in principles:
            for requirement in principle.get("data_requirements", []):
                if not str(requirement).startswith("features."):
                    continue
                token = str(requirement).split(".", 1)[1]
                for feature in match_features(token, available_features):
                    if feature in seeded:
                        continue
                    seeded.add(feature)
                    atoms.append({"feature": feature, "form": "tercile",
                                  "provenance": {"principle_id": principle["id"]}})
        set_attributes(span, n_atoms=len(atoms))
        return atoms


def search_space_size(atoms: List[Dict[str, Any]], depth: int) -> int:
    """Exact candidate count for (atoms, depth) — the pre-registered denominator input."""
    ordered = validate_atoms(atoms)
    if depth < 1 or depth > HARD_DEPTH_CAP:
        raise GrammarError(f"depth {depth} outside [1, {HARD_DEPTH_CAP}]")
    n = len(ordered)
    if depth >= 2:
        m_bool = sum(1 for a in ordered if atom_is_boolean(a))
        n += 2 * (m_bool * (m_bool - 1) // 2)
    return n
