"""Tests for RegimeDefinition + RegimeExpression AST (005 T007).

TDD: written FIRST. Pure-Python domain model — no DB required.
Covers AST validation, finest-scope-under-composition (FR-020),
detector-function-leaf detection (FR-019a), causality checks, and
JSON round-trip.
"""
import pytest

from gefion.regimes.definitions import (
    RegimeDefinition,
    RegimeExpressionError,
    validate_expression,
    finest_scope,
    has_detector_leaf,
    SCOPES,
)


# --- fixtures -------------------------------------------------------------

def cmp_leaf(feature="realized_vol_20", cmp=">", value=0, scope="market"):
    return {"leaf": "comparison", "feature": feature, "cmp": cmp, "value": value, "scope": scope}


def composite_market_and_industry():
    return {
        "op": "AND",
        "children": [
            cmp_leaf("vix_slope_20", ">", 0, "market"),
            cmp_leaf("defense_vol_slope_20", ">", 0, "industry"),
        ],
    }


# --- validation -----------------------------------------------------------

def test_valid_comparison_leaf_passes():
    validate_expression(cmp_leaf())  # should not raise


def test_valid_composite_passes():
    validate_expression(composite_market_and_industry())


def test_unknown_operator_rejected():
    with pytest.raises(RegimeExpressionError):
        validate_expression({"op": "XOR", "children": [cmp_leaf()]})


def test_unknown_comparator_rejected():
    with pytest.raises(RegimeExpressionError):
        validate_expression(cmp_leaf(cmp="≈"))


def test_bad_scope_rejected():
    with pytest.raises(RegimeExpressionError):
        validate_expression(cmp_leaf(scope="galaxy"))


def test_not_requires_single_child():
    with pytest.raises(RegimeExpressionError):
        validate_expression({"op": "NOT", "children": [cmp_leaf(), cmp_leaf()]})


def test_empty_feature_ref_rejected():
    with pytest.raises(RegimeExpressionError):
        validate_expression(cmp_leaf(feature=""))


# --- finest scope (FR-020) ------------------------------------------------

def test_finest_scope_of_market_and_industry_is_industry():
    assert finest_scope(composite_market_and_industry()) == "industry"


def test_finest_scope_single_market_leaf():
    assert finest_scope(cmp_leaf(scope="market")) == "market"


def test_finest_scope_prefers_asset():
    expr = {"op": "OR", "children": [cmp_leaf(scope="sector"), cmp_leaf(scope="asset")]}
    assert finest_scope(expr) == "asset"


# --- detector-function leaf (FR-019a) -------------------------------------

def test_detector_leaf_detected():
    expr = {"leaf": "detector_function", "function_id": 7, "scope": "market"}
    assert has_detector_leaf(expr) is True


def test_no_detector_leaf_in_pure_declarative():
    assert has_detector_leaf(composite_market_and_industry()) is False


# --- RegimeDefinition -----------------------------------------------------

def test_definition_scope_must_match_finest_scope():
    # declared scope (market) contradicts finest leaf scope (industry) → invalid
    with pytest.raises(RegimeExpressionError):
        RegimeDefinition(
            name="bad-scope",
            scope="market",
            expression=composite_market_and_industry(),
            bucketing={"labels": ["true", "false"]},
        ).validate()


def test_definition_valid_when_scope_matches():
    d = RegimeDefinition(
        name="vix-and-defense",
        scope="industry",
        expression=composite_market_and_industry(),
        bucketing={"labels": ["true", "false"]},
    )
    d.validate()  # should not raise


def test_definition_json_round_trip():
    d = RegimeDefinition(
        name="vol-regime",
        scope="market",
        expression=cmp_leaf(cmp="quantile", value="tercile"),
        bucketing={"labels": ["calm", "normal", "stressed"], "method": "tercile"},
        persistence={"min_dwell": 3, "mode": "min_dwell"},
        descriptive_metadata={"captures": "market volatility regime"},
    )
    restored = RegimeDefinition.from_json(d.to_json())
    assert restored.name == d.name
    assert restored.scope == d.scope
    assert restored.expression == d.expression
    assert restored.persistence == d.persistence


def test_scopes_constant_is_the_four():
    assert set(SCOPES) == {"market", "sector", "industry", "asset"}
