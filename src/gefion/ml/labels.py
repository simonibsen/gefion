from __future__ import annotations

from enum import Enum


class TrendClass(str, Enum):
    STRONG_UP = "strong_up"
    WEAK_UP = "weak_up"
    NEUTRAL = "neutral"
    WEAK_DOWN = "weak_down"
    STRONG_DOWN = "strong_down"


def classify_return_5class(ret: float, *, weak_threshold: float, strong_threshold: float) -> TrendClass:
    """
    Classify a forward return into a 5-class trend label.

    Boundaries:
    - ret >= +strong_threshold  => STRONG_UP
    - ret >= +weak_threshold    => WEAK_UP
    - |ret| < weak_threshold    => NEUTRAL
    - ret <= -strong_threshold  => STRONG_DOWN
    - ret <= -weak_threshold    => WEAK_DOWN
    """
    if weak_threshold <= 0:
        raise ValueError("weak_threshold must be > 0")
    if strong_threshold < weak_threshold:
        raise ValueError("strong_threshold must be >= weak_threshold")

    if ret >= strong_threshold:
        return TrendClass.STRONG_UP
    if ret >= weak_threshold:
        return TrendClass.WEAK_UP
    if ret <= -strong_threshold:
        return TrendClass.STRONG_DOWN
    if ret <= -weak_threshold:
        return TrendClass.WEAK_DOWN
    return TrendClass.NEUTRAL

