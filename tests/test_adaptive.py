from gefion.utils.adaptive import AdaptiveLimiter, chunked


def test_chunked():
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunked([], 3) == []
    assert chunked([1], 10) == [[1]]


def test_adaptive_limiter():
    lim = AdaptiveLimiter(start_workers=1, max_workers=3)
    assert lim.value() == 1
    # success ramps up
    lim.record_batch(errors=0)
    assert lim.value() == 2
    lim.record_batch(errors=0)
    assert lim.value() == 3
    # stays at max
    lim.record_batch(errors=0)
    assert lim.value() == 3
    # back off on error
    lim.record_batch(errors=1)
    assert lim.value() == 1


def test_adaptive_limiter_ceiling_only():
    lim = AdaptiveLimiter(start_workers=1, max_workers=8)
    assert lim.value() == 1  # even with high ceiling, start low
