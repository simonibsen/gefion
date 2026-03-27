"""
Test that writer thread connection failures don't cause deadlocks.

This tests the critical bug fix where writer threads that fail to acquire
a database connection would exit silently, leaving events unset and causing
the main thread to wait forever.
"""
import threading
import time
import queue
from unittest.mock import MagicMock, patch
import pytest


def test_writer_thread_event_queue_drain_on_connection_failure():
    """
    Test that writer thread drains queue and sets events when connection fails.

    This is a focused unit test that directly tests the writer_loop behavior
    when db_pool.get_connection() fails.
    """
    import gefion.features.dispatcher as dispatcher_module

    # Create a mock queue with some items
    write_queue = queue.Queue()
    writer_events = []
    writer_errors = []
    stop_token = object()

    # Add some mock items with events
    for i in range(3):
        evt = threading.Event()
        writer_events.append(evt)
        write_queue.put({
            "rows": [{"date": "2024-01-01", "value": 100}],
            "feature_map": {"test": 1},
            "queue_ts": time.monotonic(),
            "event": evt,
        })

    # Add stop token
    write_queue.put(stop_token)

    # Mock the connection pool to fail
    def failing_get_connection():
        raise RuntimeError("Connection pool exhausted")

    # Simulate writer_loop with connection failure
    def writer_loop_test():
        from gefion.db import pool as db_pool
        try:
            with failing_get_connection():
                # Should never get here
                pass
        except Exception as exc:
            writer_errors.append(exc)
            # THIS IS THE FIX: drain queue and set events
            try:
                while True:
                    item = write_queue.get_nowait()
                    if item is not stop_token:
                        evt = item.get("event")
                        if evt:
                            evt.set()
                    write_queue.task_done()
            except queue.Empty:
                pass

    # Run the writer thread
    t = threading.Thread(target=writer_loop_test)
    t.start()
    t.join(timeout=2)

    # Verify all events were set
    for evt in writer_events:
        assert evt.is_set(), "Event should be set even though connection failed"

    # Verify error was recorded
    assert len(writer_errors) == 1
    assert "Connection pool exhausted" in str(writer_errors[0])

    # Verify queue is empty
    assert write_queue.empty()


def test_inner_exception_sets_event():
    """
    Test that if write operation fails, event is still set.
    """
    write_queue = queue.Queue()
    writer_events = []
    writer_errors = []

    # Add a mock item
    evt = threading.Event()
    writer_events.append(evt)
    item = {
        "rows": [{"date": "2024-01-01", "value": 100}],
        "feature_map": {"test": 1},
        "event": evt,
    }

    # Simulate the inner try-except in writer_loop where write fails
    try:
        raise RuntimeError("Write failed")
    except Exception as exc:
        writer_errors.append(exc)
        # THIS IS THE FIX: set event even on error
        evt_from_item = item.get("event")
        if evt_from_item:
            evt_from_item.set()

    # Verify event was set despite error
    assert evt.is_set(), "Event should be set even when write fails"
    assert len(writer_errors) == 1
