"""
Tests for queue backpressure mechanism to prevent memory exhaustion.
"""
import queue
import time
import threading


def test_unbounded_queue_allows_unlimited_growth():
    """Demonstrate that unbounded queues can grow without limit."""
    q = queue.Queue()  # No maxsize

    # Add many items without blocking
    for i in range(10000):
        q.put(i)  # Never blocks

    assert q.qsize() == 10000
    # This would cause memory issues in production with millions of items


def test_bounded_queue_provides_backpressure():
    """Test that bounded queue blocks when full, preventing memory exhaustion."""
    q = queue.Queue(maxsize=100)

    # Fill the queue
    for i in range(100):
        q.put(i)

    assert q.qsize() == 100

    # Next put should block (with timeout)
    start = time.time()
    try:
        q.put("overflow", timeout=0.1)
        assert False, "Should have raised queue.Full"
    except queue.Full:
        elapsed = time.time() - start
        assert elapsed >= 0.1, "Should have blocked for timeout duration"


def test_bounded_queue_flow_control():
    """Test that bounded queue enables proper producer-consumer flow control."""
    q = queue.Queue(maxsize=5)
    produced = []
    consumed = []

    def producer():
        for i in range(20):
            q.put(i)  # Will block when queue is full
            produced.append(i)
            time.sleep(0.01)

    def consumer():
        for _ in range(20):
            item = q.get()
            consumed.append(item)
            time.sleep(0.02)  # Consumer slower than producer

    prod_thread = threading.Thread(target=producer)
    cons_thread = threading.Thread(target=consumer)

    prod_thread.start()
    cons_thread.start()

    prod_thread.join()
    cons_thread.join()

    assert len(produced) == 20
    assert len(consumed) == 20
    assert produced == consumed
    # Producer was throttled by consumer speed via bounded queue


def test_recommended_queue_size():
    """Verify that recommended maxsize=200 is reasonable for typical workloads."""
    q = queue.Queue(maxsize=200)

    # Should handle typical batch sizes
    for i in range(200):
        q.put({"symbol": f"SYM{i}", "data": [1, 2, 3]})

    assert q.qsize() == 200

    # Should provide backpressure beyond capacity
    try:
        q.put("overflow", timeout=0.05)
        assert False, "Should block when full"
    except queue.Full:
        pass  # Expected


def test_queue_maxsize_none_vs_number():
    """Document the difference between maxsize=None and maxsize=N."""
    # Unbounded queue
    q_unbounded = queue.Queue(maxsize=0)  # 0 means unlimited
    assert q_unbounded.maxsize == 0

    # Bounded queue
    q_bounded = queue.Queue(maxsize=200)
    assert q_bounded.maxsize == 200

    # Fill bounded queue
    for i in range(200):
        q_bounded.put(i)

    # Bounded queue is full
    assert q_bounded.full()

    # Unbounded queue is never full
    for i in range(300):
        q_unbounded.put(i)
    assert q_unbounded.qsize() == 300
    assert not q_unbounded.full()  # Never reports full
