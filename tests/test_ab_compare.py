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
                total_return=0.20, sharpe=1.5, max_dd=-0.10, n_days=252):
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
