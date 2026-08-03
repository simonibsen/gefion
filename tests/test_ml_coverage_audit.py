"""Coverage-bias audit at ML dataset build (#191).

When a dataset is built, every feature's COVERAGE is audited — overall and by
cohort (exchange, sector) — and bias that would let a model learn a data
artifact (e.g. "missing PE ratio == is-NYSE") is flagged. The audit is
advisory and NON-BLOCKING: the build proceeds; flags are surfaced for human
review as system observations (#144) and recorded in dataset provenance.

TDD: these tests are written before the implementation.
"""
import os

import pytest

DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"

# Distinct prefix so DB fixtures clean up only their own rows.
PFX = "qcov191"
OBSERVER = "coverage_audit"


# --------------------------------------------------------------------------
# Unit tests (no database) — the pure report computation
# --------------------------------------------------------------------------
class TestPointBiserial:
    """Point-biserial = Pearson correlation between a binary missingness
    indicator and a numeric label. Undefined (None) when either side has no
    variance or there are fewer than two points."""

    def test_perfect_positive_correlation(self):
        from gefion.ml.coverage import point_biserial

        missing = [1, 1, 0, 0]
        label = [2.0, 2.0, -2.0, -2.0]
        r = point_biserial(missing, label)
        assert r is not None and r == pytest.approx(1.0)

    def test_no_variance_in_missingness_is_none(self):
        from gefion.ml.coverage import point_biserial

        # Fully present: missingness is constant → correlation undefined.
        assert point_biserial([0, 0, 0, 0], [1.0, -1.0, 2.0, 3.0]) is None

    def test_no_variance_in_label_is_none(self):
        from gefion.ml.coverage import point_biserial

        assert point_biserial([1, 0, 1, 0], [5.0, 5.0, 5.0, 5.0]) is None

    def test_too_few_points_is_none(self):
        from gefion.ml.coverage import point_biserial

        assert point_biserial([1], [1.0]) is None


class TestBiasedCohortCoverage:
    """(a) A feature present for one exchange and absent for another is
    flagged as cohort-biased, naming the low and high cohorts."""

    def _biased_inputs(self):
        # 40 NASDAQ symbols (feature present), 40 NYSE symbols (feature
        # absent). One date. Label independent of exchange.
        grid_labels = {}
        cohorts_exchange = {}
        present = set()
        for i in range(40):
            sym = f"NAS{i}"
            grid_labels[(sym, 1)] = 1.0 if i % 2 == 0 else -1.0
            cohorts_exchange[sym] = "NASDAQ"
            present.add((sym, 1))  # present for NASDAQ
        for i in range(40):
            sym = f"NYS{i}"
            grid_labels[(sym, 1)] = 1.0 if i % 2 == 0 else -1.0
            cohorts_exchange[sym] = "NYSE"
            # absent for NYSE — not added to present
        return grid_labels, cohorts_exchange, present

    def test_cohort_disparity_flagged_with_cohorts(self):
        from gefion.ml.coverage import compute_coverage_report

        grid_labels, cohorts_exchange, present = self._biased_inputs()
        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence={"daily_pe_ratio": present},
            cohorts={"exchange": cohorts_exchange, "sector": {}},
            cohort_eligible={"daily_pe_ratio": True},
        )
        feat = report["features"]["daily_pe_ratio"]
        assert feat["overall"] == pytest.approx(0.5, abs=0.05)
        cohort_flags = [f for f in feat["flags"] if f["kind"] == "cohort_bias"]
        assert cohort_flags, f"expected a cohort_bias flag, got {feat['flags']}"
        f = cohort_flags[0]
        assert f["cohort_kind"] == "exchange"
        assert f["low"] == "NYSE" and f["low_coverage"] == pytest.approx(0.0)
        assert f["high"] == "NASDAQ" and f["high_coverage"] == pytest.approx(1.0)
        assert f["disparity"] >= 0.30
        # Surfaced at the top level too.
        assert any(
            fl["feature"] == "daily_pe_ratio" and fl["kind"] == "cohort_bias"
            for fl in report["flagged"]
        )


