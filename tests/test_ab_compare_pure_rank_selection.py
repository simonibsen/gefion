"""CLI/harness plumbing for MLSignalStrategy `selection="pure_rank"`.

Mirrors test_ab_compare_selection.py's coverage of `--selection rank` (#236),
extended to the new `pure_rank` mode: the allowlist already forwards any
`selection` value (#236), so this only needs to confirm the CLI accepts
`pure_rank` as a valid choice and it reaches the strategy construction.
"""
from datetime import date

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


def test_pure_rank_selection_reaches_strategy_construction(monkeypatch):
    _CapturingStrat.captured_kwargs = None
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: None)
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _FakeEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy",
                        _CapturingStrat)

    config = ab_compare.MatchedConfig(
        start_date=date(2020, 1, 1), end_date=date(2020, 6, 30),
        split_spec={}, horizons=[7], horizon_days=7,
        hyperparams={"algorithm": "xgboost"},
        strategy_params={"return_threshold": 0.02, "selection": "pure_rank"})
    ab_compare.run_arm(
        ab_compare.ArmSpec("A", train_universe="u"), config, conn=None)
    assert _CapturingStrat.captured_kwargs["selection"] == "pure_rank"


class TestAbCompareCliPureRankFlag:
    def _invoke(self, *extra_args):
        return runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
            *extra_args,
        ])

    def test_selection_pure_rank_lands_in_strategy_params(self, monkeypatch):
        captured = {}

        def fake_run(arm_a_universe, arm_b_universe, config, conn=None,
                     attribution=False, **kwargs):
            captured["config"] = config
            return {
                "config": config.to_dict(),
                "arms": {}, "deltas": {}, "negative_transfer": {},
                "attribution": attribution, "note": "human reads it",
            }

        monkeypatch.setattr(
            "gefion.backtest.ab_compare.run_ab_compare", fake_run)

        import contextlib

        @contextlib.contextmanager
        def fake_db(url=None):
            yield object()

        monkeypatch.setattr("gefion.cli_helpers.db_connection", fake_db)

        result = self._invoke("--selection", "pure_rank", "--json")
        assert result.exit_code == 0, result.output
        assert captured["config"].strategy_params["selection"] == "pure_rank"

    def test_invalid_selection_message_lists_pure_rank(self):
        result = self._invoke("--selection", "bogus")
        assert result.exit_code != 0
        assert "pure_rank" in result.output.lower()
