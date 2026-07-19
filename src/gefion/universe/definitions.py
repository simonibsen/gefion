"""Universe definitions (spec 015).

A universe is a named, rule-defined subset of the stock population — the
entity-space sibling of a regime definition. Rules are generic
attribute/operator/value predicates over the declared attribute registry;
matching an exclude rule excludes ("any match excludes"). Pins are rare
per-symbol overrides that beat rules. Definitions carry a content
fingerprint so results can record exactly which population they were
measured on.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

from gefion.observability import create_span, set_attributes
from gefion.universe.evaluate import ATTRIBUTES, ops_for_attribute

logger = logging.getLogger(__name__)

RESERVED_NAMES = {"all"}
DEFAULT_UNIVERSE_NAME = "modeling_default"

SEED_RULES = [
    {"name": "no-shell-companies", "attribute": "industry", "op": "eq",
     "value": "SHELL COMPANIES",
     "reason": "Blank-check entities; cash boxes, not operating businesses"},
    {"name": "no-etfs", "attribute": "asset_type", "op": "eq", "value": "ETF",
     "reason": "Funds, not companies; double-counts constituents in "
               "cross-sections"},
]

_PIN_ACTIONS = {"include", "exclude"}

_COLUMNS = ("id", "name", "description", "rules", "pins", "fingerprint",
            "is_default", "enabled", "created_at", "updated_at")


class UniverseValidationError(ValueError):
    """A definition, rule, or pin failed validation (refusal, not silence)."""


def compute_fingerprint(rules: List[Dict], pins: List[Dict]) -> str:
    """Content identity of a definition: sha256 over canonical JSON.

    Stable under dict-key order and rule/pin list order; changes iff the
    rules or pins change (FR-007).
    """
    canon = {
        "rules": sorted(rules, key=lambda r: r.get("name", "")),
        "pins": sorted(pins, key=lambda p: p.get("symbol", "")),
    }
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":"),
                         default=str)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _validate_rule(rule: Dict) -> None:
    name = rule.get("name")
    if not name or not isinstance(name, str):
        raise UniverseValidationError("every rule needs a 'name'")
    if not rule.get("reason"):
        raise UniverseValidationError(
            f"rule '{name}': a human-readable 'reason' is mandatory")
    attribute = rule.get("attribute")
    if attribute not in ATTRIBUTES:
        raise UniverseValidationError(
            f"rule '{name}': unknown attribute '{attribute}'. "
            f"Valid attributes: {', '.join(sorted(ATTRIBUTES))}")
    op = rule.get("op")
    valid_ops = ops_for_attribute(attribute)
    if op not in valid_ops:
        raise UniverseValidationError(
            f"rule '{name}': op '{op}' not valid for attribute "
            f"'{attribute}'. Valid ops: {', '.join(sorted(valid_ops))}")
    value = rule.get("value")
    if op == "is_missing":
        if value is not None:
            raise UniverseValidationError(
                f"rule '{name}': op 'is_missing' takes no value")
    elif op == "in":
        if not isinstance(value, list) or not value:
            raise UniverseValidationError(
                f"rule '{name}': op 'in' needs a non-empty list value")
    elif op == "between":
        if (not isinstance(value, list) or len(value) != 2
                or not all(isinstance(v, (int, float)) for v in value)):
            raise UniverseValidationError(
                f"rule '{name}': op 'between' needs a [low, high] value")
    elif op in ("lt", "lte", "gt", "gte"):
        if not isinstance(value, (int, float)):
            raise UniverseValidationError(
                f"rule '{name}': op '{op}' needs a numeric value")
    else:  # eq / ne
        if value is None or isinstance(value, (list, dict)):
            raise UniverseValidationError(
                f"rule '{name}': op '{op}' needs a scalar value")


def _validate_pin(pin: Dict) -> None:
    if not pin.get("symbol"):
        raise UniverseValidationError("every pin needs a 'symbol'")
    if pin.get("action") not in _PIN_ACTIONS:
        raise UniverseValidationError(
            f"pin '{pin.get('symbol')}': action must be one of "
            f"{sorted(_PIN_ACTIONS)}")
    if not pin.get("reason"):
        raise UniverseValidationError(
            f"pin '{pin['symbol']}': a 'reason' is mandatory")


def validate_definition(name: str, rules: List[Dict],
                        pins: List[Dict]) -> None:
    """Validate a full definition; raises UniverseValidationError."""
    if not name or name.lower() in RESERVED_NAMES:
        raise UniverseValidationError(
            f"'{name}' is a reserved universe name")
    for rule in rules:
        _validate_rule(rule)
    seen = [r["name"] for r in rules]
    if len(seen) != len(set(seen)):
        raise UniverseValidationError("rule names must be unique")
    for pin in pins:
        _validate_pin(pin)


def _row_to_dict(row: tuple) -> Dict[str, Any]:
    return dict(zip(_COLUMNS, row))


def define_universe(conn, name: str, description: Optional[str] = None,
                    rules: Optional[List[Dict]] = None,
                    pins: Optional[List[Dict]] = None,
                    is_default: bool = False) -> Dict[str, Any]:
    """Create or update a universe definition (upsert by name).

    Updating never strips default status; setting is_default with another
    default present is refused. Returns the stored row as a dict.
    """
    rules = rules or []
    pins = pins or []
    validate_definition(name, rules, pins)
    fingerprint = compute_fingerprint(rules, pins)
    with create_span("universe.define", universe=name,
                     rule_count=len(rules)) as span:
        with conn.cursor() as cur:
            if is_default:
                cur.execute(
                    "SELECT name FROM universe_definitions "
                    "WHERE is_default AND name <> %s", (name,))
                other = cur.fetchone()
                if other:
                    raise UniverseValidationError(
                        f"a default universe already exists: {other[0]}")
            cur.execute(
                f"""
                INSERT INTO universe_definitions
                    (name, description, rules, pins, fingerprint, is_default)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    description = COALESCE(EXCLUDED.description,
                                           universe_definitions.description),
                    rules = EXCLUDED.rules,
                    pins = EXCLUDED.pins,
                    fingerprint = EXCLUDED.fingerprint,
                    is_default = universe_definitions.is_default
                                 OR EXCLUDED.is_default,
                    updated_at = NOW()
                RETURNING {', '.join(_COLUMNS)}
                """,
                (name, description, Json(rules), Json(pins), fingerprint,
                 is_default))
            row = _row_to_dict(cur.fetchone())
        conn.commit()
        set_attributes(span, fingerprint=fingerprint)
    return row


def get_universe(conn, name: str) -> Optional[Dict[str, Any]]:
    """Fetch one definition by name; None if absent."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM universe_definitions "
            "WHERE name = %s", (name,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_universes(conn) -> List[Dict[str, Any]]:
    """All definitions, ordered by name."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM universe_definitions "
            "ORDER BY name")
        return [_row_to_dict(r) for r in cur.fetchall()]


def set_enabled(conn, name: str, enabled: bool) -> None:
    """Enable/disable a universe. Disabling the default is refused —
    consumers resolving the default would have nowhere to go."""
    u = get_universe(conn, name)
    if u is None:
        raise UniverseValidationError(f"no universe named '{name}'")
    if not enabled and u["is_default"]:
        raise UniverseValidationError(
            "cannot disable the default universe; set another default first")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE universe_definitions SET enabled = %s, updated_at = NOW() "
            "WHERE name = %s", (enabled, name))
    conn.commit()


def export_universes(conn) -> str:
    """All definitions as YAML (git backup, same idiom as regime export)."""
    import yaml
    payload = [{
        "name": u["name"],
        "description": u["description"],
        "is_default": u["is_default"],
        "enabled": u["enabled"],
        "rules": u["rules"],
        "pins": u["pins"],
    } for u in list_universes(conn)]
    return yaml.safe_dump({"universes": payload}, sort_keys=False)


def import_universes(conn, text: str, dry_run: bool = False) -> Dict[str, Any]:
    """Import definitions from YAML; validates everything BEFORE writing.

    Returns a diff report {created, updated, unchanged}; dry_run reports
    without writing.
    """
    import yaml
    doc = yaml.safe_load(text) or {}
    entries = doc.get("universes") or []
    if not isinstance(entries, list):
        raise UniverseValidationError("expected a top-level 'universes' list")
    for e in entries:
        validate_definition(e.get("name", ""), e.get("rules") or [],
                            e.get("pins") or [])
    created, updated, unchanged = [], [], []
    for e in entries:
        existing = get_universe(conn, e["name"])
        fingerprint = compute_fingerprint(e.get("rules") or [],
                                          e.get("pins") or [])
        if existing is None:
            created.append(e["name"])
        elif existing["fingerprint"] != fingerprint:
            updated.append(e["name"])
        else:
            unchanged.append(e["name"])
        if not dry_run:
            define_universe(conn, e["name"],
                            description=e.get("description"),
                            rules=e.get("rules") or [],
                            pins=e.get("pins") or [],
                            is_default=bool(e.get("is_default")))
            if "enabled" in e and not e["is_default"]:
                set_enabled(conn, e["name"], bool(e["enabled"]))
    return {"created": created, "updated": updated, "unchanged": unchanged,
            "dry_run": dry_run}


def seed_default_universe(conn) -> Dict[str, Any]:
    """Idempotent db-init seed of the default modeling universe (FR-011).

    Creates modeling_default with the two owner-approved rules ONLY if it
    does not exist — re-seeding never clobbers owner edits.
    """
    existing = get_universe(conn, DEFAULT_UNIVERSE_NAME)
    if existing is not None:
        return existing
    logger.info("Seeding default universe '%s'", DEFAULT_UNIVERSE_NAME)
    return define_universe(
        conn, DEFAULT_UNIVERSE_NAME,
        description="Default modeling universe: operating businesses only",
        rules=SEED_RULES, is_default=True)
