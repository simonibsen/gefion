"""
Data preparation helpers for experiment D3 charts.

Pure transformations from experiment/trial rows into the data shapes the
experiment chart templates expect. Database fetches live in
gefion.charts.queries; renderers in gefion.charts.d3.renderers.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes

# Experiment types whose trials explore a parameter search space and can be
# pivoted into a 2D sensitivity heatmap when exactly two params vary.
_PARAM_SEARCH_TYPES = {"hyperparameter", "strategy_params"}


def charts_for_experiment_type(experiment_type: str) -> List[str]:
    """Return which experiment charts apply to a given experiment type.

    Every type gets the trials scatter; parameter-search types additionally
    get the sensitivity heatmap (rendered only if build_heatmap_data finds
    exactly two varying numeric parameters).
    """
    if experiment_type in _PARAM_SEARCH_TYPES:
        return ["trials", "heatmap"]
    return ["trials"]


def build_heatmap_data(trials: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pivot trials into heatmap cells when exactly two numeric params vary.

    Args:
        trials: Chart-ready trial dicts with "parameters" and "score" keys.

    Returns:
        {"cells": [{"x", "y", "value"}], "x_label": str, "y_label": str},
        or None when the trials cannot be plotted as a 2D heatmap
        (fewer/more than two varying params, non-numeric values, no trials).
    """
    with create_span("charts.experiments.build_heatmap_data", trial_count=len(trials)) as span:
        values_by_param: Dict[str, set] = defaultdict(set)
        for trial in trials:
            for name, value in (trial.get("parameters") or {}).items():
                values_by_param[name].add(value)

        varying = sorted(name for name, values in values_by_param.items() if len(values) > 1)
        if len(varying) != 2:
            set_attributes(span, skipped=True, varying_params=len(varying))
            return None

        x_label, y_label = varying
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for name in varying
            for v in values_by_param[name]
        ):
            set_attributes(span, skipped=True, reason="non_numeric_params")
            return None

        scores_by_cell: Dict[tuple, List[float]] = defaultdict(list)
        for trial in trials:
            params = trial.get("parameters") or {}
            if x_label in params and y_label in params and trial.get("score") is not None:
                scores_by_cell[(params[x_label], params[y_label])].append(float(trial["score"]))

        cells = [
            {"x": x, "y": y, "value": sum(scores) / len(scores)}
            for (x, y), scores in scores_by_cell.items()
        ]
        set_attributes(span, cell_count=len(cells))
        return {"cells": cells, "x_label": x_label, "y_label": y_label}
