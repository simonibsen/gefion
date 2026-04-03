"""Tests for pipeline experiment type.

TDD: Tests written first.
"""
import pytest


class TestPipelineExperiment:
    """Tests for the PipelineExperiment class."""

    def test_class_exists(self):
        """PipelineExperiment should be importable."""
        from gefion.experiments.types.pipeline import PipelineExperiment
        assert PipelineExperiment is not None

    def test_requires_at_least_2_stages(self):
        """Pipeline must have at least 2 stages."""
        from gefion.experiments.types.pipeline import PipelineExperiment

        with pytest.raises(ValueError, match="2 stages"):
            PipelineExperiment(
                name="too-short",
                stages=[{"type": "feature_engineering", "config": {}}],
            )

    def test_instantiation_with_2_stages(self):
        """Should work with 2 or more stages."""
        from gefion.experiments.types.pipeline import PipelineExperiment

        exp = PipelineExperiment(
            name="feature-then-model",
            stages=[
                {"type": "feature_engineering", "config": {"function": "frac_diff"}},
                {"type": "hyperparameter", "config": {"model": "xgboost"}},
            ],
            principle_id="ldp-fractional-diff",
            null_hypothesis="Pipeline does not improve end-to-end performance",
        )
        assert len(exp.stages) == 2
        assert exp.principle_id == "ldp-fractional-diff"

    def test_to_experiment_config(self):
        """Should produce ExperimentConfig with type='pipeline'."""
        from gefion.experiments.types.pipeline import PipelineExperiment
        from gefion.experiments.core import ExperimentConfig

        exp = PipelineExperiment(
            name="full-pipeline",
            stages=[
                {"type": "feature_engineering", "config": {}},
                {"type": "hyperparameter", "config": {}},
                {"type": "strategy_params", "config": {}},
            ],
        )
        config = exp.to_experiment_config()
        assert isinstance(config, ExperimentConfig)
        assert config.experiment_type == "pipeline"
        assert config.extra_config["stage_count"] == 3

    def test_risk_level_is_high(self):
        """Pipeline experiments are high risk (multi-stage changes)."""
        from gefion.experiments.types.pipeline import PipelineExperiment

        exp = PipelineExperiment(
            name="test",
            stages=[{"type": "a", "config": {}}, {"type": "b", "config": {}}],
        )
        assert exp.risk_level == "high"

    def test_3_stage_pipeline(self):
        """Should work with 3 stages (feature → model → strategy)."""
        from gefion.experiments.types.pipeline import PipelineExperiment

        exp = PipelineExperiment(
            name="end-to-end",
            stages=[
                {"type": "feature_engineering", "config": {"fn": "frac_diff", "d": 0.4}},
                {"type": "hyperparameter", "config": {"model": "xgboost", "lr": [0.01, 0.1]}},
                {"type": "strategy_params", "config": {"strategy": "momentum"}},
            ],
        )
        assert len(exp.stages) == 3
