"""Tests for the principles catalog module.

TDD: These tests are written FIRST, before implementation.
Tests load_principles, query_principles, update_empirical_status,
and validate_principle_schema from gefion.experiments.principles.
"""

import copy
import os
import pytest
import yaml
from pathlib import Path

from gefion.experiments.principles import (
    load_principles,
    query_principles,
    update_empirical_status,
    validate_principle_schema,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "principles"

VALID_DOMAINS = ["statistical", "ml_finance", "factor", "risk_portfolio", "microstructure"]

REQUIRED_FIELDS = [
    "id",
    "source",
    "claim",
    "mechanism",
    "experiment_types",
    "testable_prediction",
    "experiment_design",
    "data_requirements",
    "empirical_status",
]


# ---------------------------------------------------------------------------
# load_principles tests
# ---------------------------------------------------------------------------


class TestLoadPrinciples:
    """Tests for load_principles()."""

    def test_load_all_principles_returns_62(self):
        """Loading with no domain returns all 62 principles across 5 files."""
        principles = load_principles()
        assert len(principles) == 62

    def test_load_all_principles_returns_list_of_dicts(self):
        """Each element should be a dict with at least an 'id' key."""
        principles = load_principles()
        assert isinstance(principles, list)
        for p in principles:
            assert isinstance(p, dict)
            assert "id" in p

    def test_load_statistical_domain_returns_11(self):
        """Loading domain='statistical' returns exactly 11 principles."""
        principles = load_principles(domain="statistical")
        assert len(principles) == 11

    def test_load_ml_finance_domain_returns_17(self):
        """Loading domain='ml_finance' returns exactly 17 principles."""
        principles = load_principles(domain="ml_finance")
        assert len(principles) == 17

    def test_load_factor_domain(self):
        """Loading domain='factor' returns 12 principles."""
        principles = load_principles(domain="factor")
        assert len(principles) == 12

    def test_load_risk_portfolio_domain(self):
        """Loading domain='risk_portfolio' returns 11 principles."""
        principles = load_principles(domain="risk_portfolio")
        assert len(principles) == 11

    def test_load_microstructure_domain(self):
        """Loading domain='microstructure' returns 11 principles."""
        principles = load_principles(domain="microstructure")
        assert len(principles) == 11

    def test_load_invalid_domain_raises_value_error(self):
        """Passing an unknown domain should raise ValueError."""
        with pytest.raises(ValueError):
            load_principles(domain="nonexistent_domain")

    def test_load_single_domain_subset_of_all(self):
        """Principles from one domain should be a subset of the full catalog."""
        all_principles = load_principles()
        all_ids = {p["id"] for p in all_principles}

        stat_principles = load_principles(domain="statistical")
        stat_ids = {p["id"] for p in stat_principles}

        assert stat_ids.issubset(all_ids)

    def test_load_all_domains_cover_full_set(self):
        """Loading each domain individually and combining should equal load_all."""
        all_principles = load_principles()
        all_ids = {p["id"] for p in all_principles}

        combined_ids = set()
        for domain in VALID_DOMAINS:
            for p in load_principles(domain=domain):
                combined_ids.add(p["id"])

        assert combined_ids == all_ids


# ---------------------------------------------------------------------------
# query_principles tests
# ---------------------------------------------------------------------------


class TestQueryPrinciples:
    """Tests for query_principles()."""

    @pytest.fixture()
    def all_principles(self) -> list[dict]:
        """Load all principles once for use in query tests."""
        return load_principles()

    def test_no_filters_returns_all(self, all_principles: list[dict]):
        """With no filters, query_principles returns all principles unchanged."""
        result = query_principles(all_principles)
        assert len(result) == len(all_principles)

    def test_filter_by_experiment_type_feature_engineering(self, all_principles: list[dict]):
        """Filtering by experiment_type='feature_engineering' returns a non-empty subset."""
        result = query_principles(all_principles, experiment_type="feature_engineering")
        assert len(result) > 0
        assert len(result) < len(all_principles)
        for p in result:
            assert "feature_engineering" in p["experiment_types"]

    def test_filter_by_status_untested_returns_all(self, all_principles: list[dict]):
        """All principles currently have status='untested', so filtering returns all."""
        result = query_principles(all_principles, status="untested")
        assert len(result) == len(all_principles)

    def test_filter_by_nonexistent_experiment_type_returns_empty(self, all_principles: list[dict]):
        """An experiment_type that no principle has should return empty list."""
        result = query_principles(all_principles, experiment_type="quantum_teleportation")
        assert result == []

    def test_filter_by_status_confirmed_returns_empty(self, all_principles: list[dict]):
        """No principles are confirmed yet, so filtering returns empty."""
        result = query_principles(all_principles, status="confirmed")
        assert result == []

    def test_combined_filters(self, all_principles: list[dict]):
        """Combining experiment_type and status filters both conditions."""
        result = query_principles(
            all_principles, experiment_type="feature_engineering", status="untested"
        )
        assert len(result) > 0
        for p in result:
            assert "feature_engineering" in p["experiment_types"]
            assert p["empirical_status"] == "untested"

    def test_query_is_pure_function(self, all_principles: list[dict]):
        """query_principles should not mutate the input list."""
        original = copy.deepcopy(all_principles)
        query_principles(all_principles, experiment_type="feature_engineering")
        assert all_principles == original

    def test_query_returns_list(self, all_principles: list[dict]):
        """Return type should always be a list."""
        result = query_principles(all_principles, experiment_type="model_comparison")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# validate_principle_schema tests
# ---------------------------------------------------------------------------


class TestValidatePrincipleSchema:
    """Tests for validate_principle_schema()."""

    def test_valid_principle_returns_empty_list(self):
        """A principle with all required fields returns no errors."""
        valid = {
            "id": "test-principle",
            "source": {"author": "Test", "title": "Test", "year": 2024, "chapter": "Ch 1"},
            "claim": "Some claim.",
            "mechanism": "Some mechanism.",
            "experiment_types": ["feature_engineering"],
            "testable_prediction": "Some prediction.",
            "experiment_design": "Some design.",
            "data_requirements": ["ohlcv.close"],
            "empirical_status": "untested",
        }
        errors = validate_principle_schema(valid)
        assert errors == []

    def test_missing_single_field_returns_it(self):
        """A principle missing 'id' should return ['id'] (or similar)."""
        incomplete = {
            "source": {"author": "Test", "title": "Test", "year": 2024, "chapter": "Ch 1"},
            "claim": "Some claim.",
            "mechanism": "Some mechanism.",
            "experiment_types": ["feature_engineering"],
            "testable_prediction": "Some prediction.",
            "experiment_design": "Some design.",
            "data_requirements": ["ohlcv.close"],
            "empirical_status": "untested",
        }
        errors = validate_principle_schema(incomplete)
        assert "id" in errors

    def test_missing_multiple_fields(self):
        """A principle missing several required fields lists them all."""
        incomplete = {
            "id": "test-principle",
            "claim": "Some claim.",
        }
        errors = validate_principle_schema(incomplete)
        missing_expected = {"source", "mechanism", "experiment_types",
                            "testable_prediction", "experiment_design",
                            "data_requirements", "empirical_status"}
        assert missing_expected.issubset(set(errors))

    def test_empty_dict_returns_all_required_fields(self):
        """An empty dict should report every required field as missing."""
        errors = validate_principle_schema({})
        for field in REQUIRED_FIELDS:
            assert field in errors

    def test_returns_list_type(self):
        """Return value is always a list."""
        result = validate_principle_schema({})
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Bulk validation: every real principle passes schema
# ---------------------------------------------------------------------------


class TestBulkPrincipleValidation:
    """Validate every principle in every YAML file against the schema."""

    def test_all_principles_pass_schema_validation(self):
        """Every principle across all 5 domains must pass validate_principle_schema."""
        all_principles = load_principles()
        for p in all_principles:
            errors = validate_principle_schema(p)
            assert errors == [], (
                f"Principle '{p.get('id', 'UNKNOWN')}' has schema errors: {errors}"
            )

    def test_every_principle_has_nonempty_experiment_design(self):
        """Every principle must have a non-empty experiment_design for actionability."""
        all_principles = load_principles()
        for p in all_principles:
            design = p.get("experiment_design", "")
            assert design and isinstance(design, str) and len(design.strip()) > 0, (
                f"Principle '{p.get('id', 'UNKNOWN')}' has empty experiment_design"
            )

    def test_all_principle_ids_are_unique(self):
        """No two principles should share the same id across all domains."""
        all_principles = load_principles()
        ids = [p["id"] for p in all_principles]
        assert len(ids) == len(set(ids)), "Duplicate principle IDs found"

    def test_all_experiment_types_are_known(self):
        """Every experiment_type value should be one of the known types."""
        known_types = {
            "feature_engineering",
            "feature_selection",
            "hyperparameter",
            "label_engineering",
            "model_comparison",
            "pipeline",
            "strategy_optimization",
        }
        all_principles = load_principles()
        for p in all_principles:
            for et in p.get("experiment_types", []):
                assert et in known_types, (
                    f"Principle '{p['id']}' has unknown experiment_type: {et}"
                )


# ---------------------------------------------------------------------------
# update_empirical_status tests
# ---------------------------------------------------------------------------


class TestUpdateEmpiricalStatus:
    """Tests for update_empirical_status().

    These tests use a tmp_path fixture to avoid mutating real YAML files.
    They verify the function's interface and behavior.
    """

    @pytest.fixture()
    def principle_yaml(self, tmp_path: Path) -> Path:
        """Create a temporary YAML file with one principle for update tests."""
        principle = [
            {
                "id": "test-update-principle",
                "source": {"author": "Test", "title": "Test", "year": 2024, "chapter": "Ch 1"},
                "claim": "A test claim.",
                "mechanism": "A test mechanism.",
                "experiment_types": ["feature_engineering"],
                "testable_prediction": "A prediction.",
                "experiment_design": "A design.",
                "data_requirements": ["ohlcv.close"],
                "empirical_status": "untested",
                "experiments": [],
            }
        ]
        yaml_file = tmp_path / "test_domain.yaml"
        yaml_file.write_text(yaml.dump(principle, default_flow_style=False))
        return yaml_file

    def test_update_changes_status_to_confirmed(self, principle_yaml: Path, monkeypatch):
        """Updating with outcome='confirmed' sets empirical_status accordingly."""
        # Monkeypatch the data directory so update_empirical_status finds our temp file
        monkeypatch.setenv("GEFION_PRINCIPLES_DIR", str(principle_yaml.parent))

        update_empirical_status("test-update-principle", experiment_id=42, outcome="confirmed")

        data = yaml.safe_load(principle_yaml.read_text())
        target = [p for p in data if p["id"] == "test-update-principle"][0]
        assert target["empirical_status"] == "confirmed"

    def test_update_changes_status_to_contradicted(self, principle_yaml: Path, monkeypatch):
        """Updating with outcome='contradicted' sets empirical_status accordingly."""
        monkeypatch.setenv("GEFION_PRINCIPLES_DIR", str(principle_yaml.parent))

        update_empirical_status("test-update-principle", experiment_id=7, outcome="contradicted")

        data = yaml.safe_load(principle_yaml.read_text())
        target = [p for p in data if p["id"] == "test-update-principle"][0]
        assert target["empirical_status"] == "contradicted"

    def test_update_changes_status_to_partially_confirmed(self, principle_yaml: Path, monkeypatch):
        """Updating with outcome='partially_confirmed' sets empirical_status."""
        monkeypatch.setenv("GEFION_PRINCIPLES_DIR", str(principle_yaml.parent))

        update_empirical_status(
            "test-update-principle", experiment_id=99, outcome="partially_confirmed"
        )

        data = yaml.safe_load(principle_yaml.read_text())
        target = [p for p in data if p["id"] == "test-update-principle"][0]
        assert target["empirical_status"] == "partially_confirmed"

    def test_update_invalid_outcome_raises(self, principle_yaml: Path, monkeypatch):
        """An invalid outcome value should raise ValueError."""
        monkeypatch.setenv("GEFION_PRINCIPLES_DIR", str(principle_yaml.parent))

        with pytest.raises(ValueError):
            update_empirical_status("test-update-principle", experiment_id=1, outcome="maybe")

    def test_update_unknown_principle_id_raises(self, principle_yaml: Path, monkeypatch):
        """Updating a principle ID that doesn't exist should raise an error."""
        monkeypatch.setenv("GEFION_PRINCIPLES_DIR", str(principle_yaml.parent))

        with pytest.raises((ValueError, KeyError)):
            update_empirical_status("nonexistent-id", experiment_id=1, outcome="confirmed")