class TestUniformCoverage:
    """(b) A feature covered uniformly across cohorts is NOT flagged, even at
    partial coverage — no cohort disparity, no label-correlated missingness."""

    def test_uniform_partial_coverage_not_flagged(self):
        from gefion.ml.coverage import compute_coverage_report

        grid_labels = {}
        cohorts_exchange = {}
        present = set()
        for i in range(40):
            for kind, tag in (("NAS", "NASDAQ"), ("NYS", "NYSE")):
                sym = f"{kind}{i}"
                cohorts_exchange[sym] = tag
                # Label balanced WITHIN both the present and absent groups, so
                # missingness is uncorrelated with the label (no MNAR).
                grid_labels[(sym, 1)] = 1.0 if i % 4 in (0, 1) else -1.0
                # Present for half of EACH cohort → 50% coverage everywhere.
                if i % 2 == 0:
                    present.add((sym, 1))

        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence={"indicator_rsi_14": present},
            cohorts={"exchange": cohorts_exchange, "sector": {}},
            cohort_eligible={"indicator_rsi_14": True},
        )
        feat = report["features"]["indicator_rsi_14"]
        assert feat["overall"] == pytest.approx(0.5, abs=0.05)
        assert feat["flags"] == []
        assert report["flagged"] == []

    def test_fully_present_feature_is_trivial_not_flagged(self):
        from gefion.ml.coverage import compute_coverage_report

        grid_labels = {(f"S{i}", 1): float(i % 3 - 1) for i in range(60)}
        cohorts_exchange = {f"S{i}": ("NASDAQ" if i < 30 else "NYSE")
                            for i in range(60)}
        present = set(grid_labels.keys())  # 100% coverage everywhere
        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence={"price_close": present},
            cohorts={"exchange": cohorts_exchange, "sector": {}},
            cohort_eligible={"price_close": True},
        )
        feat = report["features"]["price_close"]
        assert feat["overall"] == pytest.approx(1.0)
        assert feat["flags"] == []


class TestMnar:
    """(c) Missingness correlated with the label is flagged as MNAR."""

    def test_missingness_correlated_with_label_flagged(self):
        from gefion.ml.coverage import compute_coverage_report

        grid_labels = {}
        cohorts_exchange = {}
        present = set()
        # Feature missing exactly when label is high → missingness predicts
        # the target. Single cohort so cohort disparity cannot fire.
        for i in range(40):
            sym = f"S{i}"
            cohorts_exchange[sym] = "NASDAQ"
            if i < 20:
                grid_labels[(sym, 1)] = 1.0   # high label, missing
            else:
                grid_labels[(sym, 1)] = -1.0  # low label, present
                present.add((sym, 1))
        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence={"fundamental_eps": present},
            cohorts={"exchange": cohorts_exchange, "sector": {}},
            cohort_eligible={"fundamental_eps": True},
        )
        feat = report["features"]["fundamental_eps"]
        assert feat["mnar_label_corr"] is not None
        assert abs(feat["mnar_label_corr"]) >= 0.10
        mnar_flags = [f for f in feat["flags"] if f["kind"] == "mnar"]
        assert mnar_flags, f"expected an MNAR flag, got {feat['flags']}"


class TestSmallCohortIgnored:
    """Cohorts below MIN_COHORT_SIZE members are ignored (noise)."""

    def test_tiny_cohort_does_not_drive_disparity(self):
        from gefion.ml.coverage import compute_coverage_report

        grid_labels = {}
        cohorts_exchange = {}
        present = set()
        # 40 NASDAQ (present), plus a SINGLE NYSE symbol (absent). With the
        # default MIN_COHORT_SIZE=30 the NYSE cohort of 1 is ignored, so there
        # is only one sized cohort and no disparity.
        for i in range(40):
            sym = f"NAS{i}"
            cohorts_exchange[sym] = "NASDAQ"
            grid_labels[(sym, 1)] = float(i % 2)
            present.add((sym, 1))
        cohorts_exchange["LONELY"] = "NYSE"
        grid_labels[("LONELY", 1)] = 0.0
        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence={"daily_market_cap": present},
            cohorts={"exchange": cohorts_exchange, "sector": {}},
            cohort_eligible={"daily_market_cap": True},
        )
        feat = report["features"]["daily_market_cap"]
        cohort_flags = [f for f in feat["flags"] if f["kind"] == "cohort_bias"]
        assert cohort_flags == []
        # The undersized NYSE cohort is not reported among sized cohorts.
        assert "NYSE" not in feat["by_cohort"].get("exchange", {})


