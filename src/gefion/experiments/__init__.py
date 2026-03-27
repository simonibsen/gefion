"""
AI Experimentation Framework.

Enables autonomous experimentation with strategy params, features, and models.
Supports hybrid autonomy where AI proposes experiments and users approve.
"""
from .core import ExperimentConfig, ExperimentRunner
from .search import SearchStrategy, GridSearch, RandomSearch, BayesianSearch
from .types import StrategyParamExperiment

__all__ = [
    "ExperimentConfig",
    "ExperimentRunner",
    "SearchStrategy",
    "GridSearch",
    "RandomSearch",
    "BayesianSearch",
    "StrategyParamExperiment",
]
