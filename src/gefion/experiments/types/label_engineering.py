"""Label engineering experiment type.

Changes the prediction target (e.g., triple-barrier labeling, meta-labeling).
Evaluated via backtest performance, not model prediction metrics.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from gefion.experiments.core import ExperimentConfig
from gefion.observability import create_span

logger = logging.getLogger(__name__)


@dataclass
class LabelEngineeringExperiment:
    """Experiment that changes the prediction target.

    Unlike feature engineering (which changes inputs), label engineering
    changes what the model predicts. Evaluation must use backtest metrics
    (Sharpe, drawdown) rather than prediction accuracy since the target
    differs from the baseline.
    """
    name: str
    principle_id: str
    null_hypothesis: str
    label_type: str  # triple_barrier, meta_label, regime_adjusted
    label_config: Optional[Dict[str, Any]] = None  # {stop_loss, take_profit, max_holding_period, ...}
    risk_level: str = "high"
    evaluation_metric: str = "sharpe_ratio"  # backtest-based, not prediction accuracy

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="label_engineering",
            search_space=self.label_config or {},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.evaluation_metric,
            extra_config={
                "label_type": self.label_type,
                "label_config": self.label_config or {},
            },
        )
