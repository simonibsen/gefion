"""Shared holdout-evaluation helpers for experiment types (FR-017/019).

Every experiment type that earns a holdout p-value follows the same shape:
train on pre-holdout rows only, score experimental vs baseline per symbol
on the holdout window exactly once, and hand the paired scores to
compute_holdout_pvalue. These helpers keep that logic in one place.
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from gefion.observability import create_span, set_attributes


def load_all_cached(exp) -> tuple:
    """Load and cache (X, y, meta) on an experiment instance.

    Works for any evaluator dataclass with dataset_uri, horizon_days and a
    _cached_data field (set via object.__setattr__ for frozen-safety).
    """
    from gefion.ml.models import load_dataset

    if exp._cached_data is None:
        X, y, meta = load_dataset(exp.dataset_uri, exp.horizon_days, with_meta=True)
        object.__setattr__(exp, "_cached_data", (X, y, meta))
    return exp._cached_data


def training_data(exp) -> tuple:
    """(X, y, meta) restricted to pre-holdout rows (FR-017)."""
    X, y, meta = load_all_cached(exp)
    train, _ = holdout_masks(meta, exp.holdout_start, exp.holdout_end)
    return (X[train].reset_index(drop=True),
            y[train].reset_index(drop=True),
            meta[train].reset_index(drop=True))


def holdout_masks(meta: pd.DataFrame, holdout_start, holdout_end) -> tuple:
    """(train_mask, holdout_mask) boolean arrays over dataset rows.

    Without a holdout window every row is trainable and nothing is held out.
    """
    dates = pd.to_datetime(meta["date"]).dt.date
    if holdout_start is None:
        return np.ones(len(meta), dtype=bool), np.zeros(len(meta), dtype=bool)
    end = holdout_end or dates.max()
    train = (dates < holdout_start).values
    hold = ((dates >= holdout_start) & (dates <= end)).values
    return train, hold


def per_symbol_pinball(preds: pd.DataFrame, y: pd.Series, symbols: pd.Series,
                       quantiles: List[float]) -> Dict[str, float]:
    """Mean pinball loss per symbol over the given quantile predictions."""
    losses = []
    for q in quantiles:
        col = f"q{int(q * 100)}"
        err = y.values - preds[col].values
        losses.append(np.where(err >= 0, q * err, (q - 1) * err))
    row_loss = np.mean(losses, axis=0)
    frame = pd.DataFrame({"symbol": symbols.values, "loss": row_loss})
    return frame.groupby("symbol")["loss"].mean().to_dict()


def paired_result(base_scores: Dict[str, float], exp_scores: Dict[str, float],
                  train_rows: int, holdout_rows: int) -> Dict:
    """Paired per-symbol score lists in the compute_holdout_pvalue contract."""
    symbols = sorted(set(base_scores) & set(exp_scores))
    with create_span("experiments.holdout_eval.paired_result") as span:
        set_attributes(span, n_symbols=len(symbols), holdout_rows=holdout_rows)
        return {
            "baseline_scores": [float(base_scores[s]) for s in symbols],
            "experimental_scores": [float(exp_scores[s]) for s in symbols],
            "symbols": symbols,
            "n_symbols": len(symbols),
            "holdout_rows": holdout_rows,
            "train_rows": train_rows,
        }


def per_row_pinball(preds: pd.DataFrame, y: pd.Series, quantiles: List[float]) -> np.ndarray:
    """Row-level mean pinball loss (aligned to preds/y rows; not grouped by symbol)."""
    losses = []
    for q in quantiles:
        col = f"q{int(q * 100)}"
        err = y.values - preds[col].values
        losses.append(np.where(err >= 0, q * err, (q - 1) * err))
    return np.mean(losses, axis=0)


def paired_result_by_date(base_row_loss, exp_row_loss, dates, holdout_rows: int) -> Dict:
    """Per-observation paired scores with dates — the input to regime-conditional
    evaluation (spec 005). base_row_loss/exp_row_loss are aligned row-level losses for
    the two arms over the same holdout rows."""
    with create_span("experiments.holdout_eval.paired_result_by_date") as span:
        observations = [
            {"date": d, "baseline_score": float(b), "experimental_score": float(e)}
            for d, b, e in zip(dates, base_row_loss, exp_row_loss)
        ]
        set_attributes(span, n_observations=len(observations), holdout_rows=holdout_rows)
        return {"observations": observations, "holdout_rows": holdout_rows}


def require_holdout_window(holdout_start, holdout_end) -> None:
    if holdout_start is None or holdout_end is None:
        raise ValueError(
            "evaluate_holdout requires a holdout window (holdout_start/holdout_end)"
        )
