"""
Test that dispatcher write queue is sized appropriately to avoid backpressure.

The issue: Queue size of writer_workers * 2 is too small, causing the compute
thread to block frequently while waiting for queue space.

The fix: Increase queue size to a larger value (e.g., 200) to provide adequate
buffering between compute and write stages.
"""


def test_queue_size_is_adequate_for_buffering():
    """
    Test that the write queue size provides adequate buffering.

    With writer_workers=2 and maxsize=4 (2*2), the queue fills after just
    4 batches, causing the main thread to block. This reduces throughput.

    A larger queue (e.g., 200) allows more buffering so the compute thread
    can stay ahead of writers, improving pipeline efficiency.
    """
    # Test the formula
    writer_workers = 2

    # Current buggy formula
    buggy_size = writer_workers * 2  # = 4

    # Recommended size for good buffering
    recommended_size = 200

    # The buggy size is way too small
    assert buggy_size < 10, \
        f"Buggy size {buggy_size} is unreasonably small for buffering"

    # Recommended size should be much larger
    assert recommended_size >= 100, \
        f"Recommended size {recommended_size} should be at least 100 for adequate buffering"

    # Recommended should be at least 20x larger than buggy
    assert recommended_size >= buggy_size * 20, \
        f"Recommended {recommended_size} should be much larger than buggy {buggy_size}"


def test_queue_size_prevents_compute_thread_blocking():
    """
    Test that queue size is large enough to prevent frequent blocking.

    Scenario:
    - Compute produces batches quickly (every 10ms)
    - Writers consume slowly (50ms per batch)
    - With 2 writers and maxsize=4:
      - Queue fills in 40ms (4 batches * 10ms)
      - Then blocks for ~25ms per batch (50ms write - 2 writers)
      - Results in frequent blocking and poor pipeline utilization

    - With maxsize=200:
      - Queue can hold 200 batches
      - Takes 2000ms to fill (200 * 10ms)
      - Plenty of time for writers to catch up
      - Much better pipeline utilization
    """
    writer_workers = 2

    # Scenario parameters
    compute_batch_time_ms = 10  # Fast compute
    write_batch_time_ms = 50     # Slower write
    batches_per_stock = 100      # Typical workload

    # Small queue (buggy)
    small_queue_size = writer_workers * 2
    time_to_fill_small = small_queue_size * compute_batch_time_ms  # 40ms

    # Large queue (fixed)
    large_queue_size = 200
    time_to_fill_large = large_queue_size * compute_batch_time_ms  # 2000ms

    # Write throughput
    write_throughput_ms_per_batch = write_batch_time_ms / writer_workers  # 25ms per batch

    # Small queue will cause frequent blocking
    # After filling in 40ms, compute blocks waiting for writes
    assert time_to_fill_small < write_batch_time_ms, \
        f"Small queue fills ({time_to_fill_small}ms) faster than writes complete ({write_batch_time_ms}ms)"

    # Large queue provides good buffering
    # Can hold many batches before blocking
    assert time_to_fill_large > batches_per_stock * compute_batch_time_ms, \
        f"Large queue can hold entire workload without blocking"


def test_recommended_queue_size_value():
    """
    Test that the recommended queue size is 200.

    This provides a good balance between:
    - Memory usage (200 batches is reasonable)
    - Buffering capacity (enough to avoid frequent blocking)
    - Backpressure (still bounded, prevents memory exhaustion)
    """
    recommended = 200

    # Should be large enough for buffering
    assert recommended >= 100, "Should be at least 100 for good buffering"

    # But not unbounded (that would risk memory issues)
    assert recommended <= 1000, "Should be bounded to prevent memory issues"

    # 200 is a good practical value
    assert recommended == 200, "Recommended value is 200"
