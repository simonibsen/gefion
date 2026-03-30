"""Model comparison experiment type.

Evaluates multiple model types on identical data splits for fair comparison.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gefion.experiments.core import ExperimentConfig
from gefion.observability import create_span

logger = logging.getLogger(__name__)


@dataclass
class ModelComparisonExperiment:
    """Experiment that compares multiple model types on identical splits.

    All models are trained on the same purged CV splits and evaluated
    on the same holdout for direct metric comparability.
    """
    name: str
    model_types: List[str]  # e.g., ["quantile", "xgboost", "lightgbm"]
    principle_id: Optional[str] = None
    null_hypothesis: Optional[str] = None
    risk_level: str = "low"
    objective_metric: str = "sharpe_ratio"
    cv_config: Optional[Dict[str, Any]] = None

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="model_comparison",
            search_space={"model_types": self.model_types},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            cv_config=self.cv_config,
            extra_config={
                "model_types": self.model_types,
            },
        )
