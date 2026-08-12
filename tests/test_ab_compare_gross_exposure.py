"""TDD tests for making `max_gross_exposure` settable on `backtest ab-compare`.

A 6-month A/B smoke (2026-08-12) blew up both arms 5-7 weeks in: rank
selection under a model whose q50 median is negative yields a heavily
one-sided book (19-20 shorts vs 5-6 longs), and #211 pins
`max_gross_exposure` at 2.0 for `long_short` with no way to test a lower
value without editing code. This is plumbing only -- it must not change any
default.

Mirrors the shape of tests/test_ab_compare_selection.py (#236), but the
target is `BacktestEngine`'s constructor kwargs, not `MLSignalStrategy`'s --
`max_gross_exposure` is an engine-level control, not a strategy_params key.
"""
from datetime import date

import pytest
from typer.testing import CliRunner

from gefion.backtest import ab_compare
from gefion.cli import app

runner = CliRunner()


class _CapturingEngine:
    """Records the kwargs BacktestEngine was constructed with."""

    captured_kwargs = None

    def __init__(self, **kwargs):
        _CapturingEngine.captured_kwargs = kwargs

    def run(self):
        return {"metrics": {}, "equity_curve": [], "trades": []}


class _NoopStrat:
    def __init__(self, **kwargs):
        pass

    def generate_signals(self, *args, **kwargs):
        return []


@pytest.fixture
def captured_engine(monkeypatch):
    _CapturingEngine.captured_kwargs = None
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: None)
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _CapturingEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy", _NoopStrat)
    return _CapturingEngine


def _config(max_gross_exposure=None):
    return ab_compare.MatchedConfig(
        start_date=date(2020, 1, 1), end_date=date(2020, 6, 30),
        split_spec={}, horizons=[7], horizon_days=7,
        hyperparams={"algorithm": "xgboost"},
        strategy_params={"return_threshold": 0.02},
        max_gross_exposure=max_gross_exposure)


class TestRunArmGrossExposurePlumbing:
    def test_omitted_gross_exposure_omits_engine_kwarg(self, captured_engine):
        """Guard against a silent change: unset must mean BacktestEngine
        never even sees the kwarg, so #211's mode-dependent default applies
        exactly as it does today."""
        ab_compare.run_arm(
            ab_compare.ArmSpec("A", train_universe="u"),
            _config(),
            conn=None)
        assert "max_gross_exposure" not in captured_engine.captured_kwargs

    def test_gross_exposure_reaches_engine(self, captured_engine):
        ab_compare.run_arm(
            ab_compare.ArmSpec("A", train_universe="u"),
            _config(max_gross_exposure=1.0),
            conn=None)
        assert captured_engine.captured_kwargs["max_gross_exposure"] == 1.0

    def test_gross_exposure_identical_across_both_arms(self, captured_engine):
        cfg = _config(max_gross_exposure=1.5)
        for label in ("A", "B"):
            ab_compare.run_arm(
                ab_compare.ArmSpec(label, train_universe="u"), cfg, conn=None)
            assert captured_engine.captured_kwargs["max_gross_exposure"] == 1.5


class TestMatchedConfigGrossExposureProvenance:
    def test_to_dict_echoes_gross_exposure(self):
        cfg = _config(max_gross_exposure=1.0)
        assert cfg.to_dict()["max_gross_exposure"] == 1.0

    def test_to_dict_echoes_unset_gross_exposure_as_none(self):
        cfg = _config()
        assert cfg.to_dict()["max_gross_exposure"] is None


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


class TestAbCompareCliGrossExposureFlag:
    def _invoke(self, *extra_args):
        return runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
            *extra_args,
        ])

    def test_omitting_flag_leaves_gross_exposure_unset(self, stub_pipeline):
        result = self._invoke("--json")
        assert result.exit_code == 0, result.output
        assert stub_pipeline["config"].max_gross_exposure is None

    def test_flag_lands_in_matched_config(self, stub_pipeline):
        result = self._invoke("--gross-exposure", "1.0", "--json")
        assert result.exit_code == 0, result.output
        assert stub_pipeline["config"].max_gross_exposure == 1.0

    def test_invalid_zero_rejected_with_clear_message(self, stub_pipeline):
        result = self._invoke("--gross-exposure", "0")
        assert result.exit_code != 0
        assert "gross-exposure" in result.output.lower()

    def test_invalid_negative_rejected_with_clear_message(self, stub_pipeline):
        result = self._invoke("--gross-exposure", "-1.0")
        assert result.exit_code != 0
        assert "gross-exposure" in result.output.lower()

    def test_visible_in_report_provenance(self, stub_pipeline):
        result = self._invoke("--gross-exposure", "1.0", "--json")
        assert result.exit_code == 0, result.output
        assert '"max_gross_exposure": 1.0' in result.output or \
            '"max_gross_exposure":1.0' in result.output.replace(" ", "")
