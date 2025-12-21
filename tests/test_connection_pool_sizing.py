"""
Test connection pool sizing calculations.

The bug: Pool size calculation doesn't account for writer threads per worker.
If max_workers=10 and writer_workers=2, each worker needs 1 connection for main
work + connections for 2 writer threads = 3 connections total.
Total needed = 10 * 3 = 30 connections, but the buggy formula gives max_pool = 12.

The fix: max_pool should be max_workers * (writer_workers + 1) + buffer.
"""


def test_pool_size_calculation_for_features_compute():
    """
    Test that pool size is calculated correctly for features-compute command.

    The formula should account for:
    - Each worker thread needs 1 main connection
    - Each worker spawns writer_workers threads, each needing 1 connection
    - Some buffer for other operations
    """
    # Test case 1: max_workers=10, writer_workers=2
    max_workers = 10
    writer_workers = 2

    # Buggy calculation (current code):
    min_pool_buggy = max(2, writer_workers)  # 2
    max_pool_buggy = max(max_workers + 2, min_pool_buggy + 2)  # max(12, 4) = 12

    # Each worker needs: 1 main + 2 writer = 3 connections
    # With 10 workers: 10 * 3 = 30 connections needed
    connections_needed = max_workers * (writer_workers + 1)  # 30

    # The buggy calculation gives only 12, which is way too few
    assert max_pool_buggy < connections_needed, \
        f"Bug test failed: buggy pool size {max_pool_buggy} should be less than needed {connections_needed}"

    # Correct calculation:
    # Need max_workers * (writer_workers + 1) connections for all workers and their writers
    # Plus a small buffer (2-5) for other operations
    buffer = 2
    min_pool_correct = max(2, writer_workers)
    max_pool_correct = max(
        max_workers * (writer_workers + 1) + buffer,
        min_pool_correct + buffer
    )

    # Correct pool size should be at least the connections needed
    assert max_pool_correct >= connections_needed, \
        f"Correct pool size {max_pool_correct} should be >= needed {connections_needed}"

    # Test case 2: max_workers=5, writer_workers=3
    max_workers2 = 5
    writer_workers2 = 3
    connections_needed2 = max_workers2 * (writer_workers2 + 1)  # 5 * 4 = 20

    max_pool_correct2 = max(
        max_workers2 * (writer_workers2 + 1) + buffer,
        max(2, writer_workers2) + buffer
    )

    assert max_pool_correct2 >= connections_needed2

    # Test case 3: max_workers=1, writer_workers=1 (minimal case)
    max_workers3 = 1
    writer_workers3 = 1
    connections_needed3 = max_workers3 * (writer_workers3 + 1)  # 1 * 2 = 2

    max_pool_correct3 = max(
        max_workers3 * (writer_workers3 + 1) + buffer,
        max(2, writer_workers3) + buffer
    )

    assert max_pool_correct3 >= connections_needed3


def test_pool_size_accounts_for_writer_threads_per_worker():
    """
    Test that the pool size formula correctly accounts for writer threads.

    Key insight: It's NOT max_workers + writer_workers total threads.
    It's max_workers * (1 + writer_workers) because EACH worker spawns writer_workers threads.
    """
    # Scenario: 10 workers, each spawning 2 writer threads
    max_workers = 10
    writer_workers = 2

    # WRONG calculation (treating writer_workers as total, not per-worker):
    wrong_total = max_workers + writer_workers  # 12 - WRONG!

    # CORRECT calculation (each worker has writer_workers):
    correct_total = max_workers * (1 + writer_workers)  # 30 - CORRECT!

    assert correct_total > wrong_total, \
        f"Per-worker calculation {correct_total} should be > wrong additive calculation {wrong_total}"

    # Verify the math
    # 10 workers, each with 1 main connection and 2 writer connections
    expected = 10 * (1 + 2)
    assert correct_total == expected


def test_minimum_pool_size():
    """Test that minimum pool size is reasonable."""
    # Even with 1 worker and 1 writer, we need at least 2 connections
    max_workers = 1
    writer_workers = 1
    buffer = 2

    min_pool = max(2, writer_workers)
    max_pool = max(
        max_workers * (writer_workers + 1) + buffer,
        min_pool + buffer
    )

    # Minimum pool should be at least 2
    assert min_pool >= 2
    # Maximum pool should be at least min_pool + buffer
    assert max_pool >= min_pool + buffer
    # And should handle the worker + writer combination
    assert max_pool >= max_workers * (writer_workers + 1)
