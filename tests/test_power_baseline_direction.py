"""Direction pinned to literature for the power baseline (issue #180).

The acceptance guarantee, run through the REAL discovery + SPA gate (006/010)
via the harness core:

- **planted edge** -> admission power RISES with effective-N. Averaging more
  symbols into the market aggregate lifts the conditional edge above the
  idiosyncratic noise, so the discovery gate admits more at the top of the
  N-sweep than at the bottom.
- **pure noise** -> admitted count stays at the false-positive floor regardless
  of N. No amount of symbols manufactures a signal that is not there.
- the candidate battery is byte-identical across every subsample (only N
  varies), so the power change is attributable to N alone.

Deterministic under seed — like the spec-006 negative control, these are stable
regression proofs of the machinery, not probabilistic samples. Runs in seconds
(tiny synthetic universes, small battery).
"""
import os
import sys

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.discovery import power_baseline as pb

sys.path.insert(0, os.path.dirname(__file__))
from power_baseline_synth import make_per_symbol_universe  # noqa: E402


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    c = _conn()
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'pbase-%'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-pbase-%'")
    c.close()


# Battery, sweep, and synthetic parameters fixed a priori (do not tune the
# assertions; tune the fixture). One planted conditioning atom + two decoys.
_ATOMS = [
    {"feature": "cond_0", "cmp": ">", "value": 0.0},
    {"feature": "noise_0", "cmp": ">", "value": 0.0},
    {"feature": "noise_1", "cmp": ">", "value": 0.0},
]
_FRACTIONS = (0.25, 0.5, 1.0)
_DRAWS = 3


def _config(name, seed):
    return pb.PowerBaselineConfig(
        name=name, atoms=_ATOMS, signals=["sig_0"],
        fractions=_FRACTIONS, draws_per_fraction=_DRAWS, seed=seed,
        depth=1, budget=20, tiers=("grammar",),
        holdout_weeks=26, min_effective_n=5, dataset_version="synth-pbase")


def _run(conn, universe, name, seed):
    return pb.run_power_baseline(
        conn, _config(name, seed),
        members=universe.symbols, sector_of=universe.sector_of,
        market_for_subsample=universe.market_for_subsample,
        returns_for_subsample=universe.returns_for_subsample)


def _by_fraction(report):
    return {fr["fraction"]: fr for fr in report["fractions"]}


def test_planted_edge_power_rises_with_n(conn):
    # Params fixed a priori so the edge sits in the transition zone: lost in the
    # idiosyncratic noise at 25% of the universe, recovered once ~4x more symbols
    # are averaged in at 100%. (Tune the fixture, never the assertion.)
    u = make_per_symbol_universe(
        seed=41, n_days=500, n_symbols=80, n_sectors=4, planted=True,
        effect=0.003, common_scale=0.002, idio_scale=0.06, episode_len=10)
    report = _run(conn, u, "pbase-planted", seed=41)
    fr = _by_fraction(report)

    bottom = fr[0.25]["admitted"]["mean"]
    top = fr[1.0]["admitted"]["mean"]
    assert top > bottom, (
        f"admission power did not rise with N: mean admitted "
        f"{bottom} at 25% vs {top} at 100%")
    assert top >= 1.0, "the planted edge must be recovered at full N"

    # effective-N is the correlation-discounted cross-section, rising with N
    assert fr[1.0]["effective_n"]["mean"] > fr[0.25]["effective_n"]["mean"]

    # the report carries the frontier's marginal admission-power per effective-N
    assert report["marginal_admission_power"] is not None
    assert report["marginal_admission_power"] > 0

    # across-draw dispersion is reported (a single draw is too noisy)
    assert "std" in fr[0.5]["admitted"]


def test_noise_stays_at_false_positive_floor(conn):
    u = make_per_symbol_universe(
        seed=17, n_days=500, n_symbols=80, n_sectors=4, planted=False,
        common_scale=0.003, idio_scale=0.05, episode_len=10)
    report = _run(conn, u, "pbase-noise", seed=17)
    fr = _by_fraction(report)

    total_admitted = sum(v for f in report["fractions"]
                         for v in f["admitted"]["values"])
    n_runs = len(_FRACTIONS) * _DRAWS
    # at the v1 gate the measured false-admission rate is ~1/100 runs; allow a
    # tiny floor, but there must be NO systematic rise with N.
    assert total_admitted <= 1, (
        f"noise admitted {total_admitted} regimes over {n_runs} runs — "
        "the gate is manufacturing signal from nothing")
    assert fr[1.0]["admitted"]["mean"] <= fr[0.25]["admitted"]["mean"] + 0.5


def test_battery_identical_across_fractions(conn):
    u = make_per_symbol_universe(
        seed=41, n_days=400, n_symbols=40, n_sectors=4, planted=True,
        effect=0.05, idio_scale=0.05, episode_len=10)
    report = _run(conn, u, "pbase-battery", seed=41)
    # one fingerprint for the whole sweep: the candidate set never shifted
    assert report["battery"]["fixed"] is True
    assert report["battery"]["n_candidates"] > 0
    # every recorded draw carries the same battery fingerprint
    fps = {d["battery_fingerprint"]
           for f in report["fractions"] for d in f["draws"]}
    assert len(fps) == 1


def test_report_is_deterministic(conn):
    u = make_per_symbol_universe(
        seed=41, n_days=400, n_symbols=40, n_sectors=4, planted=True,
        effect=0.05, idio_scale=0.05, episode_len=10)
    a = _run(conn, u, "pbase-repro-a", seed=41)
    b = _run(conn, u, "pbase-repro-b", seed=41)

    def curve(r):
        return [(round(p["effective_n"], 6), p["admitted"]) for p in r["curve"]]
    assert curve(a) == curve(b)
