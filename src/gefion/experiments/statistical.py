"""Statistical methods for experiment evaluation.

Provides Benjamini-Hochberg FDR control and holdout p-value computation.
"""
import logging
import math
from typing import List

from scipy import stats

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


def apply_fdr(p_values: List[float], fdr_rate: float = 0.10) -> List[bool]:
    """Apply Benjamini-Hochberg FDR correction to a list of p-values.

    Returns a boolean mask: True = experiment survives correction (promote),
    False = rejected (do not promote).
    """
    if not p_values:
        return []

    n = len(p_values)

    with create_span("experiments.statistical.apply_fdr",
                      n_experiments=n, fdr_rate=fdr_rate) as span:
        # Sort p-values with their original indices
        indexed = sorted(enumerate(p_values), key=lambda x: x[1])

        # BH procedure: compare p(k) <= (k/n) * fdr_rate
        # Find the largest k where this holds
        max_k = -1
        for rank, (orig_idx, p) in enumerate(indexed, 1):
            threshold = (rank / n) * fdr_rate
            if p <= threshold:
                max_k = rank

        # All experiments with rank <= max_k survive
        mask = [False] * n
        if max_k > 0:
            for rank, (orig_idx, p) in enumerate(indexed, 1):
                if rank <= max_k:
                    mask[orig_idx] = True

        promoted = sum(mask)
        set_attributes(span, promoted=promoted, rejected=n - promoted)

    return mask


def compute_holdout_pvalue(
    baseline_scores: List[float],
    experimental_scores: List[float],
) -> float:
    """Compute p-value comparing experimental vs baseline scores on holdout data.

    Uses a paired t-test (two-sided) when scores are paired (same stocks),
    or an independent t-test otherwise.

    Returns p-value between 0 and 1.
    """
    if not baseline_scores or not experimental_scores:
        raise ValueError("Both baseline_scores and experimental_scores must be non-empty")

    with create_span("experiments.statistical.compute_pvalue",
                      n_baseline=len(baseline_scores),
                      n_experimental=len(experimental_scores)):
        if len(baseline_scores) == len(experimental_scores):
            t_stat, p_value = stats.ttest_rel(experimental_scores, baseline_scores)
        else:
            t_stat, p_value = stats.ttest_ind(experimental_scores, baseline_scores)

        # Identical distributions produce NaN — treat as no difference (p=1.0)
        if math.isnan(p_value):
            return 1.0

        return float(p_value)
