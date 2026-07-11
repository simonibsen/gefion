"""Smart backup retention (tiered thinning; #76 reaping story).

TDD: written FIRST. Grandfather-father-son policy: keep everything recent
(dense restore points), thin older history to newest-per-month, keep
newest-per-year forever. Fail-safe rules: prune only after a SUCCESSFUL new
backup; the newest backup is always immune; directories without a readable
manifest are NEVER pruned (unknown is not deletable); everything pruned is
named in the result.
"""
import datetime as dt
import json
import os
from pathlib import Path

import pytest

D = dt.datetime
NOW = D(2026, 7, 11, 3, 0, 0)


def _mk(root: Path, stamp: str, created: D) -> Path:
    d = root / stamp
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"created_at": created.isoformat(), "tables": {}}))
    return d


# --- pure policy ----------------------------------------------------------------------

def test_recent_all_kept():
    from gefion.backup import select_survivors
    backups = [(NOW - dt.timedelta(days=i), f"b{i}") for i in range(0, 56, 7)]
    keep, prune = select_survivors(backups, now=NOW, keep_recent_days=56)
    assert prune == []                              # all within 56 days


def test_older_thinned_to_newest_per_month():
    from gefion.backup import select_survivors
    # two backups in each of March and April 2026 (older than 56 days)
    backups = [
        (D(2026, 3, 1), "mar1"), (D(2026, 3, 15), "mar15"),
        (D(2026, 4, 2), "apr2"), (D(2026, 4, 20), "apr20"),
        (NOW, "newest"),
    ]
    keep, prune = select_survivors(backups, now=NOW, keep_monthly=12)
    kept = {p for _, p in keep}
    assert kept == {"mar15", "apr20", "newest"}     # newest per month + newest
    assert {p for _, p in prune} == {"mar1", "apr2"}


def test_yearly_anchors_kept_forever():
    from gefion.backup import select_survivors
    backups = [
        (D(2023, 6, 1), "y23a"), (D(2023, 11, 1), "y23b"),   # >12mo old
        (D(2024, 5, 1), "y24"),
        (NOW, "newest"),
    ]
    keep, prune = select_survivors(backups, now=NOW)
    kept = {p for _, p in keep}
    assert "y23b" in kept and "y24" in kept          # newest per year survives
    assert {p for _, p in prune} == {"y23a"}


def test_newest_always_immune():
    from gefion.backup import select_survivors
    backups = [(D(2020, 1, 1), "ancient")]           # fails every window
    keep, prune = select_survivors(backups, now=NOW)
    assert [p for _, p in keep] == ["ancient"] and prune == []


# --- filesystem application -----------------------------------------------------------

def test_apply_retention_prunes_and_reports(tmp_path):
    from gefion.backup import apply_retention
    _mk(tmp_path, "old-mar1", D(2026, 3, 1))
    _mk(tmp_path, "old-mar15", D(2026, 3, 15))
    newest = _mk(tmp_path, "fresh", NOW)
    report = apply_retention(str(tmp_path), now=NOW, keep_monthly=12)
    assert (tmp_path / "old-mar15").exists()         # month anchor
    assert not (tmp_path / "old-mar1").exists()      # thinned
    assert newest.exists()
    assert [Path(p).name for p in report["pruned"]] == ["old-mar1"]
    assert report["kept"] >= 2


def test_unreadable_manifest_never_pruned(tmp_path):
    from gefion.backup import apply_retention
    mystery = tmp_path / "mystery"
    mystery.mkdir()
    (mystery / "data.parquet").write_bytes(b"x")     # no manifest at all
    _mk(tmp_path, "fresh", NOW)
    report = apply_retention(str(tmp_path), now=NOW)
    assert mystery.exists()                          # unknown != deletable
    assert str(mystery) in [str(p) for p in report["skipped_unreadable"]]


# --- reproducibility-aware types --------------------------------------------------------

def test_irreplaceable_type_covers_audit_ledgers():
    """The data that CANNOT be reproduced (declarations + audit ledgers) has
    its own tiny backup type; reproducible bulk (prices, features) does not
    pollute it."""
    from gefion.backup import DATA_TYPE_TABLES
    tables = set(DATA_TYPE_TABLES["irreplaceable"])
    for t in ("regime_definitions", "regime_discovery_runs", "regime_candidates",
              "regime_trust_grades", "discovery_diagnostics", "spa_reverdicts",
              "data_quality_findings", "feature_definitions", "feature_functions",
              "experiments", "experiment_trials", "macro_series",
              "macro_series_values"):
        assert t in tables, t
    for t in ("stock_ohlcv", "computed_features", "predictions"):
        assert t not in tables, f"{t} is reproducible bulk"


def test_regimes_type_exists_and_all_includes_it():
    from gefion.backup import DATA_TYPE_TABLES
    assert "regime_discovery_runs" in DATA_TYPE_TABLES["regimes"]
    assert "spa_reverdicts" in DATA_TYPE_TABLES["all"]      # gap closed


def test_sparse_defaults():
    """Owner decision 2026-07-11: bulk is reproducible — default retention is
    sparse (14d dense, 3 monthly, yearly forever)."""
    from gefion.backup import RETAIN_RECENT_DAYS, RETAIN_MONTHLY_MONTHS
    assert RETAIN_RECENT_DAYS == 14
    assert RETAIN_MONTHLY_MONTHS == 3


# --- CLI surface ------------------------------------------------------------------------

def test_cli_backup_has_retention_flags():
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, ["backup", "--help"])
    assert r.exit_code == 0
    for opt in ("--timestamped", "--keep-recent-days", "--keep-monthly",
                "--no-prune"):
        assert opt in r.output


def test_cli_timestamped_backup_applies_retention(tmp_path):
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.db import schema
    _mk(tmp_path, "prehistoric", D(2024, 2, 1))
    _mk(tmp_path, "prehistoric2", D(2024, 2, 2))     # same month: one survives
    r = CliRunner().invoke(app, [
        "backup", "-o", str(tmp_path), "--timestamped", "--data-types", "meta",
        "--json", "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["retention"]["kept"] >= 2
    assert len(payload["retention"]["pruned"]) == 1  # older Feb-2024 twin
    subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(subdirs) == 2                         # new stamped + anchor
