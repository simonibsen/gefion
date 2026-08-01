"""The strategy_backtests discovery rung (issue #105 — the last of three rungs).

TDD: written FIRST. A hunt may declare signal_source=strategy_backtests and
name strategy-derived series (a strategy's equity curve materialized as a
market-level series); every existing discovery guarantee applies unchanged,
plus the rung's own honesty rules:

- explicit strategy-derived signals only (no silent degradation to an
  indicator hunt),
- the run records the strategy identity (config + implementation) and its
  fit cutoff,
- the equity curve maps to per-observation strategy returns aligned to the
  discovery observation grid, using ONLY equity at/before each date (no
  lookahead — the return at t derives from equity_t and the immediately
  preceding equity point),
- a signal (equity) value at or before the fit cutoff refuses (in-sample by
  construction),
- thin coverage refuses,
- the conservative entanglement rule refuses conditioning atoms drawn from a
  feature the strategy trades on.

The two unit tests (mapping + planted direction) run with NO database.
"""
import datetime as dt
import json
import os

import pytest

D = dt.date
CUTOFF = D(2023, 6, 30)
STRAT_IMPL = "sbk_momentum"
CONFIG = "sbk1"
SIGNAL = f"macro_strategy_{CONFIG}_equity"
SIGNALS = [SIGNAL]
STRAT_INPUT = "indicator_rsi_14"   # the strategy trades on this — entangled
COND_FEATURE = "indicator_adx_14"  # NOT a strategy input — usable


# --------------------------------------------------------------------------
# Unit tests (no DB): the equity-curve -> per-observation mapping + direction
# --------------------------------------------------------------------------

def _market_with_equity(equity_points):
    """MarketData holding an equity level series as the strategy signal plus a
    trivial forward-return grid over the same dates."""
    from gefion.regimes.discovery.segregation import MarketData
    dates = [d for d, _ in equity_points]
    return MarketData(
        features={SIGNAL: list(equity_points)},
        forward_returns=[(d, 0.0) for d in dates],
        dataset_version="strat-test",
    )


def test_equity_curve_maps_to_per_observation_returns_aligned_to_grid():
    """SC: records() turns the equity curve into per-observation strategy
    returns r_t = equity_t / equity_{t-1} - 1, one per grid date (after the
    first), baseline 0.0, and NOTHING from the future enters r_t."""
    from gefion.regimes.discovery.signals import StrategyBacktestSignalSource
    days = [D(2023, 1, 2) + dt.timedelta(days=i) for i in range(5)]
    # equity: 100 -> 110 (+10%) -> 99 (-10%) -> 99 (0%) -> 118.8 (+20%)
    levels = [100.0, 110.0, 99.0, 99.0, 118.8]
    src = StrategyBacktestSignalSource(
        _market_with_equity(list(zip(days, levels))), SIGNALS)
    recs = src.records(SIGNAL)
    got = {r["date"]: r["experimental_score"] for r in recs}
    assert set(got) == set(days[1:])            # aligned to grid, first dropped
    assert all(r["baseline_score"] == 0.0 for r in recs)
    assert got[days[1]] == pytest.approx(0.10)
    assert got[days[2]] == pytest.approx(-0.10)
    assert got[days[3]] == pytest.approx(0.0)
    assert got[days[4]] == pytest.approx(0.20)

    # No-lookahead: mutating a FUTURE equity point cannot change an earlier
    # return (the return at t sees only equity_t and equity_{t-1}).
    mutated = list(zip(days, levels))
    mutated[4] = (days[4], 999.0)               # tamper with the future
    src2 = StrategyBacktestSignalSource(_market_with_equity(mutated), SIGNALS)
    got2 = {r["date"]: r["experimental_score"] for r in src2.records(SIGNAL)}
    for d in days[1:4]:
        assert got2[d] == pytest.approx(got[d]), \
            "a past per-observation return must not depend on future equity"


def test_records_respect_start_end_window():
    from gefion.regimes.discovery.signals import StrategyBacktestSignalSource
    days = [D(2023, 1, 2) + dt.timedelta(days=i) for i in range(6)]
    levels = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    src = StrategyBacktestSignalSource(
        _market_with_equity(list(zip(days, levels))), SIGNALS)
    recs = src.records(SIGNAL, start=days[2], end=days[4])
    assert [r["date"] for r in recs] == days[2:5]


