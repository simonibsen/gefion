"""TDD tests for #236: let the A/B harness exercise MLSignalStrategy's
``selection="rank"`` mode.

#237 (merged) added a ``selection`` parameter to ``MLSignalStrategy``. The
harness could not reach it: ``run_arm`` filters ``strategy_params`` through a
hard-coded allowlist that never included ``selection``, and the CLI never
offered a ``--selection`` flag. These tests cover both gaps, plus the
allowlist's silent-drop behavior turning into a loud warning.
"""
import logging
from datetime import date

import pytest
from typer.testing import CliRunner

from gefion.backtest import ab_compare
from gefion.cli import app

runner = CliRunner()


class _FakeEngine:
    def __init__(self, **kwargs):
        pass

    def run(self):
        return {"metrics": {}, "equity_curve": [], "trades": []}


class _CapturingStrat:
    """Records the kwargs MLSignalStrategy was constructed with."""

    captured_kwargs = None

    def __init__(self, **kwargs):
        _CapturingStrat.captured_kwargs = kwargs

    def generate_signals(self, *args, **kwargs):
        return []


@pytest.fixture
def captured_strategy(monkeypatch):
    _CapturingStrat.captured_kwargs = None
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: None)
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _FakeEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy",
                        _CapturingStrat)
    return _CapturingStrat


def _config(strategy_params):
    return ab_compare.MatchedConfig(
        start_date=date(2020, 1, 1), end_date=date(2020, 6, 30),
        split_spec={}, horizons=[7], horizon_days=7,
        hyperparams={"algorithm": "xgboost"},
        strategy_params=strategy_params)


class TestRunArmSelectionPlumbing:
    def test_selection_reaches_strategy_construction(self, captured_strategy):
        """Fails today: run_arm's hard-coded allowlist drops `selection`
        before MLSignalStrategy ever sees it."""
        ab_compare.run_arm(
            ab_compare.ArmSpec("A", train_universe="u"),
            _config({"return_threshold": 0.02, "selection": "rank"}),
            conn=None)
        assert captured_strategy.captured_kwargs["selection"] == "rank"

    def test_default_absolute_selection_reaches_strategy(self, captured_strategy):
        ab_compare.run_arm(
            ab_compare.ArmSpec("A", train_universe="u"),
            _config({"return_threshold": 0.02, "selection": "absolute"}),
            conn=None)
        assert captured_strategy.captured_kwargs["selection"] == "absolute"

    def test_previously_allowed_params_still_pass_through(self, captured_strategy):
        ab_compare.run_arm(
            ab_compare.ArmSpec("A", train_universe="u"),
            _config({"return_threshold": 0.03, "max_positions": 15,
                     "selection": "rank"}),
            conn=None)
        kwargs = captured_strategy.captured_kwargs
        assert kwargs["return_threshold"] == 0.03
        assert kwargs["max_positions"] == 15

    def test_unknown_strategy_param_is_dropped_and_warns(
            self, captured_strategy, caplog):
        with caplog.at_level(logging.WARNING,
                              logger="gefion.backtest.ab_compare"):
            ab_compare.run_arm(
                ab_compare.ArmSpec("A", train_universe="u"),
                _config({"return_threshold": 0.02, "bogus_param": "x"}),
                conn=None)
        kwargs = captured_strategy.captured_kwargs
        # Unchanged behavior: the unknown key is still dropped.
        assert "bogus_param" not in kwargs
        # New behavior: dropping it is now loud.
        assert any("bogus_param" in rec.message for rec in caplog.records)


class TestMatchedConfigSelectionProvenance:
    def test_to_dict_echoes_selection(self):
        cfg = ab_compare.MatchedConfig(
            start_date=date(2020, 1, 1), end_date=date(2020, 12, 31),
            split_spec={}, horizons=[7], horizon_days=7, hyperparams={},
            strategy_params={"selection": "rank", "return_threshold": 0.02})
        assert cfg.to_dict()["strategy_params"]["selection"] == "rank"


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Same stub as test_ab_compare_cli.py: replace the heavy orchestrator."""
    captured = {}

    def fake_run(arm_a_universe, arm_b_universe, config, conn=None,
                 attribution=False, **kwargs):
        captured["config"] = config
        return {
            "config": config.to_dict(),
            "arms": {}, "deltas": {}, "negative_transfer": {},
            "attribution": attribution, "note": "human reads it",
        }

    monkeypatch.setattr("gefion.backtest.ab_compare.run_ab_compare", fake_run)

    import contextlib

    @contextlib.contextmanager
    def fake_db(url=None):
        yield object()

    monkeypatch.setattr("gefion.cli_helpers.db_connection", fake_db)
    return captured


class TestAbCompareCliSelectionFlag:
    def _invoke(self, *extra_args):
        return runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
            *extra_args,
        ])

    def test_omitting_selection_matches_prior_default_params(self, stub_pipeline):
        result = self._invoke("--json")
        assert result.exit_code == 0, result.output
        cfg = stub_pipeline["config"]
        # Byte-identical to today's defaults, plus the new (opt-in-default)
        # selection key set to the strategy's own default.
        assert cfg.strategy_params == {
            "return_threshold": 0.02,
            "downside_limit": -0.05,
            "rebalance_days": 20,
            "max_positions": 20,
            "selection": "absolute",
        }

    def test_selection_rank_lands_in_strategy_params(self, stub_pipeline):
        result = self._invoke("--selection", "rank", "--json")
        assert result.exit_code == 0, result.output
        cfg = stub_pipeline["config"]
        assert cfg.strategy_params["selection"] == "rank"

    def test_invalid_selection_rejected_with_clear_message(self, stub_pipeline):
        result = self._invoke("--selection", "bogus")
        assert result.exit_code != 0
        assert "selection" in result.output.lower()
        assert "bogus" in result.output.lower()

    def test_selection_visible_in_report_provenance(self, stub_pipeline):
        result = self._invoke("--selection", "rank", "--json")
        assert result.exit_code == 0, result.output
        assert '"selection": "rank"' in result.output or \
            '"selection":"rank"' in result.output.replace(" ", "")
