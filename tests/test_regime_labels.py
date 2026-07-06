"""Tests for regime label computation (005 T011).

TDD: written FIRST. Core computation is pure (synthetic feature series) so the
causal guarantees are exercised without real DB features; one DB test covers
storage + provenance.
"""
import os
import datetime as dt

import pytest

from gefion.regimes.labels import (
    rolling_tercile_labels,
    apply_min_dwell,
    episodes,
    mean_dwell,
    effective_n,
    is_flicker,
    compute_labels,
    UNDEFINED,
)
from gefion.regimes.definitions import RegimeDefinition


def _dates(n):
    d0 = dt.date(2024, 1, 1)
    return [d0 + dt.timedelta(days=i) for i in range(n)]


# --- rolling causal terciles ---------------------------------------------

def test_first_window_minus_one_are_undefined():
    series = list(zip(_dates(5), [10, 20, 30, 40, 50]))
    out = rolling_tercile_labels(series, ["calm", "normal", "stressed"], window=3)
    labels = [lab for _, lab in out]
    assert labels[0] == UNDEFINED and labels[1] == UNDEFINED
    assert UNDEFINED not in labels[2:]


def test_strictly_increasing_series_is_always_stressed():
    series = list(zip(_dates(6), [10, 20, 30, 40, 50, 60]))
    out = rolling_tercile_labels(series, ["calm", "normal", "stressed"], window=3)
    assert [lab for _, lab in out][2:] == ["stressed"] * 4


def test_no_lookahead_past_labels_stable_when_future_appended():
    base = list(zip(_dates(5), [10, 20, 30, 40, 50]))
    ext = list(zip(_dates(8), [10, 20, 30, 40, 50, 999, -999, 500]))
    a = rolling_tercile_labels(base, ["calm", "normal", "stressed"], window=3)
    b = rolling_tercile_labels(ext, ["calm", "normal", "stressed"], window=3)
    # overlapping first 5 labels must be identical (no future info leaked)
    assert [l for _, l in a] == [l for _, l in b][:5]


# --- persistence / episodes ----------------------------------------------

def test_min_dwell_absorbs_flicker():
    labels = list(zip(_dates(5), ["a", "b", "a", "b", "a"]))
    out = apply_min_dwell(labels, min_dwell=2)
    # nothing persists 2 in a row → collapses to a single confirmed label
    assert len({lab for _, lab in out}) == 1


def test_two_real_episodes_survive_min_dwell():
    labels = list(zip(_dates(6), ["a", "a", "a", "b", "b", "b"]))
    out = apply_min_dwell(labels, min_dwell=2)
    eps = episodes(out)
    assert len(eps) == 2
    assert [e[0] for e in eps] == ["a", "b"]


def test_effective_n_counts_episodes_not_days():
    labels = list(zip(_dates(6), ["a", "a", "a", "b", "b", "b"]))
    assert effective_n(labels, "a") == 1  # one 3-day episode, not 3
    assert effective_n(labels, "b") == 1


def test_mean_dwell_and_flicker():
    steady = list(zip(_dates(6), ["a", "a", "a", "b", "b", "b"]))
    flicker = list(zip(_dates(6), ["a", "b", "a", "b", "a", "b"]))
    assert mean_dwell(steady) == 3.0
    assert is_flicker(flicker, floor=2.0) is True
    assert is_flicker(steady, floor=2.0) is False


def test_episodes_exclude_undefined():
    labels = list(zip(_dates(4), [UNDEFINED, "a", "a", UNDEFINED]))
    eps = episodes(labels)
    assert len(eps) == 1 and eps[0][0] == "a"


# --- top-level compute_labels --------------------------------------------

def test_compute_labels_market_quantile_regime():
    defn = RegimeDefinition(
        name="vol-regime", scope="market",
        expression={"leaf": "comparison", "feature": "realized_vol_20",
                    "cmp": "quantile", "value": "tercile", "scope": "market"},
        bucketing={"labels": ["calm", "normal", "stressed"], "method": "tercile"},
    )
    series = list(zip(_dates(6), [10, 20, 30, 40, 50, 60]))
    rows = compute_labels(defn, {"realized_vol_20": series}, window=3, dataset_version="dev")
    # market scope → entity_id 0; one row per date; provenance carried
    assert all(r[1] == 0 for r in rows)
    assert len(rows) == 6
    assert rows[-1][2] == "stressed"