def test_planted_direction_edge_vs_noise():
    """Pin the experimental_score sign to a PLANTED edge (memory: an
    internally-consistent inversion survives every downstream layer).

    Plant a strategy that earns +2%/day in the 'high' regime and 0%/day in
    'low'. The conditional test (alternative='greater') must find a positive,
    significant edge in the high bucket and none in low; an inverted planting
    flips the significant bucket. A pure-noise strategy admits nothing."""
    from gefion.regimes.conditional import conditional_pvalues
    from gefion.regimes.discovery.signals import StrategyBacktestSignalSource

    days = [D(2023, 1, 2) + dt.timedelta(days=i) for i in range(120)]
    labels = {d: ("high" if i % 2 == 0 else "low") for i, d in enumerate(days)}

    def build_equity(ret_for):
        lvl, pts = 100.0, [(days[0], 100.0)]
        for d in days[1:]:
            lvl *= (1.0 + ret_for(labels[d]))
            pts.append((d, lvl))
        return pts

    # planted: positive in high, flat in low
    up = build_equity(lambda lab: 0.02 if lab == "high" else 0.0)
    src = StrategyBacktestSignalSource(_market_with_equity(up), SIGNALS)
    verdicts = {v["bucket"]: v for v in conditional_pvalues(
        src.records(SIGNAL), labels, alternative="greater", min_effective_n=5)}
    hi = [r["experimental_score"] for r in src.records(SIGNAL)
          if labels[r["date"]] == "high"]
    assert sum(hi) / len(hi) > 0                       # planted sign preserved
    assert verdicts["high"]["pvalue"] is not None and verdicts["high"]["pvalue"] < 0.05
    assert (verdicts["low"]["pvalue"] is None
            or verdicts["low"]["pvalue"] > 0.05)

    # inverted planting: the OTHER bucket must carry the edge
    dn = build_equity(lambda lab: 0.02 if lab == "low" else 0.0)
    src_inv = StrategyBacktestSignalSource(_market_with_equity(dn), SIGNALS)
    inv = {v["bucket"]: v for v in conditional_pvalues(
        src_inv.records(SIGNAL), labels, alternative="greater", min_effective_n=5)}
    assert inv["low"]["pvalue"] is not None and inv["low"]["pvalue"] < 0.05
    assert (inv["high"]["pvalue"] is None or inv["high"]["pvalue"] > 0.05)

    # pure noise (deterministic small alternating jitter, mean ~0): no edge
    import numpy as np
    rng = np.random.default_rng(0)
    noise = build_equity(lambda lab: float(rng.normal(0, 0.001)))
    src_n = StrategyBacktestSignalSource(_market_with_equity(noise), SIGNALS)
    nz = {v["bucket"]: v for v in conditional_pvalues(
        src_n.records(SIGNAL), labels, alternative="greater", min_effective_n=5)}
    assert all(v["pvalue"] is None or v["pvalue"] > 0.05 for v in nz.values())


# --------------------------------------------------------------------------
# DB-backed rung tests (mirror test_discovery_model_signals.py)
# --------------------------------------------------------------------------

def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    import psycopg
    from gefion.db import schema
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


def _cleanup(cur):
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'sbk-%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name = %s)", (SIGNAL,))
    cur.execute("DELETE FROM feature_definitions WHERE name = %s", (SIGNAL,))
    cur.execute("DELETE FROM feature_functions WHERE name = %s",
                (f"strategy_{CONFIG}_equity",))
    cur.execute("DELETE FROM macro_series WHERE name = %s",
                (f"strategy_{CONFIG}_equity",))
    cur.execute("DELETE FROM strategy_configs WHERE name = %s", (CONFIG,))
    cur.execute("DELETE FROM strategy_registry WHERE name = %s", (STRAT_IMPL,))
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'SBK%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'SBK%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'SBK%'")


