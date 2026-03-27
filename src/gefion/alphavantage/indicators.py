from __future__ import annotations

from typing import Dict, List, Mapping


def _parse_series(payload: Mapping[str, object], key: str, fields: Dict[str, str]) -> List[Dict[str, object]]:
    series = payload.get(key, {})
    rows: List[Dict[str, object]] = []
    if not isinstance(series, Mapping):
        return rows
    for date_str, values in series.items():
        row = {"date": date_str}
        try:
            for target, source in fields.items():
                val = values.get(source)
                row[target] = float(val) if val is not None else None
            rows.append(row)
        except Exception:
            continue
    return rows


def parse_rsi(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    return _parse_series(payload, "Technical Analysis: RSI", {"rsi_14": "RSI"})


def parse_macd(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    return _parse_series(
        payload,
        "Technical Analysis: MACD",
        {
            "macd": "MACD",
            "macd_signal": "MACD_Signal",
            "macd_hist": "MACD_Hist",
        },
    )


def parse_bbands(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    return _parse_series(
        payload,
        "Technical Analysis: BBANDS",
        {
            "bb_upper": "Real Upper Band",
            "bb_middle": "Real Middle Band",
            "bb_lower": "Real Lower Band",
        },
    )


def parse_adx(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    return _parse_series(payload, "Technical Analysis: ADX", {"adx_14": "ADX"})


def parse_stoch(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    return _parse_series(
        payload,
        "Technical Analysis: STOCH",
        {
            "stoch_k": "SlowK",
            "stoch_d": "SlowD",
        },
    )


def parse_sma(payload: Mapping[str, object], period: int) -> List[Dict[str, object]]:
    key = f"Technical Analysis: SMA"
    rows = _parse_series(payload, key, {"sma": "SMA"})
    for r in rows:
        r[f"sma_{period}"] = r.pop("sma")
    return rows


def parse_ema(payload: Mapping[str, object], period: int) -> List[Dict[str, object]]:
    key = f"Technical Analysis: EMA"
    rows = _parse_series(payload, key, {"ema": "EMA"})
    for r in rows:
        r[f"ema_{period}"] = r.pop("ema")
    return rows


def parse_psar(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    return _parse_series(payload, "Technical Analysis: SAR", {"psar": "SAR"})