class TestMarketScopeFeature:
    """(f) A market-scope / macro feature has no exchange/sector cohort —
    cohort analysis is skipped gracefully (no crash, no false cohort flag)."""

    def test_market_feature_not_cohort_flagged(self):
        from gefion.ml.coverage import compute_coverage_report

        grid_labels = {}
        cohorts_exchange = {}
        present = set()
        # Same disparate shape as the biased case, but the feature is
        # market-scope (not per-stock) → cohort analysis must be skipped.
        for i in range(40):
            sym = f"NAS{i}"
            cohorts_exchange[sym] = "NASDAQ"
            grid_labels[(sym, 1)] = float(i % 2)
            present.add((sym, 1))
        for i in range(40):
            sym = f"NYS{i}"
            cohorts_exchange[sym] = "NYSE"
            grid_labels[(sym, 1)] = float(i % 2)
        report = compute_coverage_report(
            grid_labels=grid_labels,
            presence={"market_breadth": present},
            cohorts={"exchange": cohorts_exchange, "sector": {}},
            cohort_eligible={"market_breadth": False},
        )
        feat = report["features"]["market_breadth"]
        assert feat["scope"] == "market"
        assert feat["by_cohort"] == {}
        cohort_flags = [f for f in feat["flags"] if f["kind"] == "cohort_bias"]
        assert cohort_flags == []


class TestBuildPathWiring:
    """The audit must actually be INVOKED by the dataset build path — a guard
    so the wiring cannot silently vanish and leave `coverage.py` dead code.
    (The DB test `test_export_hook_records_coverage` guards the same wiring
    end-to-end via the provenance side-effect; this spy guards the delegation
    directly, without a database.)"""

    def test_run_coverage_audit_delegates_to_audit(self, monkeypatch):
        import pandas as pd

        import gefion.ml.coverage as coverage_mod
        from gefion.ml import dataset as dataset_mod

        calls = {}

        def spy(conn, *, name, version, symbols, feature_names, labels_df,
                exclude_features=None, universe=None, **kwargs):
            calls.update(name=name, version=version, symbols=list(symbols),
                         features=list(feature_names), n_labels=len(labels_df))
            return {"grid_rows": 0, "features": {}, "flagged": []}

        monkeypatch.setattr(coverage_mod, "audit_dataset_coverage", spy)

        labels_df = pd.DataFrame({
            "symbol": ["A", "B"], "date": ["2024-01-02", "2024-01-02"],
            "forward_return": [0.01, -0.02]})
        progress = []
        dataset_mod._run_coverage_audit(
            None,
            manifest={"name": "wired", "version": "v9", "universe": {}},
            symbols=["A", "B"], feature_names=["f1"], exclude_features=[],
            labels_df=labels_df, on_progress=progress.append)

        assert calls["name"] == "wired"
        assert calls["version"] == "v9"
        assert calls["symbols"] == ["A", "B"]
        assert calls["features"] == ["f1"]
        assert calls["n_labels"] == 2

    def test_run_coverage_audit_never_raises(self, monkeypatch):
        """Strictly non-blocking: an audit failure must not fail the build."""
        import pandas as pd

        import gefion.ml.coverage as coverage_mod
        from gefion.ml import dataset as dataset_mod

        def boom(*a, **k):
            raise RuntimeError("audit exploded")

        monkeypatch.setattr(coverage_mod, "audit_dataset_coverage", boom)
        progress = []
        # Must swallow the error (no raise) and report a skip.
        dataset_mod._run_coverage_audit(
            None, manifest={"name": "n", "version": "v", "universe": {}},
            symbols=["A"], feature_names=["f"], exclude_features=[],
            labels_df=pd.DataFrame({"symbol": ["A"], "date": ["2024-01-02"],
                                    "forward_return": [0.0]}),
            on_progress=progress.append)
        assert any("skipped" in m for m in progress)


# --------------------------------------------------------------------------
# Database integration tests
# --------------------------------------------------------------------------
@pytest.fixture
def db_conn():
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    from gefion.cli_helpers import db_connection, init_schema_tables
    from gefion.db.schema import test_db_url

    with db_connection(test_db_url()) as conn:
        init_schema_tables(conn, [
            "stocks", "feature_definitions", "computed_features",
            "ml_datasets", "system_observations",
        ])
        yield conn
        _cleanup(conn)


