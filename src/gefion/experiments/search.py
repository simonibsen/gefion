"""
Search algorithms for parameter optimization.

Provides pluggable search strategies:
- GridSearch: Exhaustive search over all combinations
- RandomSearch: Random sampling from parameter space
- BayesianSearch: Adaptive optimization using Optuna (Phase 3)
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import itertools
import random

logger = logging.getLogger(__name__)


class SearchStrategy(ABC):
    """Base class for search strategies."""

    @abstractmethod
    def suggest(self) -> Optional[Dict[str, Any]]:
        """
        Suggest next parameters to try.

        Returns:
            Dict of parameter values, or None if search is exhausted.
        """
        pass

    @abstractmethod
    def report(self, params: Dict[str, Any], score: float) -> None:
        """
        Report result of a trial.

        Args:
            params: The parameters that were tested
            score: The resulting score (higher is better for maximize)
        """
        pass


class GridSearch(SearchStrategy):
    """
    Exhaustive grid search over parameter space.

    Tries all combinations of parameter values.

    Example search_space:
        {
            "lookback_days": {"type": "int", "low": 5, "high": 15, "step": 5},
            "threshold": {"type": "float", "low": 0.0, "high": 1.0, "steps": 3},
            "method": {"type": "categorical", "choices": ["a", "b"]}
        }
    """

    def __init__(self, search_space: Dict[str, Any]):
        self.search_space = search_space
        self.grid = self._build_grid()
        self.index = 0

    def _build_grid(self) -> List[Dict[str, Any]]:
        """Build all parameter combinations."""
        if not self.search_space:
            return [{}]

        keys = list(self.search_space.keys())
        values = []

        for key in keys:
            spec = self.search_space[key]
            param_type = spec.get("type", "categorical")

            if param_type == "categorical":
                values.append(spec["choices"])
            elif param_type == "int":
                step = spec.get("step", 1)
                values.append(list(range(spec["low"], spec["high"] + 1, step)))
            elif param_type == "float":
                steps = spec.get("steps", 5)
                if steps <= 1:
                    values.append([spec["low"]])
                else:
                    values.append([
                        spec["low"] + i * (spec["high"] - spec["low"]) / (steps - 1)
                        for i in range(steps)
                    ])

        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    def suggest(self) -> Optional[Dict[str, Any]]:
        """Suggest next parameters from grid."""
        if self.index >= len(self.grid):
            return None
        params = self.grid[self.index]
        self.index += 1
        return params

    def report(self, params: Dict[str, Any], score: float) -> None:
        """Grid search doesn't adapt based on results."""
        pass  # No-op for grid search

    def total_combinations(self) -> int:
        """Return total number of parameter combinations."""
        return len(self.grid)


class RandomSearch(SearchStrategy):
    """
    Random sampling from parameter space.

    Samples parameters randomly up to max_trials.
    Often finds good solutions faster than grid search for high-dimensional spaces.

    Example search_space:
        {
            "lookback_days": {"type": "int", "low": 5, "high": 30},
            "threshold": {"type": "float", "low": 0.0, "high": 1.0},
            "method": {"type": "categorical", "choices": ["a", "b", "c"]}
        }
    """

    def __init__(self, search_space: Dict[str, Any], max_trials: int = 50):
        self.search_space = search_space
        self.max_trials = max_trials
        self.trials = 0

    def suggest(self) -> Optional[Dict[str, Any]]:
        """Suggest random parameters from search space."""
        if self.trials >= self.max_trials:
            return None

        params = {}
        for key, spec in self.search_space.items():
            param_type = spec.get("type", "categorical")

            if param_type == "categorical":
                params[key] = random.choice(spec["choices"])
            elif param_type == "int":
                params[key] = random.randint(spec["low"], spec["high"])
            elif param_type == "float":
                params[key] = random.uniform(spec["low"], spec["high"])

        self.trials += 1
        return params

    def report(self, params: Dict[str, Any], score: float) -> None:
        """Random search doesn't adapt based on results."""
        pass  # No-op for random search

    def remaining_trials(self) -> int:
        """Return number of trials remaining."""
        return max(0, self.max_trials - self.trials)


class BayesianSearch(SearchStrategy):
    """
    Bayesian optimization using Optuna.

    Adapts sampling based on previous results to find optimal parameters
    more efficiently than random or grid search.

    Key features:
    - Uses Tree-structured Parzen Estimator (TPE) sampler by default
    - Learns from previous trials to focus on promising regions
    - Supports log-scale sampling for parameters spanning multiple orders of magnitude

    Example search_space:
        {
            "learning_rate": {"type": "float", "low": 0.0001, "high": 0.1, "log": True},
            "n_layers": {"type": "int", "low": 1, "high": 10},
            "dropout": {"type": "float", "low": 0.0, "high": 0.5},
            "activation": {"type": "categorical", "choices": ["relu", "tanh", "sigmoid"]}
        }
    """

    def __init__(
        self,
        search_space: Dict[str, Any],
        direction: str = "maximize",
        max_trials: int = 50,
    ):
        """
        Initialize Bayesian search.

        Args:
            search_space: Parameter search space definition
            direction: "maximize" or "minimize"
            max_trials: Maximum number of trials to run
        """
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        self.search_space = search_space
        self.direction = direction
        self.max_trials = max_trials

        # Create Optuna study
        self.study = optuna.create_study(direction=direction)
        self._current_trial = None
        self._trials_completed = 0

    def suggest(self) -> Optional[Dict[str, Any]]:
        """
        Suggest next parameters using Bayesian optimization.

        Returns:
            Dict of parameter values, or None if max_trials reached.
        """
        import optuna

        if self._trials_completed >= self.max_trials:
            return None

        # Create a trial and sample parameters
        self._current_trial = self.study.ask()

        params = {}
        for key, spec in self.search_space.items():
            param_type = spec.get("type", "categorical")

            if param_type == "categorical":
                params[key] = self._current_trial.suggest_categorical(
                    key, spec["choices"]
                )
            elif param_type == "int":
                params[key] = self._current_trial.suggest_int(
                    key, spec["low"], spec["high"]
                )
            elif param_type == "float":
                log = spec.get("log", False)
                params[key] = self._current_trial.suggest_float(
                    key, spec["low"], spec["high"], log=log
                )

        return params

    def report(self, params: Dict[str, Any], score: float) -> None:
        """
        Report result of a trial to update the optimization model.

        Args:
            params: The parameters that were tested
            score: The resulting score
        """
        if self._current_trial is not None:
            self.study.tell(self._current_trial, score)
            self._current_trial = None
            self._trials_completed += 1

    def get_best_params(self) -> Optional[Dict[str, Any]]:
        """Get the best parameters found so far."""
        if len(self.study.trials) == 0:
            return None
        return self.study.best_params

    def get_best_score(self) -> Optional[float]:
        """Get the best score found so far."""
        if len(self.study.trials) == 0:
            return None
        return self.study.best_value

    def remaining_trials(self) -> int:
        """Return number of trials remaining."""
        return max(0, self.max_trials - self._trials_completed)
