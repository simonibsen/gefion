from datetime import date

from g2.features import dispatcher


def test_db_wrapper_handles_pandas_style_function(monkeypatch):
    rows = [
        {"date": date(2024, 1, 1), "close": 10.0, "volume": 1000},
        {"date": date(2024, 1, 2), "close": 11.0, "volume": 2000},
    ]
    specs = [{"name": "volume_zscore_20", "feature_id": 1, "window": 2}]

    def compute(df, window=2):
        import pandas as pd
        vol = df["volume"].astype(float)
        z = (vol - vol.rolling(window).mean()) / vol.rolling(window).std()
        return z.fillna(0)

    wrapped = dispatcher._wrap_db_function(compute)
    out = wrapped(rows, specs)

    assert len(out) == 2
    assert out[0]["date"] == date(2024, 1, 1)
    assert "volume_zscore_20" in out[0]
    assert out[0]["source"] == "fx"
