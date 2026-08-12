"""TDD tests for the universe A/B backtest harness (issue #197).

These tests drive the orchestrator + comparison in
``gefion.backtest.ab_compare``. The per-arm pipeline (dataset-build → train →
predict → backtest) is HEAVY, so it is stubbed here: the tests exercise the
matched-config enforcement, the comparison math + A→B deltas, the
negative-transfer diagnostic, attribution Arm C wiring, and the report shape.
Real NYSE data / a real train run are deliberately NOT required.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def _isolated_ab_checkpoint_dir(tmp_path, monkeypatch):
    """Every test gets its own checkpoint dir (#234).

    Without this, tests that don't explicitly pass ``checkpoint_dir`` would
    read/write the real ``~/.gefion/ab_compare_checkpoints`` and could get a
    stale reuse from a previous test/run — the exact hazard this feature
    exists to prevent, just relocated into the test suite.
    """
    monkeypatch.setenv("GEFION_AB_CHECKPOINT_DIR", str(tmp_path / "ab_checkpoints"))


# --------------------------------------------------------------------------- #
# Fixtures — synthetic ArmResults with a known, hand-checkable structure.
# --------------------------------------------------------------------------- #
def _config():
    from gefion.backtest.ab_compare import MatchedConfig

    return MatchedConfig(
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        split_spec={"scheme": "walk_forward", "folds": 3, "oos": True},
        horizons=[7, 30],
        horizon_days=7,
        hyperparams={"max_depth": 4, "n_estimators": 200},
        strategy_params={"decile": 0.1, "mode": "long_short"},
        initial_capital=100000.0,
    )


def _arm_result(label, train_uni, trade_uni, positions, *,
                total_return=0.20, sharpe=1.5, max_dd=-0.10, n_days=252,
                blown=False, blown_date=None):
    """Build an ArmResult with an equity-curve summary + a positions ledger."""
    from gefion.backtest.ab_compare import ArmResult

    return ArmResult(
        label=label,
        train_universe=train_uni,
        trade_universe=trade_uni,
        metrics={"total_return": total_return, "sharpe_ratio": sharpe,
                 "max_drawdown": max_dd, "num_trades": len(positions)},
        equity_curve=[{"date": date(2020, 1, 1), "equity": 100000.0},
                      {"date": date(2020, 12, 31),
                       "equity": 100000.0 * (1 + total_return)}],
        positions=positions,
        n_trading_days=n_days,
        blown=blown,
        blown_date=blown_date,
    )


def _pos(dt, symbol, side, raw_return, pnl, dollar_volume):
    return {"date": dt, "symbol": symbol, "side": side,
            "raw_return": raw_return, "pnl": pnl, "dollar_volume": dollar_volume}


# --------------------------------------------------------------------------- #
# 1. Matched config — arms differ ONLY by universe.
# --------------------------------------------------------------------------- #
class TestMatchedConfig:
    def test_matched_config_has_no_universe_field(self):
        """The universe is the per-arm axis; it must NOT live on the shared
        config, or arms could silently diverge on more than the universe."""
        from gefion.backtest.ab_compare import MatchedConfig

        names = {f.name for f in fields(MatchedConfig)}
        assert "universe" not in names
        assert "train_universe" not in names
        assert "trade_universe" not in names

    def test_orchestrator_passes_identical_config_to_every_arm(self):
        """Every arm receives the SAME config object — matched by construction."""
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []

        def spy_runner(spec, config, conn):
            calls.append((spec, config))
            return _arm_result(spec.label, spec.train_universe,
                               spec.trade_universe, [])

        run_ab_compare(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            arm_runner=spy_runner,
            universe_resolver=lambda conn, name: {"AAA"},
        )

        assert len(calls) == 2
        (spec_a, cfg_a), (spec_b, cfg_b) = calls
        # Identical config object handed to each arm — nothing can differ.
        assert cfg_a is cfg_b
        assert cfg_a == cfg_b
        # The universe is the ONLY axis that differs.
        assert spec_a.train_universe != spec_b.train_universe
        assert spec_a.train_universe == "nasdaq-only"
        assert spec_b.train_universe == "nasdaq-plus-nyse"


# --------------------------------------------------------------------------- #
# 2. Per-arm summary metrics + A→B deltas.
# --------------------------------------------------------------------------- #
class TestArmSummary:
    def test_summary_metric_keys(self):
        from gefion.backtest.ab_compare import compute_arm_summary

        arm = _arm_result("A", "u", "u", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.05, 500.0, 1_000_000.0),
        ])
        s = compute_arm_summary(arm)
        for key in ("annualized_return", "sharpe", "max_drawdown",
                    "position_breadth", "tail_richness", "capacity_proxy",
                    "n_positions", "n_long", "n_short", "total_return"):
            assert key in s

    def test_position_breadth_is_avg_names_per_date(self):
        from gefion.backtest.ab_compare import compute_arm_summary

        # Date 1 holds 2 names, date 2 holds 4 names → breadth 3.0.
        positions = [
            _pos(date(2020, 1, 1), "A", "long", 0.01, 1, 1e6),
            _pos(date(2020, 1, 1), "B", "short", 0.01, 1, 1e6),
            _pos(date(2020, 2, 1), "A", "long", 0.01, 1, 1e6),
            _pos(date(2020, 2, 1), "B", "long", 0.01, 1, 1e6),
            _pos(date(2020, 2, 1), "C", "short", 0.01, 1, 1e6),
            _pos(date(2020, 2, 1), "D", "short", 0.01, 1, 1e6),
        ]
        s = compute_arm_summary(_arm_result("A", "u", "u", positions))
        assert s["position_breadth"] == pytest.approx(3.0)

    def test_tail_richness_is_long_minus_short_spread(self):
        from gefion.backtest.ab_compare import compute_arm_summary

        # long raw returns avg 0.10, short raw returns avg -0.06 → spread 0.16.
        positions = [
            _pos(date(2020, 1, 1), "A", "long", 0.08, 1, 1e6),
            _pos(date(2020, 1, 1), "B", "long", 0.12, 1, 1e6),
            _pos(date(2020, 1, 1), "C", "short", -0.04, 1, 1e6),
            _pos(date(2020, 1, 1), "D", "short", -0.08, 1, 1e6),
        ]
        s = compute_arm_summary(_arm_result("A", "u", "u", positions))
        assert s["tail_richness"] == pytest.approx(0.16)

    def test_capacity_proxy_is_median_dollar_volume(self):
        from gefion.backtest.ab_compare import compute_arm_summary

        positions = [
            _pos(date(2020, 1, 1), "A", "long", 0.01, 1, 1_000_000.0),
            _pos(date(2020, 1, 1), "B", "long", 0.01, 1, 3_000_000.0),
            _pos(date(2020, 1, 1), "C", "short", 0.01, 1, 9_000_000.0),
        ]
        s = compute_arm_summary(_arm_result("A", "u", "u", positions))
        assert s["capacity_proxy"] == pytest.approx(3_000_000.0)

    def test_annualized_return_geometric(self):
        from gefion.backtest.ab_compare import compute_arm_summary

        # 21% over half a year (126 trading days) annualizes above 21%.
        arm = _arm_result("A", "u", "u", [], total_return=0.21, n_days=126)
        s = compute_arm_summary(arm)
        expected = (1.21) ** (252 / 126) - 1
        assert s["annualized_return"] == pytest.approx(expected)

    def test_deltas_are_b_minus_a(self):
        from gefion.backtest.ab_compare import compute_arm_summary, compute_deltas

        arm_a = _arm_result("A", "u", "u", [], total_return=0.10, sharpe=1.0)
        arm_b = _arm_result("B", "v", "v", [], total_return=0.25, sharpe=1.4)
        d = compute_deltas(compute_arm_summary(arm_a), compute_arm_summary(arm_b))
        assert d["total_return"] == pytest.approx(0.15)
        assert d["sharpe"] == pytest.approx(0.4)

    def test_blown_flag_and_date_surface_in_summary(self):
        """#255: an arm that was wiped is not comparable to one that traded
        through -- the summary must say so explicitly, not just report a
        -1.0 total_return that looks like any other bad run."""
        from gefion.backtest.ab_compare import compute_arm_summary

        arm = _arm_result("B", "u", "u", [], total_return=-1.0,
                          blown=True, blown_date=date(2020, 3, 17))
        s = compute_arm_summary(arm)
        assert s["blown"] is True
        assert s["blown_date"] == "2020-03-17"

    def test_blown_defaults_false_with_no_date_for_a_surviving_arm(self):
        from gefion.backtest.ab_compare import compute_arm_summary

        s = compute_arm_summary(_arm_result("A", "u", "u", []))
        assert s["blown"] is False
        assert s["blown_date"] is None


# --------------------------------------------------------------------------- #
# 3. Negative-transfer diagnostic — restrict Arm B to shared A members.
# --------------------------------------------------------------------------- #
class TestNegativeTransfer:
    def test_flags_dilution_when_b_worse_on_shared_names(self):
        from gefion.backtest.ab_compare import negative_transfer_diagnostic

        shared = {"AAA", "BBB"}  # universe-A members
        # Arm A captures a clean edge on the shared names.
        arm_a = _arm_result("A", "nasdaq-only", "nasdaq-only", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.10, 1, 1e6),
            _pos(date(2020, 1, 1), "BBB", "short", -0.08, 1, 1e6),
        ])
        # Arm B, on those SAME names, does worse (edge diluted); the CCC
        # position is a NYSE-only name and must be excluded from the compare.
        arm_b = _arm_result("B", "nasdaq-plus-nyse", "nasdaq-plus-nyse", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.02, 1, 1e6),
            _pos(date(2020, 1, 1), "BBB", "short", -0.01, 1, 1e6),
            _pos(date(2020, 1, 1), "CCC", "long", 0.30, 1, 1e6),
        ])
        nt = negative_transfer_diagnostic(arm_a, arm_b, shared)

        # Arm A edge = mean(0.10, 0.08) = 0.09; Arm B edge = mean(0.02, 0.01)=0.015
        assert nt["arm_a_edge"] == pytest.approx(0.09)
        assert nt["arm_b_edge"] == pytest.approx(0.015)
        assert nt["delta"] == pytest.approx(0.015 - 0.09)
        assert nt["diluted"] is True
        # Only the 2 shared names counted for B; CCC excluded.
        assert nt["n_shared_b"] == 2
        assert nt["n_shared_a"] == 2

    def test_no_dilution_when_b_at_least_as_good(self):
        from gefion.backtest.ab_compare import negative_transfer_diagnostic

        shared = {"AAA"}
        arm_a = _arm_result("A", "nasdaq-only", "nasdaq-only", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.05, 1, 1e6),
        ])
        arm_b = _arm_result("B", "nasdaq-plus-nyse", "nasdaq-plus-nyse", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.09, 1, 1e6),
        ])
        nt = negative_transfer_diagnostic(arm_a, arm_b, shared)
        assert nt["diluted"] is False


# --------------------------------------------------------------------------- #
# 3b. Shared PnL (issue #248) — must never silently default to 0.0.
# --------------------------------------------------------------------------- #
class TestSharedPnl:
    def test_shared_pnl_is_none_when_positions_lack_it(self):
        """The exact bug: opening-trade positions carry no real dollar pnl
        (pnl realizes on the CLOSING trade). Reporting it as 0.0 makes a
        missing measurement look like a real, authoritative figure."""
        from gefion.backtest.ab_compare import negative_transfer_diagnostic

        shared = {"AAA"}
        arm_a = _arm_result("A", "u", "u", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.05, None, 1e6),
        ])
        arm_b = _arm_result("B", "v", "v", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.02, None, 1e6),
        ])
        nt = negative_transfer_diagnostic(arm_a, arm_b, shared)
        assert nt["arm_a_shared_pnl"] is None
        assert nt["arm_b_shared_pnl"] is None

    def test_shared_pnl_sums_real_values_when_present(self):
        from gefion.backtest.ab_compare import negative_transfer_diagnostic

        shared = {"AAA", "BBB"}
        arm_a = _arm_result("A", "u", "u", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.05, 500.0, 1e6),
            _pos(date(2020, 1, 1), "BBB", "short", -0.02, 200.0, 1e6),
        ])
        arm_b = _arm_result("B", "v", "v", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.02, 100.0, 1e6),
        ])
        nt = negative_transfer_diagnostic(arm_a, arm_b, shared)
        assert nt["arm_a_shared_pnl"] == pytest.approx(700.0)
        assert nt["arm_b_shared_pnl"] == pytest.approx(100.0)

    def test_shared_pnl_partial_data_is_not_reported_as_a_full_total(self):
        """One shared position missing pnl must not silently shrink the
        sum -- that reports a partial total as if it were complete."""
        from gefion.backtest.ab_compare import negative_transfer_diagnostic

        shared = {"AAA", "BBB"}
        arm_a = _arm_result("A", "u", "u", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.05, 500.0, 1e6),
            _pos(date(2020, 1, 1), "BBB", "short", -0.02, None, 1e6),
        ])
        arm_b = _arm_result("B", "v", "v", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.02, 100.0, 1e6),
        ])
        nt = negative_transfer_diagnostic(arm_a, arm_b, shared)
        assert nt["arm_a_shared_pnl"] is None

    def test_verdict_and_edges_unaffected_by_pnl_availability(self):
        """Locks in: `diluted` and the edge values derive only from
        raw_return, never from pnl -- a later refactor must not couple them."""
        from gefion.backtest.ab_compare import negative_transfer_diagnostic

        shared = {"AAA"}
        arm_a = _arm_result("A", "u", "u", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.10, None, 1e6),
        ])
        arm_b = _arm_result("B", "v", "v", [
            _pos(date(2020, 1, 1), "AAA", "long", 0.03, 999999.0, 1e6),
        ])
        nt = negative_transfer_diagnostic(arm_a, arm_b, shared)
        assert nt["diluted"] is True
        assert nt["arm_a_edge"] == pytest.approx(0.10)
        assert nt["arm_b_edge"] == pytest.approx(0.03)


class TestPositionsLedgerPnl:
    def test_opening_trades_never_get_a_fabricated_pnl(self):
        """Root cause of the bug: `_build_positions_ledger` only keeps
        opening trades (buy/short), but BacktestEngine only stamps `pnl` on
        CLOSING trades (sell/cover) -- so an opening trade's `t.get("pnl",
        0.0)` was always the silent default, never a measurement."""
        from gefion.backtest.ab_compare import _build_positions_ledger

        trades = [
            {"date": date(2020, 1, 1), "action": "buy", "symbol": "AAA",
             "shares": 10, "price": 100.0},
        ]
        price_data = [
            {"symbol": "AAA", "date": date(2020, 1, 1), "close": 100.0,
             "volume": 1000},
            {"symbol": "AAA", "date": date(2020, 1, 8), "close": 110.0,
             "volume": 1000},
        ]
        ledger = _build_positions_ledger(trades, price_data, horizon_days=7)
        assert len(ledger) == 1
        assert ledger[0]["pnl"] is None


# --------------------------------------------------------------------------- #
# 4. Attribution Arm C wiring.
# --------------------------------------------------------------------------- #
class TestAttribution:
    def test_arm_c_trains_on_b_trades_on_a(self):
        from gefion.backtest.ab_compare import run_ab_compare

        seen = {}

        def spy_runner(spec, config, conn):
            seen[spec.label] = spec
            return _arm_result(spec.label, spec.train_universe,
                               spec.trade_universe, [])

        report = run_ab_compare(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            attribution=True,
            arm_runner=spy_runner,
            universe_resolver=lambda conn, name: {"AAA"},
        )

        assert set(seen) == {"A", "B", "C"}
        # Arm C isolates the DATA effect: trained on the wide universe but
        # traded on the SAME opportunity set as Arm A.
        assert seen["C"].train_universe == "nasdaq-plus-nyse"
        assert seen["C"].trade_universe == "nasdaq-only"
        assert "C" in report["arms"]

    def test_no_arm_c_by_default(self):
        from gefion.backtest.ab_compare import run_ab_compare

        seen = {}

        def spy_runner(spec, config, conn):
            seen[spec.label] = spec
            return _arm_result(spec.label, spec.train_universe,
                               spec.trade_universe, [])

        run_ab_compare(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            arm_runner=spy_runner,
            universe_resolver=lambda conn, name: {"AAA"},
        )
        assert set(seen) == {"A", "B"}


# --------------------------------------------------------------------------- #
# 5. Report shape — JSON dict + human-readable table.
# --------------------------------------------------------------------------- #
class TestReportShape:
    def _run(self, attribution=False):
        from gefion.backtest.ab_compare import run_ab_compare

        def runner(spec, config, conn):
            # Arm B slightly worse on the shared name AAA than Arm A.
            edge = 0.10 if spec.label == "A" else 0.03
            positions = [
                _pos(date(2020, 1, 1), "AAA", "long", edge, 500.0, 2e6),
                _pos(date(2020, 1, 1), "ZZZ", "short", -0.05, 300.0, 5e6),
            ]
            tr = 0.20 if spec.label == "A" else 0.30
            return _arm_result(spec.label, spec.train_universe,
                               spec.trade_universe, positions, total_return=tr)

        return run_ab_compare(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            attribution=attribution,
            arm_runner=runner,
            universe_resolver=lambda conn, name: {"AAA"},
        )

    def test_report_json_keys(self):
        report = self._run()
        for key in ("config", "arms", "deltas", "negative_transfer", "note"):
            assert key in report
        assert "A" in report["arms"] and "B" in report["arms"]
        assert "A_to_B" in report["deltas"]
        assert "diluted" in report["negative_transfer"]

    def test_report_is_json_serializable(self):
        import json

        report = self._run(attribution=True)
        # Must round-trip cleanly (dates stringified, etc.).
        json.loads(json.dumps(report))

    def test_config_echoed_in_report(self):
        report = self._run()
        cfg = report["config"]
        assert cfg["horizon_days"] == 7
        assert cfg["start_date"] == "2020-01-01"
        assert cfg["end_date"] == "2020-12-31"

    def test_human_table_contains_arms_and_verdict(self):
        from gefion.backtest.ab_compare import format_ab_report

        report = self._run()
        text = format_ab_report(report)
        assert "nasdaq-only" in text
        assert "nasdaq-plus-nyse" in text
        # Metric labels present.
        assert "Sharpe" in text or "sharpe" in text
        # The negative-transfer verdict surfaces to the human reader.
        assert "transfer" in text.lower() or "dilut" in text.lower()

    def test_report_does_not_auto_decide(self):
        """Owner-gate philosophy: the harness REPORTS, it does not pick a winner."""
        report = self._run()
        note = report["note"].lower()
        assert "human" in note or "not" in note

    def test_blown_flag_surfaces_per_arm_in_report_and_human_table(self):
        from gefion.backtest.ab_compare import build_ab_report, format_ab_report

        arm_a = _arm_result("A", "nasdaq-only", "nasdaq-only", [])
        arm_b = _arm_result("B", "nasdaq-plus-nyse", "nasdaq-plus-nyse", [],
                            total_return=-1.0, blown=True,
                            blown_date=date(2020, 6, 1))
        nt = {"arm_a_edge": 0.0, "arm_b_edge": 0.0, "delta": 0.0,
              "diluted": False, "n_shared_b": 0, "verdict": "no dilution"}
        report = build_ab_report({"A": arm_a, "B": arm_b}, _config(), nt)

        assert report["arms"]["A"]["blown"] is False
        assert report["arms"]["B"]["blown"] is True
        assert report["arms"]["B"]["blown_date"] == "2020-06-01"

        text = format_ab_report(report)
        assert "2020-06-01" in text

    def test_checkpoint_provenance_surfaces_in_human_table(self):
        from gefion.backtest.ab_compare import build_ab_report, format_ab_report

        arm_a = _arm_result("A", "nasdaq-only", "nasdaq-only", [])
        arm_b = _arm_result("B", "nasdaq-plus-nyse", "nasdaq-plus-nyse", [])
        nt = {"arm_a_edge": 0.0, "arm_b_edge": 0.0, "delta": 0.0,
              "diluted": False, "n_shared_b": 0, "verdict": "no dilution"}
        report = build_ab_report(
            {"A": arm_a, "B": arm_b}, _config(), nt,
            checkpoint_provenance={
                "A": {"reused": True, "key": "abc123"},
                "B": {"reused": False, "key": "def456"},
            },
        )
        assert report["checkpoint_provenance"]["A"]["reused"] is True
        text = format_ab_report(report)
        assert "reused" in text.lower()
        assert "abc123" in text


# --------------------------------------------------------------------------- #
# 6. Per-arm checkpoint + resume (issue #234).
# --------------------------------------------------------------------------- #
def _variant_config(base, **overrides):
    """Build a MatchedConfig equal to ``base`` except for the given fields."""
    from gefion.backtest.ab_compare import MatchedConfig

    data = dict(
        start_date=base.start_date,
        end_date=base.end_date,
        split_spec=base.split_spec,
        horizons=base.horizons,
        horizon_days=base.horizon_days,
        hyperparams=base.hyperparams,
        strategy_params=base.strategy_params,
        initial_capital=base.initial_capital,
        weak_thresholds=base.weak_thresholds,
        strong_thresholds=base.strong_thresholds,
    )
    data.update(overrides)
    return MatchedConfig(**data)


# One representative change per MatchedConfig field that affects an arm's
# result. Exhaustive over the fields, per the brief: this is the test that
# protects the A/B verdict from a stale reuse.
_CONFIG_FIELD_VARIANTS = [
    ("start_date", date(2020, 1, 2)),
    ("end_date", date(2020, 12, 30)),
    ("split_spec", {"scheme": "walk_forward", "folds": 5, "oos": True}),
    ("horizons", [7, 60]),
    ("horizon_days", 14),
    ("hyperparams", {"max_depth": 6, "n_estimators": 500}),
    ("strategy_params", {"decile": 0.1, "mode": "long_short", "selection": "rank"}),
    ("initial_capital", 250_000.0),
    ("weak_thresholds", [0.03, 0.03]),
    ("strong_thresholds", [0.07, 0.07]),
]


def _spy_runner(calls):
    def runner(spec, config, conn):
        calls.append(spec.label)
        return _arm_result(spec.label, spec.train_universe, spec.trade_universe, [
            _pos(date(2020, 1, 1), "AAA", "long", 0.05, 500.0, 1_000_000.0),
        ])
    return runner


class TestCheckpointKey:
    def test_stable_for_identical_inputs(self):
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key

        spec = ArmSpec("A", train_universe="nasdaq-only")
        cfg = _config()
        assert (compute_arm_key(spec, cfg, ["AAA"], ["AAA"])
                == compute_arm_key(spec, cfg, ["AAA"], ["AAA"]))

    def test_changes_with_universe(self):
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key

        cfg = _config()
        key1 = compute_arm_key(
            ArmSpec("A", train_universe="nasdaq-only"), cfg, ["AAA"], ["AAA"])
        key2 = compute_arm_key(
            ArmSpec("A", train_universe="nyse-only"), cfg, ["AAA"], ["AAA"])
        assert key1 != key2

    def test_changes_with_label(self):
        """Label is part of the key (brief: 'key each arm by ... arm label')."""
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key

        cfg = _config()
        key_a = compute_arm_key(ArmSpec("A", train_universe="u"), cfg, ["AAA"], ["AAA"])
        key_c = compute_arm_key(ArmSpec("C", train_universe="u"), cfg, ["AAA"], ["AAA"])
        assert key_a != key_c

    @pytest.mark.parametrize("field_name,new_value", _CONFIG_FIELD_VARIANTS)
    def test_changes_with_every_matched_config_field(self, field_name, new_value):
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key

        spec = ArmSpec("A", train_universe="nasdaq-only")
        base_cfg = _config()
        changed_cfg = _variant_config(base_cfg, **{field_name: new_value})
        assert (compute_arm_key(spec, base_cfg, ["AAA"], ["AAA"])
                != compute_arm_key(spec, changed_cfg, ["AAA"], ["AAA"]))

    def test_changes_with_universe_membership_even_when_name_is_unchanged(self):
        """Universe membership is resolved point-in-time (``universe_members``
        defaults ``as_of`` to today and joins effective-dated exclusions), so
        the member SET under a fixed name can drift day to day. The key must
        pin the resolved membership, not just the name — otherwise a
        checkpoint from before a data-quality exclusion landed would be
        silently reused after it lands (review finding #234)."""
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key

        spec = ArmSpec("A", train_universe="nasdaq-only")
        cfg = _config()
        key1 = compute_arm_key(spec, cfg, ["AAA", "BBB"], ["AAA", "BBB"])
        key2 = compute_arm_key(spec, cfg, ["AAA"], ["AAA"])
        assert key1 != key2

    def test_key_independent_of_member_set_order(self):
        """Membership is a set, not a sequence — resolver iteration order
        must not perturb the key (that would make reuse flaky, not just
        conservative)."""
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key

        spec = ArmSpec("A", train_universe="nasdaq-only")
        cfg = _config()
        key1 = compute_arm_key(spec, cfg, ["AAA", "BBB"], ["AAA"])
        key2 = compute_arm_key(spec, cfg, ["BBB", "AAA"], ["AAA"])
        assert key1 == key2


class TestCheckpointResume:
    def _kwargs(self, tmp_path, calls, **extra):
        return dict(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            arm_runner=_spy_runner(calls),
            universe_resolver=lambda conn, name: {"AAA"},
            checkpoint_dir=tmp_path,
            **extra,
        )

    def test_identical_rerun_reuses_every_arm_and_report_is_unchanged(self, tmp_path):
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        report1 = run_ab_compare(**self._kwargs(tmp_path, calls))
        assert calls == ["A", "B"]

        calls.clear()
        report2 = run_ab_compare(**self._kwargs(tmp_path, calls))
        assert calls == []
        assert report1["arms"] == report2["arms"]
        assert report2["checkpoint_provenance"]["A"]["reused"] is True
        assert report2["checkpoint_provenance"]["B"]["reused"] is True

    def test_first_run_is_not_reused(self, tmp_path):
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        report = run_ab_compare(**self._kwargs(tmp_path, calls))
        assert report["checkpoint_provenance"]["A"]["reused"] is False
        assert report["checkpoint_provenance"]["B"]["reused"] is False

    def test_blown_flag_and_date_survive_a_checkpoint_round_trip(self, tmp_path):
        """A blown arm reused from checkpoint (#234) must still report
        blown=True with its original date — not silently lost on reuse."""
        from gefion.backtest.ab_compare import run_ab_compare

        def blown_runner(spec, config, conn):
            return _arm_result(spec.label, spec.train_universe,
                               spec.trade_universe, [], total_return=-1.0,
                               blown=(spec.label == "B"),
                               blown_date=date(2020, 4, 9)
                               if spec.label == "B" else None)

        kwargs = dict(self._kwargs(tmp_path, []), arm_runner=blown_runner)
        report1 = run_ab_compare(**kwargs)
        assert report1["arms"]["B"]["blown"] is True
        assert report1["arms"]["B"]["blown_date"] == "2020-04-09"

        calls = []
        kwargs = dict(self._kwargs(tmp_path, calls), arm_runner=blown_runner)
        report2 = run_ab_compare(**kwargs)
        assert calls == []  # both reused from checkpoint
        assert report2["arms"]["B"]["blown"] is True
        assert report2["arms"]["B"]["blown_date"] == "2020-04-09"
        assert report2["arms"]["A"]["blown"] is False
        assert report2["arms"]["A"]["blown_date"] is None

    @pytest.mark.parametrize("field_name,new_value", _CONFIG_FIELD_VARIANTS)
    def test_changed_config_field_forces_rebuild_of_both_arms(
            self, tmp_path, field_name, new_value):
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        base_kwargs = self._kwargs(tmp_path, calls)
        run_ab_compare(**base_kwargs)
        calls.clear()

        changed_cfg = _variant_config(base_kwargs["config"], **{field_name: new_value})
        changed_kwargs = dict(base_kwargs, config=changed_cfg)
        run_ab_compare(**changed_kwargs)
        # The shared config changed, so BOTH arms must rebuild — reusing
        # either would risk exactly the stale-verdict hazard the brief warns
        # about.
        assert calls == ["A", "B"]

    def test_changed_universe_rebuilds_only_that_arm(self, tmp_path):
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        run_ab_compare(**self._kwargs(tmp_path, calls))
        calls.clear()
        kwargs = self._kwargs(tmp_path, calls)
        kwargs["arm_b_universe"] = "nyse-only"
        run_ab_compare(**kwargs)
        # Arm A's key is unchanged (same universe + config) -> reused.
        # Arm B's universe changed -> rebuilt.
        assert calls == ["B"]

    def test_partial_resume_after_mid_run_failure(self, tmp_path):
        """The actual NYSE-retry scenario: arm A checkpoints, arm B raises;
        on re-run arm A is reused and only arm B recomputes."""
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        attempts = {"B": 0}

        def flaky_runner(spec, config, conn):
            calls.append(spec.label)
            if spec.label == "B":
                attempts["B"] += 1
                if attempts["B"] == 1:
                    raise RuntimeError("arm B blew up (simulated window failure)")
            return _arm_result(spec.label, spec.train_universe, spec.trade_universe, [])

        kwargs = dict(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            arm_runner=flaky_runner,
            universe_resolver=lambda conn, name: {"AAA"},
            checkpoint_dir=tmp_path,
        )
        with pytest.raises(RuntimeError):
            run_ab_compare(**kwargs)
        assert calls == ["A", "B"]

        calls.clear()
        report = run_ab_compare(**kwargs)
        assert calls == ["B"]
        assert report["checkpoint_provenance"]["A"]["reused"] is True
        assert report["checkpoint_provenance"]["B"]["reused"] is False

    def test_no_resume_forces_rebuild_of_every_arm(self, tmp_path):
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        run_ab_compare(**self._kwargs(tmp_path, calls))
        calls.clear()

        run_ab_compare(**self._kwargs(tmp_path, calls, resume=False))
        assert calls == ["A", "B"]

    def test_no_resume_still_refreshes_the_checkpoint(self, tmp_path):
        """A --no-resume run must overwrite the checkpoint, so a later
        resumed run picks up the fresh result, not the stale one."""
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        run_ab_compare(**self._kwargs(tmp_path, calls))
        calls.clear()
        run_ab_compare(**self._kwargs(tmp_path, calls, resume=False))
        assert calls == ["A", "B"]

        calls.clear()
        run_ab_compare(**self._kwargs(tmp_path, calls))
        assert calls == []  # the refreshed checkpoint is reused

    def test_universe_membership_drift_forces_rebuild_under_unchanged_name(
            self, tmp_path):
        """The actual review scenario: a data-quality exclusion lands for a
        symbol between runs, changing arm A's universe membership while its
        NAME ('nasdaq-only') stays the same. A stale checkpoint keyed only on
        the name would be silently reused with day-1 membership, corrupting
        the verdict. It must instead be treated as a miss and rebuilt."""
        from gefion.backtest.ab_compare import run_ab_compare

        calls = []
        membership = {
            "nasdaq-only": {"AAA", "BBB"},
            "nasdaq-plus-nyse": {"AAA", "BBB", "CCC"},
        }

        def resolver(conn, name):
            return set(membership[name])

        kwargs = dict(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            arm_runner=_spy_runner(calls),
            universe_resolver=resolver,
            checkpoint_dir=tmp_path,
        )
        run_ab_compare(**kwargs)
        assert calls == ["A", "B"]

        # A data-quality exclusion lands for "nasdaq-only" — same universe
        # name, different membership. "nasdaq-plus-nyse" is untouched.
        calls.clear()
        membership["nasdaq-only"] = {"AAA"}
        report = run_ab_compare(**kwargs)
        assert calls == ["A"]  # membership drift forces A to rebuild
        assert report["checkpoint_provenance"]["A"]["reused"] is False
        assert report["checkpoint_provenance"]["B"]["reused"] is True


class TestCheckpointDir:
    def test_default_checkpoint_dir_honors_env_override(self, monkeypatch, tmp_path):
        from gefion.backtest.ab_compare import default_checkpoint_dir

        override = tmp_path / "custom-checkpoints"
        monkeypatch.setenv("GEFION_AB_CHECKPOINT_DIR", str(override))
        assert default_checkpoint_dir() == override

    def test_default_checkpoint_dir_falls_back_to_gefion_home(self, monkeypatch):
        from pathlib import Path

        from gefion.backtest.ab_compare import default_checkpoint_dir

        monkeypatch.delenv("GEFION_AB_CHECKPOINT_DIR", raising=False)
        assert default_checkpoint_dir() == (
            Path.home() / ".gefion" / "ab_compare_checkpoints")

    def test_corrupt_checkpoint_file_is_treated_as_a_miss(self, tmp_path):
        """Conservative-by-construction: a checkpoint that fails to parse
        must trigger a rebuild, never a crash or a guess."""
        from gefion.backtest.ab_compare import ArmSpec, compute_arm_key, run_ab_compare

        key = compute_arm_key(
            ArmSpec("A", train_universe="nasdaq-only"), _config(), ["AAA"], ["AAA"])
        (tmp_path / f"{key}.json").write_text("not valid json{{{")

        calls = []
        run_ab_compare(
            arm_a_universe="nasdaq-only",
            arm_b_universe="nasdaq-plus-nyse",
            config=_config(),
            arm_runner=_spy_runner(calls),
            universe_resolver=lambda conn, name: {"AAA"},
            checkpoint_dir=tmp_path,
        )
        assert calls == ["A", "B"]