def test_compute_labels_boolean_composite():
    defn = RegimeDefinition(
        name="risk-on", scope="market",
        expression={"op": "AND", "children": [
            {"leaf": "comparison", "feature": "a", "cmp": ">", "value": 0, "scope": "market"},
            {"leaf": "comparison", "feature": "b", "cmp": ">", "value": 0, "scope": "market"},
        ]},
        bucketing={"labels": ["true", "false"]},
    )
    a = list(zip(_dates(3), [1, -1, 1]))
    b = list(zip(_dates(3), [1, 1, -1]))
    rows = compute_labels(defn, {"a": a, "b": b}, dataset_version="dev")
    assert [r[2] for r in rows] == ["true", "false", "false"]


def test_compute_labels_rejects_detector_leaf():
    defn = RegimeDefinition(
        name="hmm", scope="market",
        expression={"leaf": "detector_function", "function_id": 1, "scope": "market"},
        bucketing={"labels": ["s0", "s1"]},
    )
    with pytest.raises(NotImplementedError):
        compute_labels(defn, {}, dataset_version="dev")


# --- DB storage + provenance ---------------------------------------------

@pytest.fixture
def conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled")
    import psycopg
    from gefion.db import schema
    try:
        c = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_labels")
        cur.execute("DELETE FROM regime_definitions")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_labels")
        cur.execute("DELETE FROM regime_definitions")
    c.close()


def test_compute_and_store_writes_labels_with_provenance(conn):
    from gefion.regimes.definitions import store_definition
    from gefion.regimes.labels import compute_and_store
    defn = RegimeDefinition(
        name="vol-regime", scope="market",
        expression={"leaf": "comparison", "feature": "realized_vol_20",
                    "cmp": "quantile", "value": "tercile", "scope": "market"},
        bucketing={"labels": ["calm", "normal", "stressed"], "method": "tercile"},
    )
    store_definition(conn, defn)
    series = list(zip(_dates(6), [10, 20, 30, 40, 50, 60]))
    n = compute_and_store(conn, defn, {"realized_vol_20": series},
                          window=3, dataset_version="dev-2024")
    assert n == 6
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT dataset_version) FROM regime_labels")
        cnt, versions = cur.fetchone()
    assert cnt == 6 and versions == 1


@pytest.fixture
def canonical_db():
    """Earlier destructive suite modules can leave gutted/minimal tables behind
    (e.g. a stocks table without `sector`). Restore the canonical test DB FIRST,
    then open a fresh connection — restoring under an open connection breaks it."""
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled")
    import psycopg
    from conftest import restore_test_db
    from gefion.db import schema as dbschema
    restore_test_db()
    c = psycopg.connect(dbschema.test_db_url())
    c.autocommit = True
    yield c
    c.close()


def test_market_series_uses_median_not_mean(canonical_db):
    conn = canonical_db
    """The market-level conditioning series must be robust to cross-sectional
    outliers (penny-stock vol, bad split returns): median, not mean (found via
    the first production regime sanity check — Oct 2019 mean vol > Mar 2020)."""
    from gefion.regimes.definitions import RegimeDefinition
    from gefion.regimes.labels import load_market_feature_series

    with conn.cursor() as cur:
        cur.execute("INSERT INTO feature_definitions (name, function_name) "
                    "VALUES ('vol_test_feat', 'realized_vol') "
                    "ON CONFLICT (name) DO UPDATE SET function_name=EXCLUDED.function_name "
                    "RETURNING id")
        fid = cur.fetchone()[0]
        sids = []
        for sym in ("MED1", "MED2", "MED3"):
            cur.execute("INSERT INTO stocks (symbol) VALUES (%s) "
                        "ON CONFLICT (symbol) DO UPDATE SET symbol=EXCLUDED.symbol RETURNING id", (sym,))
            sids.append(cur.fetchone()[0])
        # two sane values + one absurd outlier on the same date
        for sid, val in zip(sids, (0.2, 0.3, 99.0)):
            cur.execute("INSERT INTO computed_features (data_id, date, feature_id, value) "
                        "VALUES (%s, DATE '2024-01-02', %s, %s) "
                        "ON CONFLICT (feature_id, data_id, date) DO UPDATE SET value=EXCLUDED.value",
                        (sid, fid, val))

    defn = RegimeDefinition(
        name="median-test", scope="market",
        expression={"leaf": "comparison", "feature": "vol_test_feat",
                    "cmp": "quantile", "value": "tercile", "scope": "market"},
        bucketing={"labels": ["a", "b", "c"]},
    )
    series = load_market_feature_series(conn, defn)["vol_test_feat"]
    assert len(series) == 1
    # median of (0.2, 0.3, 99.0) is 0.3; the mean (33.17) would be outlier-dominated
    assert abs(series[0][1] - 0.3) < 1e-9

    with conn.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id = %s", (fid,))
        cur.execute("DELETE FROM feature_definitions WHERE id = %s", (fid,))
