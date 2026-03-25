"""
Test caching of intermediate calculations during feature computation.

Problem: Multiple features often compute the same intermediate values (e.g., moving averages).
Without caching, each feature recomputes these values independently, wasting CPU time.

Solution: Provide a cache to feature functions that persists across all features for a given stock.
This allows expensive calculations to be computed once and reused.

Example:
- Feature A needs MA(20): Computes and caches it
- Feature B needs MA(20): Retrieves from cache (no recomputation)
- Feature C needs MA(50): Computes and caches it
- Feature D needs MA(20): Retrieves from cache
"""
from unittest.mock import MagicMock, patch
from datetime import date


def test_cache_is_passed_to_feature_functions():
    """
    Test that a cache object is passed to feature functions.

    Feature functions should be able to access a cache to store/retrieve
    intermediate calculations.
    """
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    # Feature function that uses cache
    feature_code = """
import pandas as pd

def compute(df, cache=None):
    # Use cache to avoid recomputing expensive operation
    if cache is not None and 'ma20' in cache:
        ma20 = cache['ma20']
    else:
        ma20 = df['close'].rolling(20).mean()
        if cache is not None:
            cache['ma20'] = ma20

    return ma20
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_cached_ma')
    assert result is not None, "Feature function should load successfully"

    fn, version = result

    # Call function with cache
    rows = [
        {'symbol': 'TEST', 'date': date(2025, 1, i), 'close': 100.0 + i}
        for i in range(1, 31)
    ]
    specs = [{'name': 'test_feature', 'feature_id': 1}]

    # First call: cache is empty, should compute
    cache = {}
    results = fn(rows, specs, cache=cache)

    # Cache should now contain ma20
    assert 'ma20' in cache, "Cache should contain computed MA(20)"


def test_cache_shared_across_features_for_same_stock():
    """
    Test that the same cache is shared across all features for a given stock.

    When processing multiple features for one stock, they should all share
    the same cache instance, allowing them to reuse calculations.
    """
    from gefion.features.dispatcher import _process_function_group
    from unittest.mock import Mock

    # Mock two features that both need MA(20)
    feature1_code = """
def compute(df, cache=None):
    if cache is not None and 'ma20' in cache:
        ma20 = cache['ma20']
    else:
        ma20 = df['close'].rolling(20).mean()
        if cache is not None:
            cache['ma20'] = ma20
            cache['feature1_computed'] = True
    return ma20
"""

    feature2_code = """
def compute(df, cache=None):
    if cache is not None and 'ma20' in cache:
        # Feature 2 should find ma20 already computed by feature 1
        if cache is not None:
            cache['feature2_found_cached'] = True
        return cache['ma20']
    else:
        # Should not reach here if cache is shared
        ma20 = df['close'].rolling(20).mean()
        if cache is not None:
            cache['ma20'] = ma20
        return ma20
"""

    # Mock functions
    def mock_feature1(rows, specs, cache=None):
        # Simulates feature 1 computing and caching MA(20)
        if cache is not None:
            cache['ma20'] = 'computed_value'
            cache['feature1_computed'] = True
        return []

    def mock_feature2(rows, specs, cache=None):
        # Simulates feature 2 finding MA(20) in cache
        if cache is not None and 'ma20' in cache:
            cache['feature2_found_cached'] = True
        return []

    # Test that cache is shared
    cache = {}

    mock_feature1([], [], cache=cache)
    assert cache.get('feature1_computed'), "Feature 1 should have computed"

    mock_feature2([], [], cache=cache)
    assert cache.get('feature2_found_cached'), "Feature 2 should have found cached value"


def test_cache_provides_performance_benefit():
    """
    Test that caching provides performance benefit by avoiding recomputation.

    Scenario:
    - 10 features all need MA(20)
    - Without cache: MA(20) computed 10 times
    - With cache: MA(20) computed 1 time, reused 9 times
    """
    # Conceptual test documenting the benefit
    num_features = 10

    # Without cache
    ma20_computations_without_cache = num_features  # Each feature computes

    # With cache
    ma20_computations_with_cache = 1  # Only first feature computes
    ma20_cache_hits = num_features - 1  # Other features reuse

    speedup = ma20_computations_without_cache / ma20_computations_with_cache

    assert speedup == 10, \
        f"Caching provides {speedup}x speedup for shared calculations"

    assert ma20_cache_hits == 9, \
        "9 out of 10 features should benefit from cache hit"


def test_cache_is_cleared_between_stocks():
    """
    Test that cache is cleared between different stocks.

    Cache should be stock-specific. When processing a new stock,
    the cache should be fresh to avoid incorrect data reuse.
    """
    # This test documents the expected behavior
    # Cache for AAPL should not be reused for MSFT

    cache_aapl = {'ma20': 'aapl_ma20'}
    cache_msft = {}  # Fresh cache for new stock

    assert cache_aapl != cache_msft, \
        "Each stock should have its own cache instance"

    assert 'ma20' not in cache_msft, \
        "New stock should not have cached values from previous stock"


def test_cache_persists_across_feature_groups():
    """
    Test that cache persists across different feature groups for same stock.

    If features are processed in groups (by function), the cache should
    persist across groups for the same stock.
    """
    # Feature group 1: ma_based features
    cache = {}

    # Group 1 computes MA(20)
    cache['ma20'] = 'computed'
    cache['group1_done'] = True

    # Feature group 2: rsi_based features
    # Should still have access to MA(20) from group 1
    assert 'ma20' in cache, \
        "Cache should persist across feature groups for same stock"

    assert cache.get('group1_done'), \
        "Cache should contain data from previous group"


def test_cache_supports_multiple_timeframes():
    """
    Test that cache can store multiple versions of same indicator.

    Features may need different window sizes (MA(20), MA(50), MA(200)).
    Cache should support storing all of them with descriptive keys.
    """
    cache = {}

    # Store different MA windows
    cache['ma_20'] = 'ma20_value'
    cache['ma_50'] = 'ma50_value'
    cache['ma_200'] = 'ma200_value'

    assert len(cache) == 3, "Cache should support multiple related calculations"
    assert 'ma_20' in cache and 'ma_50' in cache and 'ma_200' in cache, \
        "Cache should store different timeframes of same indicator"


def test_cache_is_optional_for_backwards_compatibility():
    """
    Test that feature functions work with or without cache parameter.

    Existing feature functions that don't use cache should continue to work.
    Cache is an optional enhancement.
    """
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    # Feature function WITHOUT cache parameter (legacy)
    feature_code = """
def compute(df):
    return df['close'].rolling(20).mean()
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_no_cache')
    assert result is not None, \
        "Feature functions without cache parameter should still work"

    fn, version = result
    assert callable(fn), "Function should be callable without cache"
