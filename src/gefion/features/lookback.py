"""Declared lookback for incremental stock-feature compute (#120 item 1b).

A function's registry row (feature_functions.inputs["lookback"]) declares
how much pre-cutoff history its values need — the body is DB-resident and
operator-editable, so the policy travels with the body instead of living in
the dispatcher. Undeclared means full history: the honest default, and the
only correct one for path-dependent bodies (PSAR's state runs from series
start).

Modes:
- window:     exact rolling windows (SMA, BB, stoch, realized_vol) —
              bars = max period x multiplier (default 1)
- converging: recursive / exponentially smoothed (EMA, RSI, MACD, ADX) —
              bars = max period x multiplier (default 25), which puts the
              truncation term (1-alpha)^n below ~1e-10 for every period in
              use; the equality gate (tests/test_windowed_lookback.py)
              enforces rtol 1e-9 against full-history compute
- full:       explicit "fetch everything"

Both bounded modes apply max(min_bars, ...) + buffer; min_bars covers
bodies with hardcoded parameters (MACD's 12/26/9 never appear in specs).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes  # noqa: F401

_DEFAULTS = {
    "window": {"multiplier": 1, "min_bars": 20, "buffer": 10},
    "converging": {"multiplier": 25, "min_bars": 400, "buffer": 10},
}

# spec param keys that denote a bar count
_PERIOD_KEYS = ("period", "window", "fast_period", "slow_period",
                "signal_period", "fastk_period", "slowk_period",
                "slowd_period")


class LookbackError(ValueError):
    """Unusable lookback declaration."""


def _max_period(specs: List[Dict[str, Any]]) -> int:
    best = 0
    for spec in specs or []:
        params = spec.get("params", spec) if isinstance(spec, dict) else {}
        for key in _PERIOD_KEYS:
            value = params.get(key)
            if isinstance(value, (int, float)) and value > best:
                best = int(value)
    return best


def lookback_bars(declaration: Optional[Dict[str, Any]],
                  specs: List[Dict[str, Any]]) -> Optional[int]:
    """Bars of pre-cutoff history the group needs; None = full history."""
    if not declaration:
        return None
    mode = declaration.get("mode")
    if mode == "full":
        return None
    if mode not in _DEFAULTS:
        raise LookbackError(
            f"unknown lookback mode {mode!r} — expected 'window', "
            f"'converging', or 'full'")
    d = _DEFAULTS[mode]
    multiplier = declaration.get("multiplier", d["multiplier"])
    min_bars = declaration.get("min_bars", d["min_bars"])
    buffer = declaration.get("buffer", d["buffer"])
    return int(max(min_bars, math.ceil(_max_period(specs) * multiplier))
               + buffer)
