"""Chart-file reaping (#76 disk story).

TDD: written FIRST. Charts accumulate in the chart output dir with no
lifecycle; `gefion charts-clean` reaps files older than --keep-days,
dry-run by default (reports files + bytes, deletes nothing), --confirm
executes. Only chart HTML is in scope: exports are git-tracked backups by
design and dataset artifacts belong to their ml_datasets rows.
"""
import os
import time

import pytest
from typer.testing import CliRunner

from gefion.cli import app

runner = CliRunner()


@pytest.fixture
def chart_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("G2_CHART_DIR", str(tmp_path))
    old = tmp_path / "AAPL_price_20250101_000000.html"
    old.write_text("<html>old</html>")
    two_months_ago = time.time() - 60 * 86400
    os.utime(old, (two_months_ago, two_months_ago))
    fresh = tmp_path / "MSFT_price_fresh.html"
    fresh.write_text("<html>fresh</html>")
    other = tmp_path / "notes.txt"          # non-chart file: never touched
    other.write_text("keep me")
    os.utime(other, (two_months_ago, two_months_ago))
    return tmp_path


def test_dry_run_reports_and_deletes_nothing(chart_dir):
    r = runner.invoke(app, ["charts-clean", "--keep-days", "30", "--json"])
    assert r.exit_code == 0
    import json
    payload = json.loads(r.output)
    assert payload["dry_run"] is True
    assert payload["files"] == 1                    # only the old chart html
    assert payload["bytes"] > 0
    assert (chart_dir / "AAPL_price_20250101_000000.html").exists()


def test_confirm_reaps_old_charts_only(chart_dir):
    r = runner.invoke(app, ["charts-clean", "--keep-days", "30",
                            "--confirm", "--json"])
    assert r.exit_code == 0
    assert not (chart_dir / "AAPL_price_20250101_000000.html").exists()
    assert (chart_dir / "MSFT_price_fresh.html").exists()   # inside keep window
    assert (chart_dir / "notes.txt").exists()               # non-chart: untouched
