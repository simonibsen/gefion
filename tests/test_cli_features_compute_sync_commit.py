"""
RETIRED: This test was for the old CLI architecture.

The synchronous_commit functionality is now tested at a lower level in:
- test_features_dispatcher_sync_commit.py (unit test for dispatcher)
- test_insert_computed_features_prepared.py (unit test for insert)

The CLI integration test became too complex to maintain with the new
parallel worker architecture and connection pooling changes.
"""
import pytest

def test_sync_commit_retired():
    pytest.skip("Test retired - functionality tested at lower levels")
