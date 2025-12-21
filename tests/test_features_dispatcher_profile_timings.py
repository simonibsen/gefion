import time
from datetime import date

from g2.features import dispatcher


def test_compute_features_returns_timings_when_profile(monkeypatch):
    # stub dependencies
    monkeypatch.setattr(dispatcher, "_fetch_feature_definitions", lambda conn, function_names=None, feature_names=None: [
        (1, "feat1", "indicator", {}, "stock_ohlcv", "close", "computed_features", "value")
    ])
    monkeypatch.setattr(dispatcher, "_group_by_function_name", lambda defs: {"indicator": defs})
    monkeypatch.setattr(dispatcher, "_latest_dates_for_features", lambda conn, data_id, feature_ids: {})
    monkeypatch.setattr(dispatcher, "_fetch_source_data", lambda conn, data_id, source_key, features, start_date=None: [
        {"date": date(2025, 1, 1), "feat1": 1.0}
    ])

    def fake_compute(rows, specs):
        time.sleep(0.01)
        # Ensure output uses expected column name
        return [{"date": r["date"], "feat1": r.get("feat1", 0)} for r in rows]

    monkeypatch.setattr(dispatcher, "_resolve_compute_function", lambda conn, fn: fake_compute)

    def fake_insert(conn, data_id, rows, feature_map, update_existing=False, batch_size=2000):
        time.sleep(0.01)
        return len(rows)

    monkeypatch.setattr(dispatcher, "insert_computed_features", fake_insert)

    res = dispatcher.compute_features(
        conn=None,
        data_id=1,
        profile=True,
    )

    timing = res["summary"].get("timing")
    assert timing is not None
    assert timing["fetch"] >= 0
    assert timing["compute"] >= 0.01
    assert timing["write"] >= 0.01
