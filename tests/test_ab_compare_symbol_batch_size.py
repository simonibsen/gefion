"""The A/B must be able to bound dataset-build memory (#205, #209).

The 6-year full run OOM-killed on sloth during arm A's dataset build:

    Out of memory: Killed process (python)
      total-vm: 19,655,332 kB   anon-rss: 13,974,632 kB

14.0 GB resident on a 15 GB box -- the same ceiling recorded in #209. The
streaming build (#238) chunks by SYMBOL, and `ml dataset-build` exposes
`--symbol-batch-size` to tune that chunk, but `run_arm` never passed it, so
every A/B built at DEFAULT_SYMBOL_BATCH_SIZE (200) no matter how long the
window. Peak memory scales with batch_size x window, so a 6-year window needs
a smaller batch than a 6-month one.

Carrying it on MatchedConfig keeps it MATCHED across arms: a batch size that
differed per arm would change each arm's export path and confound the
comparison, the same reason max_gross_exposure lives there (#211).

It is a capacity knob, NOT a modelling one -- batching changes peak memory,
never the resulting dataset -- so leaving it None must reproduce today's
behavior byte-for-byte.
"""
from datetime import date

from gefion.backtest import ab_compare


class _FakeEngine:
    def __init__(self, **kwargs):
        pass

    def run(self):
        return {"metrics": {}, "equity_curve": [], "trades": []}


class _Strat:
    def __init__(self, **kwargs):
        pass

    def generate_signals(self, *args, **kwargs):
        return []


def _capture(monkeypatch, **config_kwargs):
    """Run one arm, returning every CLI argv run_arm issued."""
    calls = []
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _FakeEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy", _Strat)

    base = dict(
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"},
        strategy_params={"return_threshold": 0.02})
    base.update(config_kwargs)
    config = ab_compare.MatchedConfig(**base)
    ab_compare.run_arm(ab_compare.ArmSpec("A", train_universe="u"),
                       config, conn=None)
    return calls


def _dataset_build_cmd(calls):
    for c in calls:
        if c[:2] == ["ml", "dataset-build"]:
            return c
    raise AssertionError(f"no dataset-build call in {calls}")


def test_symbol_batch_size_reaches_dataset_build(monkeypatch):
    cmd = _dataset_build_cmd(_capture(monkeypatch, symbol_batch_size=50))

    assert "--symbol-batch-size" in cmd
    assert cmd[cmd.index("--symbol-batch-size") + 1] == "50"


def test_omitted_batch_size_passes_no_flag(monkeypatch):
    """A capacity knob must not change behavior when unset -- the CLI default
    (200) has to keep applying exactly as it does today."""
    cmd = _dataset_build_cmd(_capture(monkeypatch))

    assert "--symbol-batch-size" not in cmd


def test_batch_size_is_echoed_in_the_matched_config(monkeypatch):
    """Provenance: a run's memory budget must be readable from its report,
    like every other matched control."""
    config = ab_compare.MatchedConfig(
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"},
        strategy_params={}, symbol_batch_size=50)

    assert config.to_dict()["symbol_batch_size"] == 50


def test_batch_size_defaults_to_none_in_the_echo(monkeypatch):
    config = ab_compare.MatchedConfig(
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"}, strategy_params={})

    assert config.to_dict()["symbol_batch_size"] is None


def test_batch_size_is_shared_not_per_arm(monkeypatch):
    """MatchedConfig is frozen and handed to every arm by reference, so a
    per-arm batch size is structurally impossible. Pin that."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ab_compare.MatchedConfig)}
    assert "symbol_batch_size" in fields
    assert not hasattr(ab_compare.ArmSpec("A", train_universe="u"),
                       "symbol_batch_size")


class TestAbCompareCliSymbolBatchSize:
    """The flag must be reachable from `backtest ab-compare`, not just the API."""

    def test_cli_exposes_the_flag(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["backtest", "ab-compare", "--help"])

        assert result.exit_code == 0
        assert "--symbol-batch-size" in result.output

    def test_cli_forwards_it_into_the_matched_config(self, monkeypatch):
        from typer.testing import CliRunner
        from gefion.cli import app
        from gefion.backtest import ab_compare as ac

        seen = {}

        def _fake_run(*args, **kwargs):
            seen["config"] = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            return {"status": "ok", "arms": {}, "deltas": {}}

        monkeypatch.setattr(ac, "run_ab_compare", _fake_run)
        CliRunner().invoke(app, [
            "backtest", "ab-compare", "--arm-a", "u1", "--arm-b", "u2",
            "--start-date", "2018-01-01", "--end-date", "2023-12-31",
            "--symbol-batch-size", "50", "--json"])

        # Assert the capture happened FIRST. A bare `if cfg is not None`
        # would let this test pass silently when run_ab_compare is never
        # reached -- a test that cannot fail is worse than no test.
        cfg = seen.get("config")
        assert cfg is not None, "run_ab_compare was never called"
        assert cfg.symbol_batch_size == 50
