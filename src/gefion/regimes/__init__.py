"""Regime slicing (spec 005).

A first-class regime abstraction: describe the state of the market/sector/asset
as a named, causal, persistent, time-indexed dimension and evaluate signals and
strategies conditionally against it.

Submodules:
    definitions — RegimeDefinition + RegimeExpression AST (this increment)
    labels      — causal label computation + persistence (US1)
    slicing     — regime-sliced backtest metrics (US2)
    interaction — continuous-interaction test (US5)
    conditional — per-regime holdout p-values + FDR (US3)
"""
from gefion.observability import create_span, set_attributes  # noqa: F401

__all__ = ["create_span", "set_attributes"]
