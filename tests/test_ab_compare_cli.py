"""TDD tests for the `backtest ab-compare` CLI command (issue #197).

The per-arm pipeline is stubbed via monkeypatching ``run_ab_compare`` so
these tests exercise only the CLI wiring: flag parsing → MatchedConfig
construction → matched arms A/B (+C) → report emission (JSON + human table).
No DB or train run required.
"""
import json
from datetime import date

import pytest
from typer.testing import CliRunner

from gefion.cli import app

runner = CliRunner()


def _canned_report():
    return {
        "config": {"start_date": "2020-01-01", "end_date": "2020-06-30",
                   "horizon_days": 7},
        "arms": {
            "A": {"label": "A", "train_universe": "nasdaq-only",
                  "trade_universe": "nasdaq-only", "total_return": 0.2,
                  "annualized_return": 0.4, "sharpe": 1.5, "max_drawdown": -0.1,
                  "position_breadth": 5.0, "tail_richness": 0.1,
                  "capacity_proxy": 1e6, "n_positions": 10, "n_long": 6,
                  "n_short": 4},
            "B": {"label": "B", "train_universe": "nasdaq-plus-nyse",
                  "trade_universe": "nasdaq-plus-nyse", "total_return": 0.25,
                  "annualized_return": 0.5, "sharpe": 1.6, "max_drawdown": -0.12,
                  "position_breadth": 8.0, "tail_richness": 0.12,
                  "capacity_proxy": 2e6, "n_positions": 16, "n_long": 9,
                  "n_short": 7},
        },
        "deltas": {"A_to_B": {"total_return": 0.05, "sharpe": 0.1}},
        "negative_transfer": {"arm_a_edge": 0.09, "arm_b_edge": 0.05,
                              "delta": -0.04, "diluted": True, "n_shared_b": 3,
                              "verdict": "NEGATIVE TRANSFER: diluted"},
        "attribution": False,
        "note": "human reads it",
    }


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Replace the heavy orchestrator with a capture stub."""
    captured = {}

    def fake_run(arm_a_universe, arm_b_universe, config, conn=None,
                 attribution=False, **kwargs):
        captured["arm_a"] = arm_a_universe
        captured["arm_b"] = arm_b_universe
        captured["config"] = config
        captured["attribution"] = attribution
        return _canned_report()

    monkeypatch.setattr("gefion.backtest.ab_compare.run_ab_compare", fake_run)

    # The CLI opens a DB connection before delegating to run_ab_compare; stub it
    # so these CLI-wiring tests need no live DB (the unit-test CI job has none).
    import contextlib

    @contextlib.contextmanager
    def fake_db(url=None):
        yield object()

    monkeypatch.setattr("gefion.cli_helpers.db_connection", fake_db)
    return captured


class TestAbCompareCli:
    def test_command_registered_under_backtest(self):
        result = runner.invoke(app, ["backtest", "ab-compare", "--help"])
        assert result.exit_code == 0
        assert "arm-a" in result.output
        assert "arm-b" in result.output
        assert "attribution" in result.output

    def test_requires_arms(self):
        result = runner.invoke(app, [
            "backtest", "ab-compare",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
        ])
        assert result.exit_code != 0

    def test_json_output_carries_report(self, stub_pipeline):
        result = runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
            "--horizon-days", "7",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        lines = [l for l in result.output.strip().splitlines()
                 if l.strip().startswith("{")]
        payload = json.loads(lines[-1])
        # The report is emitted (possibly nested under a data envelope).
        blob = json.dumps(payload)
        assert "negative_transfer" in blob
        assert "A_to_B" in blob

    def test_flags_map_into_matched_config(self, stub_pipeline):
        result = runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
            "--horizons", "7,30", "--horizon-days", "30",
            "--json",
        ])
        assert result.exit_code == 0, result.output
        cfg = stub_pipeline["config"]
        assert cfg.start_date == date(2020, 1, 1)
        assert cfg.end_date == date(2020, 6, 30)
        assert cfg.horizon_days == 30
        assert cfg.horizons == [7, 30]
        assert stub_pipeline["arm_a"] == "nasdaq-only"
        assert stub_pipeline["arm_b"] == "nasdaq-plus-nyse"

    def test_attribution_flag_threads_through(self, stub_pipeline):
        result = runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
            "--attribution", "--json",
        ])
        assert result.exit_code == 0, result.output
        assert stub_pipeline["attribution"] is True

    def test_human_output_renders_table(self, stub_pipeline):
        result = runner.invoke(app, [
            "backtest", "ab-compare",
            "--arm-a", "nasdaq-only", "--arm-b", "nasdaq-plus-nyse",
            "--start-date", "2020-01-01", "--end-date", "2020-06-30",
        ])
        assert result.exit_code == 0, result.output
        assert "nasdaq-only" in result.output
        assert "transfer" in result.output.lower() or "dilut" in result.output.lower()
