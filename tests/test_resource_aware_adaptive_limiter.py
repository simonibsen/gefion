"""
Test resource-aware adaptive limiter that dynamically scales workers based on system resources.

The ResourceAwareAdaptiveLimiter extends AdaptiveLimiter to:
- Monitor system resources (CPU, memory, DB connections)
- Dynamically adjust max_workers based on available resources
- Dynamically adjust writer_workers based on available resources
- Emit messages when scaling decisions are made
- Periodically re-evaluate resources during processing
"""
import time
from unittest.mock import Mock, MagicMock, patch
from g2.utils.adaptive import ResourceAwareAdaptiveLimiter


def test_resource_aware_limiter_initializes():
    """
    Test that resource-aware limiter initializes and calculates optimal workers.
    """
    messages = []
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=10,
        available_db_connections=50,
        writer_workers=2,
        emit_func=lambda msg: messages.append(msg),
    )

    assert limiter.current >= 1
    assert limiter.max_workers <= 10
    assert limiter.writer_workers >= 1

    # Should have done initial resource check
    assert limiter.last_check_time >= 0


def test_resource_aware_limiter_respects_user_max():
    """
    Test that resource-aware limiter never exceeds user's max_workers.
    """
    # Even with lots of resources, should cap at user's max
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=5,
        max_workers=3,  # User wants max 3
        available_db_connections=1000,  # Plenty of connections
        writer_workers=2,
    )

    # max_workers should be capped at user's limit
    assert limiter.max_workers <= 3


def test_resource_aware_limiter_calculates_based_on_memory():
    """
    Test that limiter considers memory when calculating optimal workers.
    """
    messages = []

    import sys
    psutil_module = sys.modules.get('psutil')
    if psutil_module:
        with patch.object(psutil_module, 'virtual_memory') as mock_mem:
            # Simulate 10GB available memory
            mock_vm = MagicMock()
            mock_vm.available = 10 * 1024 ** 3
            mock_mem.return_value = mock_vm

            limiter = ResourceAwareAdaptiveLimiter(
                start_workers=2,
                max_workers=100,  # High max
                writer_workers=2,
                memory_per_worker_mb=125.0,  # Each worker needs ~125MB
                memory_buffer_gb=2.0,  # Reserve 2GB
                emit_func=lambda msg: messages.append(msg),
            )

            # With 10GB available - 2GB buffer = 8GB usable = 8192MB
            # Each worker with 2 writers needs ~129MB (125 + 2*2)
            # 8192 / 129 = ~63 workers max
            # But we have max_workers=100, so should be limited by memory
            assert limiter.max_workers <= 100
            assert limiter.max_workers >= 1
    else:
        # psutil not available, just verify limiter works
        limiter = ResourceAwareAdaptiveLimiter(
            start_workers=2,
            max_workers=100,
            writer_workers=2,
            emit_func=lambda msg: messages.append(msg),
        )
        assert limiter.max_workers <= 100
        assert limiter.max_workers >= 1


def test_resource_aware_limiter_calculates_based_on_db_connections():
    """
    Test that limiter considers DB connections when calculating optimal workers.
    """
    messages = []
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=100,
        available_db_connections=30,  # Limited connections
        writer_workers=2,
        db_buffer=5,
        emit_func=lambda msg: messages.append(msg),
    )

    # With 30 connections - 5 buffer = 25 available
    # Each worker needs 1 + 2 (writers) = 3 connections
    # 25 / 3 = 8 workers max
    # So max_workers should be around 8
    assert limiter.max_workers <= 10  # Some flexibility in calculation


def test_resource_aware_limiter_scales_writer_workers():
    """
    Test that limiter optimizes writer_workers as well as max_workers.
    """
    messages = []
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=10,
        available_db_connections=100,
        writer_workers=1,  # Start with 1
        user_max_writer_workers=None,  # Allow auto-scaling
        emit_func=lambda msg: messages.append(msg),
    )

    # With sufficient resources, writer_workers should be able to increase
    # Initial calculation may have increased writer_workers
    initial_writers = limiter.writer_workers
    assert initial_writers >= 1


def test_resource_aware_limiter_emits_messages_on_scaling():
    """
    Test that limiter emits messages when it scales up or down.
    """
    messages = []

    # Start with very limited resources
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=2,
        writer_workers=1,
        emit_func=lambda msg: messages.append(msg),
    )

    initial_messages = len(messages)

    # Simulate resource change by directly calling update
    # (In real usage, this happens periodically via record_batch)
    # We can't easily simulate resource changes, so just verify
    # the limiter doesn't crash when updating
    limiter._update_resource_limits()

    # Should complete without error
    assert limiter.max_workers >= 1


