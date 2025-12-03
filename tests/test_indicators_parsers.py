import json
from pathlib import Path

from g2.alphavantage import indicators
from g2.indicators.local import compute_indicators


def load_fixture(name: str) -> dict:
    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text())


def test_parse_rsi():
    payload = load_fixture("demo_indicator_rsi.json")
    rows = indicators.parse_rsi(payload)
    assert len(rows) == 2
    assert rows[0]["rsi_14"] is not None


def test_parse_macd():
    payload = load_fixture("demo_indicator_macd.json")
    rows = indicators.parse_macd(payload)
    assert len(rows) == 2
    assert {"macd", "macd_signal", "macd_hist"} <= set(rows[0].keys())


def test_parse_bbands():
    payload = load_fixture("demo_indicator_bbands.json")
    rows = indicators.parse_bbands(payload)
    assert len(rows) == 2
    assert {"bb_upper", "bb_middle", "bb_lower"} <= set(rows[0].keys())


def test_parse_adx():
    payload = {
        "Technical Analysis: ADX": {
            "2023-07-21": {"ADX": "25.5"},
            "2023-07-20": {"ADX": "24.1"},
        }
    }
    rows = indicators.parse_adx(payload)
    assert len(rows) == 2
    assert rows[0]["adx_14"] is not None


def test_parse_stoch():
    payload = {
        "Technical Analysis: STOCH": {
            "2023-07-21": {"SlowK": "50.1", "SlowD": "40.2"},
            "2023-07-20": {"SlowK": "48.0", "SlowD": "45.0"},
        }
    }
    rows = indicators.parse_stoch(payload)
    assert len(rows) == 2
    assert rows[0]["stoch_k"] == 50.1
    assert rows[0]["stoch_d"] == 40.2


def test_parse_sma_and_ema():
    sma_payload = {
        "Technical Analysis: SMA": {
            "2023-07-21": {"SMA": "135.0"},
            "2023-07-20": {"SMA": "134.0"},
        }
    }
    ema_payload = {
        "Technical Analysis: EMA": {
            "2023-07-21": {"EMA": "136.0"},
            "2023-07-20": {"EMA": "135.0"},
        }
    }
    sma_rows = indicators.parse_sma(sma_payload, period=50)
    ema_rows = indicators.parse_ema(ema_payload, period=12)
    assert sma_rows[0]["sma_50"] == 135.0
    assert "sma" not in sma_rows[0]
    assert ema_rows[0]["ema_12"] == 136.0


def test_local_compute_indicators_basic():
    price_rows = [
        {"date": f"2023-07-{i:02d}", "close": 10 + i, "adjusted_close": 10 + i, "high": 11 + i, "low": 9 + i}
        for i in range(1, 25)
    ]
    rows = compute_indicators(price_rows, ["rsi", "macd", "bbands", "stoch", "sma50", "ema12"])
    assert rows
    last = rows[-1]
    assert any("rsi_14" in r for r in rows)
    assert any("macd" in r for r in rows)