@pytest.fixture(scope="module")
def world():
    """2 stocks x 400 weekday bars; a strategy config; its equity curve
    materialized as a market series post-cutoff, and one conditioning feature
    (indicator_adx_14) plus the strategy's input feature (indicator_rsi_14)."""
    import numpy as np
    from gefion.db import schema
    from gefion.db.ingest import ensure_feature_definitions
    from gefion.regimes.discovery.signals import materialize_strategy_equity
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    schema.create_macro_series_tables(c)
    schema.create_strategy_registry_table(c)
    schema.create_strategy_configs_table(c)
    rng = np.random.default_rng(11)
    days, d = [], D(2023, 1, 2)
    while len(days) < 400:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) VALUES "
                    "('SBK1','Stock'),('SBK2','Stock') RETURNING id")
        ids = [r[0] for r in cur.fetchall()]
        _base = {"function_name": "indicator", "params": None,
                 "source_table": "stock_ohlcv", "source_column": "close",
                 "store_table": "computed_features", "store_column": "value",
                 "store_type": "double precision", "entity_table": "stocks",
                 "active": True}
        defs = ensure_feature_definitions(c, [
            {**_base, "name": STRAT_INPUT}, {**_base, "name": COND_FEATURE}])
        rsi_id, adx_id = defs[STRAT_INPUT], defs[COND_FEATURE]
        for i, day in enumerate(days):
            for j, sid in enumerate(ids):
                close = 100.0 * (1.0 + 0.0002 * i) + j + float(rng.normal(0, 1))
                cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open, high,
                    low, close, adjusted_close, volume)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,1000) ON CONFLICT DO NOTHING""",
                            (sid, day, close, close, close, close, close))
                for fid, val in ((rsi_id, 50.0 + float(rng.normal(0, 8))),
                                 (adx_id, 25.0 + float(rng.normal(0, 6)))):
                    cur.execute("""INSERT INTO computed_features (data_id, date,
                        feature_id, value) VALUES (%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING""", (sid, day, fid, val))
        cur.execute("""INSERT INTO strategy_registry (name, module_path, class_name)
                       VALUES (%s,'gefion.strategies.momentum','MomentumStrategy')
                       ON CONFLICT (name) DO NOTHING""", (STRAT_IMPL,))
        cur.execute("""INSERT INTO strategy_configs (name, strategy_name, params)
                       VALUES (%s,%s,'{}') ON CONFLICT (name) DO NOTHING""",
                    (CONFIG, STRAT_IMPL))
    # equity curve over the POST-cutoff grid (a real backtest's equity is
    # point-in-time by construction; here a smooth compounding curve)
    post = [day for day in days if day > CUTOFF]
    lvl, curve = 100.0, []
    for k, day in enumerate(post):
        lvl *= (1.0 + 0.001 + 0.0005 * float(rng.normal()))
        curve.append((day, lvl))
    materialize_strategy_equity(
        c, config_name=CONFIG, strategy_name=STRAT_IMPL, equity_curve=curve,
        fit_cutoff=CUTOFF, input_features=[STRAT_INPUT])
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


@pytest.fixture()
def atoms_file(tmp_path):
    p = tmp_path / "atoms.json"
    p.write_text(json.dumps({"atoms": [
        {"feature": COND_FEATURE, "form": "tercile"},
        {"feature": STRAT_INPUT, "form": "tercile"},
    ]}))
    return p


def _start(runner, name, atoms_path, *extra):
    from gefion.cli import app
    from gefion.db import schema
    return runner.invoke(app, [
        "regime", "discover", "start", "--name", name,
        "--atoms", str(atoms_path),
        "--signal-source", "strategy_backtests",
        "--signal", SIGNAL,
        "--tier", "grammar",
        "--horizon-days", "5", "--holdout-weeks", "8",
        "--min-effective-n", "5",
        "--universe-filter", "passthrough",
        "--dataset", "sbk-synth",
        "--db-url", schema.test_db_url(), *extra])


def test_rung_end_to_end_records_provenance_and_entanglement(world, atoms_file):
    """A full synthetic hunt on strategy signals completes; the run row records
    signal_source + strategy identity + fit cutoff; the strategy's own input
    feature is refused as a conditioning atom (conservative rule)."""
    from typer.testing import CliRunner
    r = _start(CliRunner(), "sbk-e2e", atoms_file)
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("""SELECT id, search_space FROM regime_discovery_runs
                       WHERE name = 'sbk-e2e'""")
        run_id, space = cur.fetchone()
        assert space["signal_source"] == "strategy_backtests"
        strat = space["strategy"]
        assert strat["fit_cutoff"] == CUTOFF.isoformat()
        assert any(s["config"] == CONFIG and s["strategy_name"] == STRAT_IMPL
                   for s in strat["strategies"])
        assert STRAT_INPUT in strat["input_features"]
        cur.execute("""SELECT detail FROM discovery_diagnostics
                       WHERE run_id = %s AND kind = 'entangled'""", (run_id,))
        entangled = [row[0] for row in cur.fetchall()]
        assert any(d.get("feature") == STRAT_INPUT for d in entangled), \
            "the strategy's input feature must be refused as a conditioning atom"
        assert not any(d.get("feature") == COND_FEATURE for d in entangled)


def test_rung_plugs_into_spa_and_grading_downstream(world, atoms_file):
    """The rung feeds the SAME downstream: a non-empty family carries an in-run
    SPA re-verdict (mean-form) and the run reaches 'complete'."""
    from typer.testing import CliRunner
    r = _start(CliRunner(), "sbk-downstream", atoms_file)
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output) if r.output.strip().startswith("{") else None
    with world.cursor() as cur:
        cur.execute("SELECT id, status, family_size FROM regime_discovery_runs "
                    "WHERE name = 'sbk-downstream'")
        run_id, status, family_size = cur.fetchone()
        assert status == "complete"
        if family_size and family_size > 0:
            cur.execute("SELECT count(*) FROM spa_reverdicts WHERE run_id = %s",
                        (run_id,))
            assert cur.fetchone()[0] >= 1, "non-empty family must carry a SPA re-verdict"


def test_rung_requires_strategy_derived_signals(world, atoms_file):
    """No silent degradation: an indicator signal under the strategy rung refuses."""
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.db import schema
    r = CliRunner().invoke(app, [
        "regime", "discover", "start", "--name", "sbk-wrongsig",
        "--atoms", str(atoms_file),
        "--signal-source", "strategy_backtests",
        "--signal", COND_FEATURE,
        "--universe-filter", "passthrough",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "strategy" in r.output.lower()


def test_rung_requires_explicit_signals(world, atoms_file):
    """Defaulting to 'all active features' is meaningless for this rung."""
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.db import schema
    r = CliRunner().invoke(app, [
        "regime", "discover", "start", "--name", "sbk-nosig",
        "--atoms", str(atoms_file),
        "--signal-source", "strategy_backtests",
        "--universe-filter", "passthrough",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "--signal" in r.output


def test_rung_refuses_lookahead_window(world, atoms_file):
    """A signal (equity) value at or before the fit cutoff is lookahead by
    construction (in-sample) — the run must refuse."""
    from typer.testing import CliRunner
    with world.cursor() as cur:
        cur.execute("""SELECT cf.feature_id, cf.data_id FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = %s LIMIT 1""", (SIGNAL,))
        fid, did = cur.fetchone()
        cur.execute("""INSERT INTO computed_features (data_id, date, feature_id,
                       value) VALUES (%s, %s, %s, 100.0)
                       ON CONFLICT (data_id, feature_id, date) DO NOTHING""",
                    (did, CUTOFF, fid))
    try:
        r = _start(CliRunner(), "sbk-lookahead", atoms_file)
        assert r.exit_code == 1
        assert "cutoff" in r.output.lower() or "lookahead" in r.output.lower()
    finally:
        with world.cursor() as cur:
            cur.execute("""DELETE FROM computed_features WHERE data_id = %s
                           AND feature_id = %s AND date = %s""",
                        (did, fid, CUTOFF))


def test_rung_refuses_thin_coverage(world, atoms_file):
    """Coverage below the declared floor refuses."""
    from typer.testing import CliRunner
    with world.cursor() as cur:
        cur.execute("""SELECT cf.feature_id FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = %s LIMIT 1""", (SIGNAL,))
        fid = cur.fetchone()[0]
        lo, hi = CUTOFF + dt.timedelta(days=60), CUTOFF + dt.timedelta(days=140)
        cur.execute("""SELECT data_id, date, value FROM computed_features
                       WHERE feature_id = %s AND date BETWEEN %s AND %s""",
                    (fid, lo, hi))
        removed = cur.fetchall()
        cur.execute("""DELETE FROM computed_features WHERE feature_id = %s
                       AND date BETWEEN %s AND %s""", (fid, lo, hi))
    try:
        r = _start(CliRunner(), "sbk-thin", atoms_file)
        assert r.exit_code == 1
        assert "covers" in r.output.lower()
    finally:
        with world.cursor() as cur:
            for data_id, day, val in removed:
                cur.execute("""INSERT INTO computed_features (data_id, date,
                    feature_id, value) VALUES (%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (data_id, day, fid, val))
