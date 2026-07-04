"""RegimeDefinition and the RegimeExpression AST (spec 005, T008).

A regime is described by a declarative expression tree (AST): leaves are atomic
causal conditions (comparison / reference / gated detector_function), nodes are
boolean operators. The AST is data (JSON), evaluated without code execution
except at a detector_function leaf. See specs/005-regime-slicing/data-model.md.
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Dict, Iterator, Optional

from gefion.observability import create_span, set_attributes

# --- vocabulary -----------------------------------------------------------

SCOPES = ("market", "sector", "industry", "asset")
# Granularity ordering: market (coarsest) .. asset (finest). Higher = finer.
_SCOPE_GRANULARITY = {"market": 0, "sector": 1, "industry": 2, "asset": 3}

BOOLEAN_OPS = ("AND", "OR", "NOT")
COMPARATORS = ("<", "<=", ">", ">=", "==", "in", "quantile")

LEAF_TYPES = ("comparison", "reference", "detector_function")

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RegimeExpressionError(ValueError):
    """Raised when a RegimeExpression AST is structurally or semantically invalid."""


# --- AST helpers ----------------------------------------------------------

def _is_leaf(node: Any) -> bool:
    return isinstance(node, dict) and "leaf" in node


def _is_op(node: Any) -> bool:
    return isinstance(node, dict) and "op" in node


def validate_expression(node: Any) -> None:
    """Validate a RegimeExpression AST node, raising RegimeExpressionError on any problem."""
    with create_span("regimes.definitions.validate_expression"):
        _validate_node(node)


def _validate_node(node: Any) -> None:
    if _is_op(node):
        op = node.get("op")
        if op not in BOOLEAN_OPS:
            raise RegimeExpressionError(f"unknown operator: {op!r}")
        children = node.get("children")
        if not isinstance(children, list) or not children:
            raise RegimeExpressionError(f"operator {op} requires a non-empty children list")
        if op == "NOT" and len(children) != 1:
            raise RegimeExpressionError("NOT requires exactly one child")
        for child in children:
            _validate_node(child)
        return

    if _is_leaf(node):
        _validate_leaf(node)
        return

    raise RegimeExpressionError(f"node is neither an operator nor a leaf: {node!r}")


def _validate_leaf(leaf: Dict[str, Any]) -> None:
    kind = leaf.get("leaf")
    if kind not in LEAF_TYPES:
        raise RegimeExpressionError(f"unknown leaf type: {kind!r}")

    if kind == "comparison":
        feature = leaf.get("feature")
        if not isinstance(feature, str) or not feature.strip():
            raise RegimeExpressionError("comparison leaf requires a non-empty feature ref")
        if leaf.get("cmp") not in COMPARATORS:
            raise RegimeExpressionError(f"unknown comparator: {leaf.get('cmp')!r}")
        _require_scope(leaf.get("scope"))

    elif kind == "reference":
        ref = leaf.get("regime")
        if not isinstance(ref, str) or not ref.strip():
            raise RegimeExpressionError("reference leaf requires a non-empty regime name")

    elif kind == "detector_function":
        if not isinstance(leaf.get("function_id"), int):
            raise RegimeExpressionError("detector_function leaf requires an integer function_id")
        _require_scope(leaf.get("scope"))


def _require_scope(scope: Any) -> None:
    if scope not in SCOPES:
        raise RegimeExpressionError(f"invalid scope: {scope!r} (expected one of {SCOPES})")


def iter_leaves(node: Any) -> Iterator[Dict[str, Any]]:
    """Yield every leaf dict in the AST."""
    if _is_leaf(node):
        yield node
    elif _is_op(node):
        for child in node.get("children", []):
            yield from iter_leaves(child)


def finest_scope(node: Any) -> str:
    """Return the finest (most specific) scope among the AST's leaves (FR-020)."""
    scopes = [leaf["scope"] for leaf in iter_leaves(node) if "scope" in leaf]
    if not scopes:
        raise RegimeExpressionError("expression has no scoped leaves")
    return max(scopes, key=lambda s: _SCOPE_GRANULARITY[s])


def has_detector_leaf(node: Any) -> bool:
    """True if the AST contains a (countability-breaking) detector_function leaf (FR-019a)."""
    return any(leaf.get("leaf") == "detector_function" for leaf in iter_leaves(node))


# --- RegimeDefinition -----------------------------------------------------

@dataclasses.dataclass
class RegimeDefinition:
    """A regime recipe: scope + expression AST + bucketing + persistence + metadata."""

    name: str
    scope: str
    expression: Dict[str, Any]
    bucketing: Dict[str, Any]
    persistence: Optional[Dict[str, Any]] = None
    origin: str = "human"
    descriptive_metadata: Optional[Dict[str, Any]] = None
    status: str = "active"

    def validate(self) -> None:
        """Raise RegimeExpressionError if the definition is invalid."""
        with create_span("regimes.definitions.validate", regime=self.name) as span:
            if not _NAME_RE.match(self.name or ""):
                raise RegimeExpressionError(f"name must be a kebab-case slug: {self.name!r}")
            _require_scope(self.scope)
            validate_expression(self.expression)
            declared = self.scope
            actual = finest_scope(self.expression)
            if declared != actual:
                raise RegimeExpressionError(
                    f"declared scope {declared!r} != finest leaf scope {actual!r} (FR-020)"
                )
            if self.origin not in ("human", "machine"):
                raise RegimeExpressionError(f"invalid origin: {self.origin!r}")
            if self.status not in ("active", "archived"):
                raise RegimeExpressionError(f"invalid status: {self.status!r}")
            set_attributes(span, has_detector_leaf=has_detector_leaf(self.expression))

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "RegimeDefinition":
        data = json.loads(payload)
        return cls(**data)
