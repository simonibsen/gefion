"""
Tests for TA-Lib based indicator functions.

Validates that the indicator functions (indicator_rsi, indicator_sma, etc.)
correctly compute technical indicators using TA-Lib.
"""
import os
import json
import pytest
import psycopg
from datetime import date, timedelta
from pathlib import Path

from gefion.db import schema


@pytest.fixture(scope="module", autouse=True)
def _restore_db_after_module():
    """Restore canonical test DB state after this module's destructive cleanup (issue #29)."""
    yield
    from conftest import restore_test_db
    restore_test_db()


@pytest.fixture
def db_conn():
    """Create test database connection with feature functions loaded."""
    if not os.getenv("ENABLE_DB_TESTS"):
        pytest.skip("Database tests not enabled (set ENABLE_DB_TESTS=1)")
    db_url = schema.test_db_url()

    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            from conftest import reset_public_schema
            reset_public_schema(conn)

            # Create schema
            schema.create_feature_functions_table(conn)

            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


@pytest.fixture
def sample_ohlcv_data():
    """Generate sample OHLCV data for indicator testing."""
    # Generate 60 days of price data with realistic price movement
    # 60 rows ensures enough warm-up for all indicators:
    # - MACD needs ~34 rows (26 slow + 9 signal - 1)
    # - RSI-30 needs 30 rows
    # - SMA-200 would need 200 rows (not tested here)
    base_price = 100.0
    data = []
    for i in range(60):
        close = base_price + (i * 0.5) + ((-1) ** i * 0.3)  # Trending up with small oscillation
        data.append({
            'date': date(2025, 1, 1) + timedelta(days=i),
            'open': close - 0.5,
            'high': close + 1.0,
            'low': close - 1.0,
            'close': close,
            'adjusted_close': close,
            'volume': 1000000
        })
    return data


