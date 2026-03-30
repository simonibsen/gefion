"""Feature engineering experiment type.

Creates and evaluates new computed features within the experiment sandbox.
Features are tagged as experimental until auto-promoted via statistical gates.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from gefion.experiments.core import ExperimentConfig
from gefion.observability import create_span

logger = logging.getLogger(__name__)


@dataclass
class FeatureEngineeringExperiment:
    """Experiment that creates and evaluates a new computed feature.

    The experiment:
    1. Creates an experimental feature definition
    2. Computes the feature for training data (excluding holdout)
    3. Rebuilds the dataset with the new feature
    4. Retrains the model
    5. Evaluates on holdout
    6. Returns p-value for FDR evaluation
    """
    name: str
    principle_id: str
    null_hypothesis: str
    feature_config: Dict[str, Any]  # {function_name, params}
    source_column: str
    source_table: str = "stock_ohlcv"
    risk_level: str = "medium"
    objective_metric: str = "sharpe_ratio"

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig for storage."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="feature_engineering",
            search_space=self.feature_config.get("params", {}),
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            extra_config={
                "feature_config": self.feature_config,
                "source_column": self.source_column,
                "source_table": self.source_table,
            },
        )
