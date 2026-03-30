"""Tests for ExperimentConfig serialization extensions.

TDD: These tests are written FIRST, before implementation.
Tests cover new optional fields (holdout_config, data_split, principle_id,
null_hypothesis, cv_config, resource_limits) and to_dict/from_dict methods.
"""
import copy
import pytest

from gefion.experiments.core import ExperimentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config() -> ExperimentConfig:
    """Return a minimal ExperimentConfig using only pre-existing fields."""
    return ExperimentConfig(
        name="test_experiment",
        experiment_type="strategy_params",
        search_space={"lookback_days": {"type": "int", "low": 5, "high": 20}},
    )


def _full_config() -> ExperimentConfig:
    """Return an ExperimentConfig populated with all new fields."""
    return ExperimentConfig(
        name="full_experiment",
        experiment_type="strategy_params",
        search_space={"lookback_days": {"type": "int", "low": 5, "high": 20}},
        objective_metric="sharpe_ratio",
        objective_direction="maximize",
        max_trials=100,
        search_method="bayesian",
        symbols=["AAPL", "MSFT"],
        exchange="NYSE",
        start_date="2024-01-01",
        end_date="2024-12-31",
        # New fields
        holdout_config={
            "holdout_weeks": 4,
            "holdout_start_date": "2024-11-01",
            "holdout_end_date": "2024-11-30",
        },
        data_split={
            "train_start": "2024-01-01",
            "train_end": "2024-06-30",
            "validation_start": "2024-07-01",
            "validation_end": "2024-09-30",
        },
        principle_id="mean-reversion-v2",
        null_hypothesis="The strategy produces returns indistinguishable from a random walk",
        cv_config={
            "n_splits": 5,
            "embargo_pct": 0.01,
            "prediction_horizon": 5,
        },
        resource_limits={
            "max_wall_seconds": 3600,
            "max_disk_mb": 500,
            "max_memory_mb": 2048,
        },
    )


# ---------------------------------------------------------------------------
# 1. Backward compatibility -- old configs without new fields still work
# ---------------------------------------------------------------------------

class TestExperimentConfigNewFields:
    """Verify ExperimentConfig accepts the new optional fields."""

    def test_minimal_config_without_new_fields(self):
        """Old-style configs without new fields still instantiate fine."""
        config = _minimal_config()
        assert config.name == "test_experiment"
        # New fields should default to None
        assert config.holdout_config is None
        assert config.data_split is None
        assert config.principle_id is None
        assert config.null_hypothesis is None
        assert config.cv_config is None
        assert config.resource_limits is None

    def test_config_with_all_new_fields(self):
        """Config can be created with every new field populated."""
        config = _full_config()
        assert config.holdout_config["holdout_weeks"] == 4
        assert config.data_split["train_start"] == "2024-01-01"
        assert config.principle_id == "mean-reversion-v2"
        assert "random walk" in config.null_hypothesis
        assert config.cv_config["n_splits"] == 5
        assert config.resource_limits["max_wall_seconds"] == 3600

    def test_config_with_partial_new_fields(self):
        """Config with only some new fields leaves the rest as None."""
        config = ExperimentConfig(
            name="partial",
            experiment_type="strategy_params",
            search_space={"lr": {"type": "float", "low": 0.001, "high": 0.1}},
            principle_id="momentum-v1",
            cv_config={"n_splits": 3, "embargo_pct": 0.02, "prediction_horizon": 10},
        )
        assert config.principle_id == "momentum-v1"
        assert config.cv_config["n_splits"] == 3
        assert config.holdout_config is None
        assert config.data_split is None
        assert config.null_hypothesis is None
        assert config.resource_limits is None


# ---------------------------------------------------------------------------
# 2. to_dict() serializes all fields including new ones
# ---------------------------------------------------------------------------

class TestToDict:
    """Verify to_dict() produces the expected dictionary representation."""

    def test_to_dict_includes_new_fields(self):
        """to_dict() output contains every new field with correct values."""
        config = _full_config()
        d = config.to_dict()

        assert d["holdout_config"] == config.holdout_config
        assert d["data_split"] == config.data_split
        assert d["principle_id"] == "mean-reversion-v2"
        assert d["null_hypothesis"] == config.null_hypothesis
        assert d["cv_config"] == config.cv_config
        assert d["resource_limits"] == config.resource_limits

    def test_to_dict_includes_existing_fields(self):
        """to_dict() still contains the original fields."""
        config = _full_config()
        d = config.to_dict()

        assert d["name"] == "full_experiment"
        assert d["experiment_type"] == "strategy_params"
        assert d["search_space"] == config.search_space
        assert d["objective_metric"] == "sharpe_ratio"
        assert d["max_trials"] == 100
        assert d["symbols"] == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# 3. from_dict() deserializes back to identical ExperimentConfig
# ---------------------------------------------------------------------------

class TestFromDict:
    """Verify from_dict() reconstructs an ExperimentConfig from a dict."""

    def test_from_dict_reconstructs_full_config(self):
        """from_dict(to_dict()) yields an equivalent object."""
        original = _full_config()
        d = original.to_dict()
        restored = ExperimentConfig.from_dict(d)

        assert restored.name == original.name
        assert restored.experiment_type == original.experiment_type
        assert restored.search_space == original.search_space
        assert restored.holdout_config == original.holdout_config
        assert restored.data_split == original.data_split
        assert restored.principle_id == original.principle_id
        assert restored.null_hypothesis == original.null_hypothesis
        assert restored.cv_config == original.cv_config
        assert restored.resource_limits == original.resource_limits

    def test_from_dict_reconstructs_minimal_config(self):
        """from_dict works for configs without any new fields."""
        original = _minimal_config()
        d = original.to_dict()
        restored = ExperimentConfig.from_dict(d)

        assert restored.name == original.name
        assert restored.holdout_config is None
        assert restored.resource_limits is None