def _load_function_from_json(conn, function_name):
    """Load a function definition from JSON file into database."""
    json_path = Path(__file__).parent.parent / "feature-functions" / f"{function_name}.json"
    if not json_path.exists():
        pytest.skip(f"Function definition not found: {json_path}")

    with open(json_path) as f:
        func_def = json.load(f)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, description, status, enabled, created_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (name, version) DO UPDATE SET
                function_body = EXCLUDED.function_body,
                enabled = EXCLUDED.enabled
        """, (
            func_def['name'],
            func_def['version'],
            func_def['language'],
            func_def['function_body'],
            func_def.get('description', ''),
            func_def.get('status', 'active'),
            func_def.get('enabled', True),
            func_def.get('created_by', 'test')
        ))


def _execute_function(conn, function_name, rows, specs):
    """Execute a feature function from the database."""
    from gefion.features.dispatcher import _load_db_function

    result = _load_db_function(conn, function_name)
    if not result:
        pytest.fail(f"Function '{function_name}' not found in database")

    func, version = result
    return func(rows, specs)


class TestIndicatorRSI:
    """Tests for RSI indicator function."""

    def test_rsi_computes_values_in_valid_range(self, db_conn, sample_ohlcv_data):
        """RSI values should be between 0 and 100."""
        _load_function_from_json(db_conn, "indicator_rsi")

        specs = [{'period': 14}]
        results = _execute_function(db_conn, "indicator_rsi", sample_ohlcv_data, specs)

        assert len(results) > 0, "Should produce some results"

        for row in results:
            if 'rsi_14' in row:
                assert 0 <= row['rsi_14'] <= 100, f"RSI should be 0-100, got {row['rsi_14']}"

    def test_rsi_with_different_periods(self, db_conn, sample_ohlcv_data):
        """RSI should work with different period parameters."""
        _load_function_from_json(db_conn, "indicator_rsi")

        specs = [{'period': 14}, {'period': 30}]
        results = _execute_function(db_conn, "indicator_rsi", sample_ohlcv_data, specs)

        has_rsi_14 = any('rsi_14' in row for row in results)
        has_rsi_30 = any('rsi_30' in row for row in results)

        assert has_rsi_14, "Should compute RSI-14"
        assert has_rsi_30, "Should compute RSI-30"


class TestIndicatorSMA:
    """Tests for SMA indicator function."""

    def test_sma_computes_moving_average(self, db_conn, sample_ohlcv_data):
        """SMA should compute correct moving average."""
        _load_function_from_json(db_conn, "indicator_sma")

        specs = [{'period': 20}]
        results = _execute_function(db_conn, "indicator_sma", sample_ohlcv_data, specs)

        assert len(results) > 0, "Should produce some results"

        # SMA should have values after period-1 warm-up
        has_sma = any('sma_20' in row for row in results)
        assert has_sma, "Should compute SMA-20"

    def test_sma_with_multiple_periods(self, db_conn, sample_ohlcv_data):
        """SMA should work with multiple periods."""
        _load_function_from_json(db_conn, "indicator_sma")

        specs = [{'period': 20}, {'period': 50}]
        results = _execute_function(db_conn, "indicator_sma", sample_ohlcv_data, specs)

        has_sma_20 = any('sma_20' in row for row in results)
        assert has_sma_20, "Should compute SMA-20 (enough data)"


class TestIndicatorEMA:
    """Tests for EMA indicator function."""

    def test_ema_computes_exponential_average(self, db_conn, sample_ohlcv_data):
        """EMA should compute exponential moving average."""
        _load_function_from_json(db_conn, "indicator_ema")

        specs = [{'period': 12}]
        results = _execute_function(db_conn, "indicator_ema", sample_ohlcv_data, specs)

        assert len(results) > 0, "Should produce some results"
        has_ema = any('ema_12' in row for row in results)
        assert has_ema, "Should compute EMA-12"


class TestIndicatorMACD:
    """Tests for MACD indicator function."""

    def test_macd_computes_all_components(self, db_conn, sample_ohlcv_data):
        """MACD should compute macd, signal, and histogram."""
        _load_function_from_json(db_conn, "indicator_macd")

        specs = [{'output': 'all'}]
        results = _execute_function(db_conn, "indicator_macd", sample_ohlcv_data, specs)

        has_macd = any('macd' in row for row in results)
        has_signal = any('macd_signal' in row for row in results)
        has_hist = any('macd_hist' in row for row in results)

        assert has_macd, "Should compute MACD line"
        assert has_signal, "Should compute MACD signal"
        assert has_hist, "Should compute MACD histogram"


class TestIndicatorBB:
    """Tests for Bollinger Bands indicator function."""

    def test_bb_computes_all_bands(self, db_conn, sample_ohlcv_data):
        """BB should compute upper, middle, and lower bands."""
        _load_function_from_json(db_conn, "indicator_bb")

        specs = [{'output': 'all'}]
        results = _execute_function(db_conn, "indicator_bb", sample_ohlcv_data, specs)

        has_upper = any('bb_upper' in row for row in results)
        has_middle = any('bb_middle' in row for row in results)
        has_lower = any('bb_lower' in row for row in results)

        assert has_upper, "Should compute upper band"
        assert has_middle, "Should compute middle band"
        assert has_lower, "Should compute lower band"

    def test_bb_bands_are_ordered(self, db_conn, sample_ohlcv_data):
        """Upper band should be > middle > lower."""
        _load_function_from_json(db_conn, "indicator_bb")

        specs = [{'output': 'all'}]
        results = _execute_function(db_conn, "indicator_bb", sample_ohlcv_data, specs)

        for row in results:
            if all(k in row for k in ['bb_upper', 'bb_middle', 'bb_lower']):
                assert row['bb_upper'] >= row['bb_middle'], "Upper >= middle"
                assert row['bb_middle'] >= row['bb_lower'], "Middle >= lower"


class TestIndicatorADX:
    """Tests for ADX indicator function."""

    def test_adx_computes_values_in_valid_range(self, db_conn, sample_ohlcv_data):
        """ADX values should be between 0 and 100."""
        _load_function_from_json(db_conn, "indicator_adx")

        specs = [{'period': 14}]
        results = _execute_function(db_conn, "indicator_adx", sample_ohlcv_data, specs)

        for row in results:
            if 'adx_14' in row:
                assert 0 <= row['adx_14'] <= 100, f"ADX should be 0-100, got {row['adx_14']}"


class TestIndicatorStoch:
    """Tests for Stochastic indicator function."""

    def test_stoch_computes_k_and_d(self, db_conn, sample_ohlcv_data):
        """Stochastic should compute %K and %D."""
        _load_function_from_json(db_conn, "indicator_stoch")

        specs = [{'output': 'all'}]
        results = _execute_function(db_conn, "indicator_stoch", sample_ohlcv_data, specs)

        has_k = any('stoch_k' in row for row in results)
        has_d = any('stoch_d' in row for row in results)

        assert has_k, "Should compute Stochastic %K"
        assert has_d, "Should compute Stochastic %D"

    def test_stoch_values_in_valid_range(self, db_conn, sample_ohlcv_data):
        """Stochastic values should be between 0 and 100."""
        _load_function_from_json(db_conn, "indicator_stoch")

        specs = [{'output': 'all'}]
        results = _execute_function(db_conn, "indicator_stoch", sample_ohlcv_data, specs)

        for row in results:
            if 'stoch_k' in row:
                assert 0 <= row['stoch_k'] <= 100, f"Stoch K should be 0-100"
            if 'stoch_d' in row:
                assert 0 <= row['stoch_d'] <= 100, f"Stoch D should be 0-100"


class TestIndicatorPSAR:
    """Tests for Parabolic SAR indicator function."""

    def test_psar_computes_values(self, db_conn, sample_ohlcv_data):
        """PSAR should compute stop and reverse values."""
        _load_function_from_json(db_conn, "indicator_psar")

        specs = [{}]
        results = _execute_function(db_conn, "indicator_psar", sample_ohlcv_data, specs)

        has_psar = any('psar' in row for row in results)
        assert has_psar, "Should compute PSAR"

    def test_psar_values_are_positive(self, db_conn, sample_ohlcv_data):
        """PSAR values should be positive (prices)."""
        _load_function_from_json(db_conn, "indicator_psar")

        specs = [{}]
        results = _execute_function(db_conn, "indicator_psar", sample_ohlcv_data, specs)

        for row in results:
            if 'psar' in row:
                assert row['psar'] > 0, "PSAR should be positive"


class TestIndicatorEmptyInput:
    """Tests for handling empty/invalid input."""

    def test_empty_rows_returns_empty(self, db_conn):
        """Empty input should return empty results."""
        _load_function_from_json(db_conn, "indicator_rsi")

        results = _execute_function(db_conn, "indicator_rsi", [], [{'period': 14}])
        assert results == [], "Empty input should return empty list"

    def test_insufficient_data_handles_gracefully(self, db_conn):
        """Less data than period should not crash."""
        _load_function_from_json(db_conn, "indicator_sma")

        # Only 5 rows but requesting 20-period SMA
        short_data = [
            {'date': date(2025, 1, i + 1), 'close': 100.0 + i, 'adjusted_close': 100.0 + i}
            for i in range(5)
        ]

        # Should not raise exception
        results = _execute_function(db_conn, "indicator_sma", short_data, [{'period': 20}])
        assert isinstance(results, list)
