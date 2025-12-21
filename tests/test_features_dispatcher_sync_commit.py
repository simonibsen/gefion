from datetime import date

from g2.features import dispatcher


def test_writer_uses_sync_commit_flag(monkeypatch):
    calls = {}

    monkeypatch.setattr(dispatcher, "_fetch_feature_definitions", lambda conn, function_names=None, feature_names=None: [
        (1, "feat1", "indicator", {}, "stock_ohlcv", "close", "computed_features", "value")
    ])
    monkeypatch.setattr(dispatcher, "_group_by_function_name", lambda defs: {"indicator": defs})
    monkeypatch.setattr(dispatcher, "_latest_dates_for_features", lambda conn, data_id, feature_ids: {})
    monkeypatch.setattr(dispatcher, "_fetch_source_data", lambda conn, data_id, source_key, features, start_date=None: [
        {"date": date(2025, 1, 1), "feat1": 1.0}
    ])
    monkeypatch.setattr(dispatcher, "_resolve_compute_function", lambda conn, fn: lambda rows, specs: rows)

    def fake_insert(conn, data_id, rows, feature_map, update_existing=False, batch_size=2000, sync_commit=False):
        calls["sync_commit"] = sync_commit
        return len(rows)

    monkeypatch.setattr(dispatcher, "insert_computed_features", fake_insert)

    res = dispatcher.compute_features(
        conn=None,
        data_id=1,
        writer_workers=1,
        sync_commit=False,
    )

    assert res["summary"]["total_inserted"] == 1
    assert calls["sync_commit"] is False
