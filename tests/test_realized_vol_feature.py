"""Tests for the realized_vol feature function + realized_vol_20 definition.

TDD: written FIRST. The function is DB-first data (JSON in feature-functions/),
so tests validate the shipped JSON and execute its body on synthetic prices —
verifying the math and, critically, causality (no future data in any value).
"""
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).parent.parent
FX_PATH = REPO / "feature-functions" / "realized_vol.json"
DEF_PATH = REPO / "feature-definitions" / "realized_vol_20.json"


def _load_compute():
    body = json.loads(FX_PATH.read_text())["function_body"]
    ns: dict = {}
    exec(body, ns)  # same execution model as the sandbox harness
    return ns["compute"]


def _rows(prices):
    d0 = date(2024, 1, 1)
    return [{"date": d0 + timedelta(days=i), "close": float(p)}
            for i, p in enumerate(prices)]


def test_function_json_exists_and_is_valid():
    fx = json.loads(FX_PATH.read_text())
    assert fx["name"] == "realized_vol"
    assert fx["language"] == "python"
    assert "def compute(" in fx["function_body"]


def test_definition_json_references_function():
    d = json.loads(DEF_PATH.read_text())
    assert d["name"] == "realized_vol_20"
    assert d["function_name"] == "realized_vol"
    assert d["params"]["period"] == 20
    assert d["params"]["column"] == "realized_vol_20"
    assert d["active"] is True


def test_compute_matches_manual_stddev():
    compute = _load_compute()
    rng = np.random.default_rng(7)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 60))
    out = compute(_rows(prices), [{"period": 20, "column": "realized_vol_20"}])
    assert out, "no output rows"
    last = out[-1]
    rets = np.diff(prices) / prices[:-1]
    expected = float(np.std(rets[-20:], ddof=1) * math.sqrt(252))
    assert abs(last["realized_vol_20"] - expected) / expected < 1e-6


def test_warmup_rows_are_absent():
    compute = _load_compute()
    prices = list(100 + np.arange(60.0))
    out = compute(_rows(prices), [{"period": 20, "column": "realized_vol_20"}])
    # need `period` returns => period+1 prices; earlier dates must not appear
    dates = [r["date"] for r in out]
    assert min(dates) == _rows(prices)[20]["date"]


def test_causality_past_values_stable_when_future_changes():
    compute = _load_compute()
    rng = np.random.default_rng(11)
    base = 100 * np.cumprod(1 + rng.normal(0, 0.01, 50))
    crazy = np.concatenate([base, [base[-1] * 5, base[-1] * 0.1]])
    a = compute(_rows(base), [{"period": 20, "column": "realized_vol_20"}])
    b = compute(_rows(crazy), [{"period": 20, "column": "realized_vol_20"}])
    by_date = {r["date"]: r["realized_vol_20"] for r in b}
    for r in a:
        assert abs(by_date[r["date"]] - r["realized_vol_20"]) < 1e-12
