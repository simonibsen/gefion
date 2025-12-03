from g2.indicators.local import compute_indicators


def test_compute_indicators_constant_high_low_no_errors():
    rows = []
    for i in range(1, 16):
        rows.append(
            {
                "date": f"2025-01-{i:02d}",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "adjusted_close": 10.0,
                "volume": 1000,
            }
        )
    out = compute_indicators(rows, ["adx", "stoch"])
    assert isinstance(out, list)


def test_compute_indicators_missing_high_low_skips_gracefully():
    rows = [
        {"date": "2025-01-01", "close": 10.0, "adjusted_close": 10.0, "volume": 1000},
        {"date": "2025-01-02", "close": 10.5, "adjusted_close": 10.5, "volume": 1000},
    ]
    out = compute_indicators(rows, ["adx", "stoch"])
    assert isinstance(out, list)
