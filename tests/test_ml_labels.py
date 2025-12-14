import pytest

from g2.ml.labels import TrendClass, classify_return_5class


@pytest.mark.parametrize(
    "ret,weak,strong,expected",
    [
        (0.0, 0.05, 0.10, TrendClass.NEUTRAL),
        (0.01, 0.05, 0.10, TrendClass.NEUTRAL),
        (-0.01, 0.05, 0.10, TrendClass.NEUTRAL),
        (0.05, 0.05, 0.10, TrendClass.WEAK_UP),
        (-0.05, 0.05, 0.10, TrendClass.WEAK_DOWN),
        (0.099, 0.05, 0.10, TrendClass.WEAK_UP),
        (-0.099, 0.05, 0.10, TrendClass.WEAK_DOWN),
        (0.10, 0.05, 0.10, TrendClass.STRONG_UP),
        (-0.10, 0.05, 0.10, TrendClass.STRONG_DOWN),
        (0.25, 0.05, 0.10, TrendClass.STRONG_UP),
        (-0.25, 0.05, 0.10, TrendClass.STRONG_DOWN),
    ],
)
def test_classify_return_5class(ret, weak, strong, expected):
    assert classify_return_5class(ret, weak_threshold=weak, strong_threshold=strong) == expected


def test_classify_return_rejects_invalid_thresholds():
    with pytest.raises(ValueError, match="weak_threshold must be > 0"):
        classify_return_5class(0.01, weak_threshold=0.0, strong_threshold=0.1)
    with pytest.raises(ValueError, match="strong_threshold must be >= weak_threshold"):
        classify_return_5class(0.01, weak_threshold=0.05, strong_threshold=0.01)