def _cleanup(conn):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM computed_features WHERE data_id IN "
            "(SELECT id FROM stocks WHERE symbol LIKE %s)", (PFX + "%",))
        cur.execute(
            "DELETE FROM system_observations WHERE observer = %s "
            "AND evidence->>'dataset' LIKE %s", (OBSERVER, PFX + "%"))
        cur.execute("DELETE FROM ml_datasets WHERE name LIKE %s", (PFX + "%",))
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE %s",
                    (PFX + "%",))
        cur.execute("DELETE FROM stocks WHERE symbol LIKE %s", (PFX + "%",))
    conn.commit()


def _seed_biased_dataset(conn, dataset_name, version, *,
                         n_nasdaq=6, n_nyse=6, feature=PFX + "_pe"):
    """Insert NASDAQ + NYSE stocks and a feature present ONLY on NASDAQ.

    Returns (symbols, feature_name, labels_df) ready for the audit.
    """
    import pandas as pd

    _cleanup(conn)
    symbols = []
    labels_rows = []
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name, "
            "entity_table) VALUES (%s, %s, 'stocks') RETURNING id",
            (feature, feature + "_fn"))
        feat_id = cur.fetchone()[0]
        for i in range(n_nasdaq + n_nyse):
            exch = "NASDAQ" if i < n_nasdaq else "NYSE"
            sym = f"{PFX}{i}"
            symbols.append(sym)
            cur.execute(
                "INSERT INTO stocks (symbol, exchange, sector) "
                "VALUES (%s, %s, %s) RETURNING id",
                (sym, exch, "Tech"))
            sid = cur.fetchone()[0]
            # Feature present only on NASDAQ.
            if exch == "NASDAQ":
                cur.execute(
                    "INSERT INTO computed_features (data_id, date, "
                    "feature_id, value) VALUES (%s, '2024-01-02', %s, %s)",
                    (sid, feat_id, 1.5 + i))
            # Label independent of exchange (balanced within present/absent),
            # so ONLY the cohort-coverage disparity flags — not MNAR.
            labels_rows.append(
                {"symbol": sym, "date": "2024-01-02",
                 "forward_return": 0.01 if i % 2 == 0 else -0.01})
    conn.commit()
    labels_df = pd.DataFrame(labels_rows)

    # Register the dataset row (audit stamps coverage into its universe JSONB).
    from gefion.ml.store import upsert_ml_dataset
    upsert_ml_dataset(conn, {
        "name": dataset_name, "version": version,
        "universe": {"symbols": symbols},
        "feature_names": [feature], "lookback_days": 200,
        "horizons_days": [7], "label_spec": {"type": "x", "thresholds": {}},
        "split_spec": {"type": "walk_forward"},
        "artifact_uri": "x/manifest.json", "checksum": "x",
    })
    return symbols, feature, labels_df


def test_audit_records_coverage_in_provenance(db_conn):
    """(d) The audit records per-feature coverage (overall + by cohort) in the
    ml_datasets.universe JSONB provenance."""
    from gefion.ml.coverage import audit_dataset_coverage

    name, version = PFX + "_prov", "v1"
    symbols, feature, labels_df = _seed_biased_dataset(db_conn, name, version)
    universe = {"symbols": symbols}

    audit_dataset_coverage(
        db_conn, name=name, version=version, symbols=symbols,
        feature_names=[feature], labels_df=labels_df, universe=universe,
        min_cohort_size=2,
    )

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT universe FROM ml_datasets WHERE name = %s AND version = %s",
            (name, version))
        stored = cur.fetchone()[0]
    assert "coverage" in stored, f"no coverage in provenance: {stored}"
    cov = stored["coverage"]
    assert feature in cov["features"]
    feat = cov["features"][feature]
    assert feat["overall"] == pytest.approx(0.5, abs=0.05)
    assert feat["by_cohort"]["exchange"]["NYSE"] == pytest.approx(0.0)
    assert feat["by_cohort"]["exchange"]["NASDAQ"] == pytest.approx(1.0)


