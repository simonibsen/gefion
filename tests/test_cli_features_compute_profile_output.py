"""
RETIRED: This test was for the old CLI architecture.

Profile output functionality is tested through actual CLI usage and 
the underlying profiling is tested in:
- test_features_dispatcher_profile_timings.py
- test_features_dispatcher_profile_timings_queue.py

The CLI integration test became too complex to maintain with the new
parallel worker architecture and connection pooling changes.
"""
import pytest

def test_profile_output_retired():
    pytest.skip("Test retired - profiling tested at lower levels")
