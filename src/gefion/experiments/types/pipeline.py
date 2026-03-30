"""Pipeline experiment type.

Chains multiple experiment stages (feature → model → strategy) with
dependency tracking and end-to-end holdout evaluation.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gefion.experiments.core import ExperimentConfig
from gefion.observability import create_span

logger = logging.getLogger(__name__)


@dataclass
class PipelineExperiment:
    """Experiment that chains multiple stages evaluated end-to-end.

    Each stage depends on the previous stage's output. The entire pipeline
    is evaluated on the holdout — not per-stage metrics (which could
    compound overfitting).
    """
    name: str
    stages: List[Dict[str, Any]]  # [{type, config}, {type, config}, ...]
    principle_id: Optional[str] = None
    null_hypothesis: Optional[str] = None
    risk_level: str = "high"
    objective_metric: str = "sharpe_ratio"

    def __post_init__(self):
        if not self.stages or len(self.stages) < 2:
            raise ValueError("Pipeline experiments require at least 2 stages")

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="pipeline",
            search_space={"stages": self.stages},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            extra_config={
                "stages": self.stages,
                "stage_count": len(self.stages),
            },
        )
