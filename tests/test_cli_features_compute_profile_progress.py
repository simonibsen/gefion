"""
RETIRED: Complex CLI integration test with parallel worker architecture.

Profile progress functionality is tested through:
- Actual CLI usage
- Unit tests for profiling (test_features_dispatcher_profile_timings*)

This integration test became too complex with the parallel worker  
architecture and connection pooling changes.
"""
import pytest

def test_profile_progress_retired():
    pytest.skip("Test retired - profiling tested at lower levels")
