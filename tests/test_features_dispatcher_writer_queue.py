from datetime import date

from g2.features.dispatcher import _process_function_group


def test_process_function_group_with_writer_queue(monkeypatch):
    rows_written = {}

    def fake_fetch(conn, data_id, source_key, features, start_date=None):
        return [{"date": date(2025, 1, 1), "value": 1.0}]

    def fake_insert(conn, data_id, rows, feature_map, update_existing=False, batch_size=2000):
        rows_written["called"] = True
        rows_written["rows"] = rows
        rows_written["feature_map"] = feature_map
        return len(rows)

    monkeypatch.setattr("g2.features.dispatcher._fetch_source_data", fake_fetch)
    monkeypatch.setattr("g2.features.dispatcher.insert_computed_features", fake_insert)

    features = [
        (1, "feat1", "indicator", {}, "stock_ohlcv", "close", "computed_features", "value"),
    ]

    # Patch resolve_compute_function to bypass DB
    monkeypatch.setattr("g2.features.dispatcher._resolve_compute_function", lambda conn, fn: lambda rows, specs: rows)

    result = _process_function_group(
        conn=None,
        data_id=1,
        function_name="indicator",
        features=features,
        incremental=False,
        update_existing=False,
        latest_by_feature={},
        feature_batch_size=2000,
        writer=lambda rows, fm: fake_insert(None, 1, rows, fm),
    )

    assert result["inserted"] == 1
    assert rows_written.get("called") is True
