"""Principles catalog — load, query, and update quantitative finance principles.

Principles are stored as YAML files in data/principles/ split by domain area.
They provide domain knowledge for the AI agent when proposing experiments.
"""
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

VALID_DOMAINS = ["statistical", "ml_finance", "factor", "risk_portfolio", "microstructure"]

REQUIRED_FIELDS = [
    "id", "source", "claim", "mechanism", "experiment_types",
    "testable_prediction", "experiment_design", "data_requirements",
    "empirical_status",
]


def _get_principles_dir() -> Path:
    """Get the principles data directory, respecting GEFION_PRINCIPLES_DIR env var."""
    env_dir = os.environ.get("GEFION_PRINCIPLES_DIR")
    if env_dir:
        return Path(env_dir)
    # Default: data/principles/ relative to repo root
    return Path(__file__).resolve().parent.parent.parent.parent / "data" / "principles"


def load_principles(domain: Optional[str] = None) -> List[Dict]:
    """Load principles from YAML files.

    Args:
        domain: If specified, load only this domain. Must be one of VALID_DOMAINS.
                If None, load all domains.

    Returns:
        List of principle dicts.
    """
    with create_span("experiments.principles.load", domain=domain or "all"):
        principles_dir = _get_principles_dir()

        if domain is not None:
            if domain not in VALID_DOMAINS:
                raise ValueError(
                    f"Invalid domain '{domain}'. Must be one of: {VALID_DOMAINS}"
                )
            domains = [domain]
        else:
            domains = VALID_DOMAINS

        all_principles = []
        for d in domains:
            yaml_path = principles_dir / f"{d}.yaml"
            if yaml_path.exists():
                with open(yaml_path) as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, list):
                        all_principles.extend(data)
            else:
                logger.warning("Principles file not found: %s", yaml_path)

        return all_principles


def query_principles(
    principles: List[Dict],
    experiment_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict]:
    """Filter principles by experiment type and/or empirical status.

    Pure function — does not mutate the input list.
    """
    with create_span("experiments.principles.query",
                      experiment_type=experiment_type or "all",
                      status=status or "all") as span:
        result = principles

        if experiment_type is not None:
            result = [
                p for p in result
                if experiment_type in p.get("experiment_types", [])
            ]

        if status is not None:
            result = [
                p for p in result
                if p.get("empirical_status") == status
            ]

        set_attributes(span, result_count=len(result))
        return result


def validate_principle_schema(principle: Dict) -> List[str]:
    """Validate a principle dict has all required fields.

    Returns list of missing field names (empty = valid).
    """
    missing = []
    for field in REQUIRED_FIELDS:
        if field not in principle:
            missing.append(field)
    return missing


def update_empirical_status(
    principle_id: str,
    experiment_id: int,
    outcome: str,
) -> None:
    """Update a principle's empirical status based on experiment results.

    Args:
        principle_id: The principle's id slug.
        experiment_id: The experiment that produced the result.
        outcome: One of 'confirmed', 'contradicted', 'partially_confirmed'.
    """
    valid_outcomes = {"confirmed", "contradicted", "partially_confirmed"}
    if outcome not in valid_outcomes:
        raise ValueError(f"Invalid outcome '{outcome}'. Must be one of: {valid_outcomes}")

    with create_span("experiments.principles.update_status",
                      principle_id=principle_id, outcome=outcome,
                      experiment_id=experiment_id):
        principles_dir = _get_principles_dir()

        # Search all YAML files for the principle
        for yaml_file in principles_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            if not isinstance(data, list):
                continue

            for principle in data:
                if principle.get("id") == principle_id:
                    principle["empirical_status"] = outcome
                    # Add experiment reference
                    if "experiments" not in principle:
                        principle["experiments"] = []
                    principle["experiments"].append({
                        "experiment_id": experiment_id,
                        "outcome": outcome,
                    })

                    with open(yaml_file, "w") as f:
                        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

                    logger.info(
                        "Updated principle '%s' to status '%s' (experiment %d)",
                        principle_id, outcome, experiment_id,
                    )
                    return

        raise KeyError(f"Principle '{principle_id}' not found in any YAML file")