# ---------------------------------------------------------------------------
# 4. Round-trip: config -> to_dict -> from_dict -> to_dict produces same dict
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """Verify lossless round-trip serialization."""

    def test_round_trip_full_config(self):
        """Full config survives a to_dict -> from_dict -> to_dict round-trip."""
        config = _full_config()
        first = config.to_dict()
        second = ExperimentConfig.from_dict(first).to_dict()
        assert first == second

    def test_round_trip_minimal_config(self):
        """Minimal config also survives the round-trip."""
        config = _minimal_config()
        first = config.to_dict()
        second = ExperimentConfig.from_dict(first).to_dict()
        assert first == second

    def test_round_trip_preserves_nested_dicts(self):
        """Nested dict values (cv_config, etc.) are not mutated."""
        config = _full_config()
        original_cv = copy.deepcopy(config.cv_config)
        d = config.to_dict()
        restored = ExperimentConfig.from_dict(d)
        assert restored.cv_config == original_cv


# ---------------------------------------------------------------------------
# 5. None optional fields serialize correctly
# ---------------------------------------------------------------------------

class TestNoneSerialization:
    """Verify that None-valued optional fields are handled properly."""

    def test_none_fields_omitted_or_null_in_dict(self):
        """When new fields are None they are either absent or null in the dict."""
        config = _minimal_config()
        d = config.to_dict()

        for key in ("holdout_config", "data_split", "principle_id",
                     "null_hypothesis", "cv_config", "resource_limits"):
            # Accept either omission or explicit None
            assert d.get(key) is None, (
                f"Expected '{key}' to be None or absent, got {d.get(key)!r}"
            )

    def test_from_dict_handles_missing_optional_keys(self):
        """from_dict works when the dict omits optional new-field keys."""
        d = {
            "name": "sparse",
            "experiment_type": "strategy_params",
            "search_space": {"x": {"type": "int", "low": 1, "high": 10}},
        }
        config = ExperimentConfig.from_dict(d)
        assert config.name == "sparse"
        assert config.holdout_config is None
        assert config.cv_config is None
        assert config.resource_limits is None


# ---------------------------------------------------------------------------
# 6. Config reuse with different date parameters
# ---------------------------------------------------------------------------

class TestConfigReuse:
    """Verify a config can be duplicated with different date splits."""

    def test_reuse_with_different_data_split(self):
        """Changing data_split dates while keeping everything else produces a valid config."""
        base = _full_config()
        base_dict = base.to_dict()

        # Create a variant with shifted dates
        variant_dict = copy.deepcopy(base_dict)
        variant_dict["data_split"] = {
            "train_start": "2025-01-01",
            "train_end": "2025-06-30",
            "validation_start": "2025-07-01",
            "validation_end": "2025-09-30",
        }

        variant = ExperimentConfig.from_dict(variant_dict)

        # Core config unchanged
        assert variant.name == base.name
        assert variant.search_space == base.search_space
        assert variant.cv_config == base.cv_config

        # Dates are updated
        assert variant.data_split["train_start"] == "2025-01-01"
        assert variant.data_split["validation_end"] == "2025-09-30"
        assert variant.data_split != base.data_split

    def test_reuse_preserves_resource_limits(self):
        """Resource limits stay intact when only dates change."""
        base = _full_config()
        base_dict = base.to_dict()

        variant_dict = copy.deepcopy(base_dict)
        variant_dict["data_split"]["train_start"] = "2026-01-01"

        variant = ExperimentConfig.from_dict(variant_dict)
        assert variant.resource_limits == base.resource_limits


# ---------------------------------------------------------------------------
# 7. holdout_config serializes dates as strings
# ---------------------------------------------------------------------------

class TestHoldoutDateSerialization:
    """Verify holdout_config dates remain strings after serialization."""

    def test_holdout_dates_are_strings_in_dict(self):
        """holdout_config date values are plain strings in to_dict() output."""
        config = _full_config()
        d = config.to_dict()

        hc = d["holdout_config"]
        assert isinstance(hc["holdout_start_date"], str)
        assert isinstance(hc["holdout_end_date"], str)
        assert hc["holdout_start_date"] == "2024-11-01"
        assert hc["holdout_end_date"] == "2024-11-30"

    def test_holdout_dates_survive_round_trip_as_strings(self):
        """After round-trip, holdout dates are still strings (not datetime)."""
        config = _full_config()
        restored = ExperimentConfig.from_dict(config.to_dict())

        assert isinstance(restored.holdout_config["holdout_start_date"], str)
        assert isinstance(restored.holdout_config["holdout_end_date"], str)

    def test_holdout_weeks_is_int(self):
        """holdout_weeks remains an integer through serialization."""
        config = _full_config()
        d = config.to_dict()
        assert isinstance(d["holdout_config"]["holdout_weeks"], int)

        restored = ExperimentConfig.from_dict(d)
        assert isinstance(restored.holdout_config["holdout_weeks"], int)
