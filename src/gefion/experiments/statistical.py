"""Statistical methods for experiment evaluation.

Provides Benjamini-Hochberg FDR control and holdout p-value computation.
"""
import logging
from typing import List

from scipy import stats

from gefion.observability import create_span

logger = logging.getLogger(__name__)


def apply_fdr(p_values: List[float], fdr_rate: float = 0.10) -> List[bool]:
    """Apply Benjamini-Hochberg FDR correction to a list of p-values.

    Returns a boolean mask: True = experiment survives correction (promote),
    False = rejected (do not promote).
    """
    if not p_values:
        return []

    n = len(p_values)

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

    import math

    if len(baseline_scores) == len(experimental_scores):
        # Paired t-test (same stocks, different models)
        t_stat, p_value = stats.ttest_rel(experimental_scores, baseline_scores)
    else:
        # Independent t-test
        t_stat, p_value = stats.ttest_ind(experimental_scores, baseline_scores)

    # Identical distributions produce NaN — treat as no difference (p=1.0)
    if math.isnan(p_value):
        return 1.0

    return float(p_value)
