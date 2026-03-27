import pytest

from gefion import cli


def test_normalize_feature_definition_defaults_and_rejects_legacy():
    base = {"name": "my_feature", "function_name": "fx", "store_table": "computed_features", "store_column": "value"}
    normalized = cli._normalize_feature_definition(base)

    assert normalized["source_table"] == "stock_ohlcv"
    assert normalized["source_column"] == "close"
    assert normalized["store_type"] == "double precision"
    assert normalized["active"] is True

    with pytest.raises(ValueError):
        cli._normalize_feature_definition({**base, "source_table": "stock_prices"})
