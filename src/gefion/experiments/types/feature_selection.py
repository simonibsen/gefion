"""Feature selection experiment type.

Evaluates feature subsets to find the optimal set for model performance.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gefion.experiments.core import ExperimentConfig
from gefion.observability import create_span

logger = logging.getLogger(__name__)


@dataclass
class FeatureSelectionExperiment:
    """Experiment that evaluates feature subsets.

    Supports importance-based, forward, and backward selection methods.
    """
    name: str
    principle_id: str
    null_hypothesis: str
    feature_names: List[str]
    selection_method: str = "importance"  # importance, forward, backward
    risk_level: str = "low"
    objective_metric: str = "sharpe_ratio"

    def __post_init__(self):
        if not self.feature_names:
            raise ValueError("feature_names must be a non-empty list")

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="feature_selection",
            search_space={"features": self.feature_names},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            extra_config={
                "selection_method": self.selection_method,
                "feature_names": self.feature_names,
            },
        )
