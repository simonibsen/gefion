"""Agentic regime discovery (spec 006).

The autonomous agent proposes and evaluates candidate regimes under structural
guardrails: nested segregation (discovery never sees the outer holdout),
pre-registered bounded search spaces, one flat FDR family that counts every
test — including the losers — and auditable candidate/diagnostics ledgers.
Composes the spec-005 primitives (RegimeDefinition AST, causal labels,
conditional p-values, HAC interaction) rather than inventing new statistics.

Modules:
    grammar      — atom library validation, deterministic enumeration, hashing
    universe     — pluggable symbol-universe filter chain
    segregation  — DiscoveryDataContext: inner-window-only data access
    signals      — pluggable signal_source (v1: features)
    edges        — per-candidate conditional edge tests
    ledger       — runs, candidate ledger, diagnostics persistence
    detectors    — expressive tier: sandboxed detector candidates
    freshhold    — fresh-holdout reserve declaration/consumption
    grading      — pluggable, forward-only trust grading
    runner       — orchestration: pre-register → enumerate → freeze → test → FDR
"""
from gefion.observability import create_span, set_attributes  # noqa: F401