def test_audit_emits_observation_for_biased_feature(db_conn):
    """A biased feature yields one OPEN system_observation (#144)."""
    from gefion.ml.coverage import audit_dataset_coverage

    name, version = PFX + "_obs", "v1"
    symbols, feature, labels_df = _seed_biased_dataset(db_conn, name, version)

    audit_dataset_coverage(
        db_conn, name=name, version=version, symbols=symbols,
        feature_names=[feature], labels_df=labels_df,
        universe={"symbols": symbols}, min_cohort_size=2)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT observation, evidence, category FROM system_observations "
            "WHERE observer = %s AND review_state = 'open' "
            "AND evidence->>'dataset' = %s AND evidence->>'feature' = %s",
            (OBSERVER, name, feature))
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected exactly one observation, got {rows}"
    text, evidence, category = rows[0]
    assert feature in text and "NYSE" in text and "NASDAQ" in text
    assert evidence["bias_kind"].startswith("cohort_bias")


def test_audit_idempotent_no_duplicate_observation(db_conn):
    """(e) Rebuilding the same dataset does not duplicate the open
    observation — one OPEN row per (dataset+version, feature, bias-kind)."""
    from gefion.ml.coverage import audit_dataset_coverage

    name, version = PFX + "_idem", "v1"
    symbols, feature, labels_df = _seed_biased_dataset(db_conn, name, version)

    for _ in range(2):
        audit_dataset_coverage(
            db_conn, name=name, version=version, symbols=symbols,
            feature_names=[feature], labels_df=labels_df,
            universe={"symbols": symbols}, min_cohort_size=2)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM system_observations WHERE observer = %s "
            "AND review_state = 'open' AND evidence->>'dataset' = %s",
            (OBSERVER, name))
        assert cur.fetchone()[0] == 1


def test_export_hook_records_coverage(db_conn, tmp_path):
    """The audit is wired into the dataset export path: exporting a dataset
    with horizons records coverage in the ml_datasets provenance."""
    from gefion.ml.dataset import export_dataset_artifacts
    from gefion.ml.store import upsert_ml_dataset

    name, version = PFX + "_hook", "v1"
    _cleanup(db_conn)
    symbols = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name, "
            "entity_table) VALUES (%s, %s, 'stocks') RETURNING id",
            (PFX + "_f", PFX + "_f_fn"))
        feat_id = cur.fetchone()[0]
        for i in range(6):
            exch = "NASDAQ" if i < 3 else "NYSE"
            sym = f"{PFX}{i}"
            symbols.append(sym)
            cur.execute("INSERT INTO stocks (symbol, exchange, sector) "
                        "VALUES (%s, %s, 'Tech') RETURNING id", (sym, exch))
            sid = cur.fetchone()[0]
            # 5 daily bars so a 2-day-horizon label exists.
            for d in range(1, 6):
                cur.execute(
                    "INSERT INTO stock_ohlcv (data_id, date, close, "
                    "adjusted_close) VALUES (%s, %s, %s, %s)",
                    (sid, f"2024-01-0{d}", 100 + d + i, 100 + d + i))
                if exch == "NASDAQ":
                    cur.execute(
                        "INSERT INTO computed_features (data_id, date, "
                        "feature_id, value) VALUES (%s, %s, %s, %s)",
                        (sid, f"2024-01-0{d}", feat_id, float(d + i)))
    db_conn.commit()

    manifest = {
        "name": name, "version": version,
        "universe": {"symbols": symbols},
        "feature_names": [PFX + "_f"],
        "horizons_days": [2],
        "label_spec": {"thresholds": {"2": {"weak": 0.001, "strong": 0.01}}},
        "format": "csv",
        "coverage_audit": {"min_cohort_size": 2},
    }
    upsert_ml_dataset(db_conn, {
        "name": name, "version": version, "universe": manifest["universe"],
        "feature_names": [PFX + "_f"], "lookback_days": 200,
        "horizons_days": [2], "label_spec": {"thresholds": {}},
        "split_spec": {"type": "walk_forward"},
        "artifact_uri": "x", "checksum": "x"})

    export_dataset_artifacts(db_conn, manifest=manifest, out_dir=tmp_path)

    with db_conn.cursor() as cur:
        cur.execute("SELECT universe FROM ml_datasets WHERE name = %s "
                    "AND version = %s", (name, version))
        stored = cur.fetchone()[0]
    assert "coverage" in stored
    assert (PFX + "_f") in stored["coverage"]["features"]
    _cleanup(db_conn)
