"""
RETIRED: Tests for old features-run command architecture.

The features-run command and ingest_indicators_for_symbols function
were removed during backward compatibility cleanup. 

New architecture uses:
- feat-compute command with connection pooling
- Feature functions in feature_functions table (no code registration)
- Tested via integration tests and actual CLI usage
"""
import pytest

def test_old_architecture_retired():
    pytest.skip("Test retired - old features-run architecture removed")