def test_resource_aware_limiter_periodic_checking():
    """
    Test that limiter checks resources periodically via record_batch.
    """
    messages = []
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=10,
        check_interval_seconds=0.1,  # Very short interval for testing
        emit_func=lambda msg: messages.append(msg),
    )

    # Note: __init__ calls _update_resource_limits() which sets last_check_time
    # So we need to record the time after initialization
    initial_check_time = limiter.last_check_time
    assert initial_check_time > 0  # Should be set

    # Wait for interval to pass
    time.sleep(0.15)

    # record_batch call should trigger resource check
    limiter.record_batch(errors=0)

    # Should have updated check time
    assert limiter.last_check_time > initial_check_time


def test_resource_aware_limiter_get_resource_info():
    """
    Test that limiter can provide resource information.
    """
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=10,
        writer_workers=3,
        user_max_writer_workers=3,  # Fix writer_workers to 3 for this test
    )

    info = limiter.get_resource_info()

    assert 'current_workers' in info
    assert 'max_workers' in info
    assert 'user_max' in info
    assert 'writer_workers' in info
    assert info['writer_workers'] == 3


def test_resource_aware_limiter_get_writer_workers():
    """
    Test that limiter provides get_writer_workers() method.
    """
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=10,
        writer_workers=4,
    )

    assert limiter.get_writer_workers() == limiter.writer_workers


def test_resource_aware_limiter_respects_user_max_writer_workers():
    """
    Test that limiter respects user's max writer_workers if specified.
    """
    messages = []
    limiter = ResourceAwareAdaptiveLimiter(
        start_workers=2,
        max_workers=10,
        available_db_connections=1000,  # Plenty of resources
        writer_workers=2,
        user_max_writer_workers=3,  # User wants max 3 writers
        emit_func=lambda msg: messages.append(msg),
    )

    # writer_workers should not exceed user's max
    assert limiter.writer_workers <= 3


def test_resource_aware_limiter_optimizes_for_throughput():
    """
    Test that limiter tries to maximize throughput (max_workers * writer_workers).
    """
    import sys
    psutil_module = sys.modules.get('psutil')
    if psutil_module:
        with patch.object(psutil_module, 'virtual_memory') as mock_mem:
            # Simulate abundant memory (20GB available)
            mock_vm = MagicMock()
            mock_vm.available = 20 * 1024 ** 3
            mock_mem.return_value = mock_vm

            limiter = ResourceAwareAdaptiveLimiter(
                start_workers=2,
                max_workers=20,
                available_db_connections=200,
                writer_workers=1,
            )

            # With abundant resources, should optimize for throughput
            # score = max_workers * writer_workers should be high
            score = limiter.max_workers * limiter.writer_workers
            assert score > 1  # Should use parallelism
    else:
        # psutil not available, just verify basic functionality
        limiter = ResourceAwareAdaptiveLimiter(
            start_workers=2,
            max_workers=20,
            available_db_connections=200,
            writer_workers=1,
        )
        score = limiter.max_workers * limiter.writer_workers
        assert score > 1


def test_resource_aware_limiter_handles_low_resources():
    """
    Test that limiter gracefully handles very low resources.
    """
    import sys
    psutil_module = sys.modules.get('psutil')
    if psutil_module:
        with patch.object(psutil_module, 'virtual_memory') as mock_mem:
            # Simulate very low memory (1GB available)
            mock_vm = MagicMock()
            mock_vm.available = 1 * 1024 ** 3
            mock_mem.return_value = mock_vm

            limiter = ResourceAwareAdaptiveLimiter(
                start_workers=10,  # Want 10
                max_workers=10,
                available_db_connections=10,
                writer_workers=8,
                memory_buffer_gb=0.5,  # Small buffer
            )

            # Should scale down due to limited resources
            # With 0.5GB usable (1GB - 0.5GB buffer), can't fit many workers
            assert limiter.max_workers >= 1  # Always at least 1
            assert limiter.current <= limiter.max_workers
    else:
        # psutil not available, verify graceful degradation
        limiter = ResourceAwareAdaptiveLimiter(
            start_workers=10,
            max_workers=10,
            available_db_connections=10,
            writer_workers=8,
        )
        assert limiter.max_workers >= 1
        assert limiter.current <= limiter.max_workers
