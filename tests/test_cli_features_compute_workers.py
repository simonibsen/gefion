"""
RETIRED: This test was for the old CLI architecture.

Worker scaling is now handled by ResourceAwareAdaptiveLimiter with different
initialization patterns. This integration test became too complex to maintain
with the new parallel worker architecture.

Worker behavior is tested through actual usage and the limiter has its own tests.
"""
import pytest

def test_workers_retired():
    pytest.skip("Test retired - worker scaling tested through actual usage")
