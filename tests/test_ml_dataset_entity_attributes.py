"""
DB-backed tests for entity attributes in ml/dataset.py (spec: general entity
attribute mechanism, sector as the first configured attribute).

Requires ENABLE_DB_TESTS=1 to run (see tests/test_ml_dataset_db_integration.py
for the same gating pattern).
"""
import os
from datetime import date

import pandas as pd
import pytest


@pytest.fixture(scope="module", autouse=True)
def _restore_db_after_module():
    """Restore canonical test DB state after this module's destructive
    per-test cleanup (issue #195)."""
    yield
    if os.getenv("ENABLE_DB_TESTS") == "1":
        from conftest import restore_test_db
        restore_test_db()


@pytest.fixture
def db_conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    from gefion.cli_helpers import db_connection, init_schema_tables
    from gefion.db.schema import test_db_url

    url = test_db_url()
    with db_connection(url) as conn:
        init_schema_tables(conn, [
            "stocks", "stock_ohlcv", "stocks_fundamentals",
            "feature_definitions", "computed_features", "ml_datasets",
        ])
        yield conn
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'ENTATTR_%')"
            )
            cur.execute(
                "DELETE FROM stocks_fundamentals WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'ENTATTR_%')"
            )
            cur.execute("DELETE FROM stocks WHERE symbol LIKE 'ENTATTR_%'")
        conn.commit()


def _insert_stock(conn, symbol):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks (symbol) VALUES (%s) "
            "ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol "
            "RETURNING id",
            (symbol,),
        )
        data_id = cur.fetchone()[0]
    conn.commit()
    return data_id


def _insert_prices(conn, data_id, dates):
    with conn.cursor() as cur:
        for d in dates:
            cur.execute(
                "INSERT INTO stock_ohlcv "
                "(data_id, date, open, high, low, close, adjusted_close, volume) "
                "VALUES (%s, %s, 10, 11, 9, 10, 10, 1000) "
                "ON CONFLICT (data_id, date) DO NOTHING",
                (data_id, d),
            )
    conn.commit()


def _insert_fundamentals(conn, data_id, snapshot_date, sector=None, industry=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks_fundamentals (data_id, date, sector, industry) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (data_id, date) DO UPDATE SET "
            "sector = EXCLUDED.sector, industry = EXCLUDED.industry",
            (data_id, snapshot_date, sector, industry),
        )
    conn.commit()


def _export(conn, symbols, out_dir, **manifest_kwargs):
    from gefion.ml.dataset import export_dataset_artifacts

    manifest = {
        "universe": {"symbols": symbols},
        "horizons_days": [],
        "format": "csv",
    }
    manifest.update(manifest_kwargs)
    export_dataset_artifacts(conn, manifest=manifest, out_dir=out_dir)
    return manifest


def _feature_names(out_dir, symbol):
    df = pd.read_csv(out_dir / "features.csv")
    if df.empty:
        return set()
    return set(df[df["symbol"] == symbol]["feature_name"])


def _feature_value(out_dir, symbol, feature_name):
    df = pd.read_csv(out_dir / "features.csv")
    row = df[(df["symbol"] == symbol) & (df["feature_name"] == feature_name)]
    assert len(row) == 1, f"expected exactly one {feature_name} row, got {len(row)}"
    return float(row["value"].iloc[0])


def test_slugify_pure_function():
    from gefion.ml.dataset import _slugify_attribute_value

    assert _slugify_attribute_value("FINANCIAL SERVICES") == "financial_services"


class TestKnownAndUnknownSector:

    def test_known_value_emits_slug_and_known_rows(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_KNOWN")
        _insert_prices(db_conn, data_id, [date(2026, 7, 10)])
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7), sector="Technology")

        _export(db_conn, ["ENTATTR_KNOWN"], tmp_path)

        names = _feature_names(tmp_path, "ENTATTR_KNOWN")
        assert "sector_technology" in names
        assert "sector_known" in names
        assert _feature_value(tmp_path, "ENTATTR_KNOWN", "sector_technology") == 1.0
        assert _feature_value(tmp_path, "ENTATTR_KNOWN", "sector_known") == 1.0

    def test_null_sector_value_emits_neither_row(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_NULLSEC")
        _insert_prices(db_conn, data_id, [date(2026, 7, 10)])
        # A snapshot row exists, but sector itself is NULL (unknown).
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7), sector=None)

        _export(db_conn, ["ENTATTR_NULLSEC"], tmp_path)

        names = _feature_names(tmp_path, "ENTATTR_NULLSEC")
        assert not any(str(n).startswith("sector_") for n in names)

    def test_no_fundamentals_rows_at_all_build_succeeds_no_attribute_rows(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_NOFUND")
        _insert_prices(db_conn, data_id, [date(2026, 7, 10)])
        # No stocks_fundamentals row inserted for this stock at all.

        manifest = _export(db_conn, ["ENTATTR_NOFUND"], tmp_path)

        assert (tmp_path / "features.csv").exists()
        names = _feature_names(tmp_path, "ENTATTR_NOFUND")
        assert not any(str(n).startswith("sector_") for n in names)
        # No snapshot ever existed, so nothing was backdated.
        assert manifest["attributes_point_in_time"] is True

    def test_only_own_category_emitted(self, db_conn, tmp_path):
        tech_id = _insert_stock(db_conn, "ENTATTR_TECH")
        fin_id = _insert_stock(db_conn, "ENTATTR_FIN")
        _insert_prices(db_conn, tech_id, [date(2026, 7, 10)])
        _insert_prices(db_conn, fin_id, [date(2026, 7, 10)])
        _insert_fundamentals(db_conn, tech_id, date(2026, 7, 7), sector="Technology")
        _insert_fundamentals(db_conn, fin_id, date(2026, 7, 7), sector="Financial Services")

        _export(db_conn, ["ENTATTR_TECH", "ENTATTR_FIN"], tmp_path)

        tech_names = _feature_names(tmp_path, "ENTATTR_TECH")
        fin_names = _feature_names(tmp_path, "ENTATTR_FIN")
        assert "sector_technology" in tech_names
        assert "sector_financial_services" not in tech_names
        assert "sector_financial_services" in fin_names
        assert "sector_technology" not in fin_names


class TestAsOfAndBackdating:

    def test_as_of_uses_latest_snapshot_not_after_row_date(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_ASOF")
        _insert_prices(db_conn, data_id, [date(2026, 7, 20), date(2026, 8, 5)])
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7), sector="Industrials")
        _insert_fundamentals(db_conn, data_id, date(2026, 8, 1), sector="Technology")

        _export(db_conn, ["ENTATTR_ASOF"], tmp_path)

        df = pd.read_csv(tmp_path / "features.csv")
        rows = df[df["symbol"] == "ENTATTR_ASOF"]
        before_y = set(rows[rows["date"] == "2026-07-20"]["feature_name"])
        after_y = set(rows[rows["date"] == "2026-08-05"]["feature_name"])

        assert "sector_industrials" in before_y
        assert "sector_technology" not in before_y
        assert "sector_technology" in after_y
        assert "sector_industrials" not in after_y

    def test_backdating_before_first_snapshot_uses_earliest_and_flags_manifest(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_BACKDATE")
        _insert_prices(db_conn, data_id, [date(2026, 6, 1)])  # before any snapshot
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7), sector="Healthcare")

        manifest = _export(db_conn, ["ENTATTR_BACKDATE"], tmp_path)

        names = _feature_names(tmp_path, "ENTATTR_BACKDATE")
        assert "sector_healthcare" in names
        assert manifest["attributes_point_in_time"] is False

    def test_all_rows_at_or_after_snapshot_records_point_in_time_true(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_PIT")
        _insert_prices(db_conn, data_id, [date(2026, 7, 7), date(2026, 8, 1)])
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7), sector="Energy")

        manifest = _export(db_conn, ["ENTATTR_PIT"], tmp_path)

        assert manifest["attributes_point_in_time"] is True


class TestSlugging:

    def test_uppercase_and_spaces_become_lowercase_underscored_slug(self, db_conn, tmp_path):
        data_id = _insert_stock(db_conn, "ENTATTR_SLUG")
        _insert_prices(db_conn, data_id, [date(2026, 7, 10)])
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7), sector="FINANCIAL SERVICES")

        _export(db_conn, ["ENTATTR_SLUG"], tmp_path)

        names = _feature_names(tmp_path, "ENTATTR_SLUG")
        assert "sector_financial_services" in names


class TestGenerality:

    def test_second_spec_reuses_industry_column_without_code_changes(self, db_conn, tmp_path):
        from gefion.ml.dataset import (ENTITY_ATTRIBUTE_SPECS, EntityAttributeSpec,
                                       export_dataset_artifacts)

        # Proves the default spec list is sector-only; industry is opt-in per
        # this test, not hardcoded into the module.
        assert not any(s.name == "industry" for s in ENTITY_ATTRIBUTE_SPECS)

        data_id = _insert_stock(db_conn, "ENTATTR_GEN")
        _insert_prices(db_conn, data_id, [date(2026, 7, 10)])
        _insert_fundamentals(db_conn, data_id, date(2026, 7, 7),
                             sector="Technology", industry="Software - Infrastructure")

        industry_spec = EntityAttributeSpec(
            name="industry", table="stocks_fundamentals", key_column="data_id",
            as_of_column="date", value_column="industry", encoding="one_hot",
            prefix="industry",
        )
        manifest = {
            "universe": {"symbols": ["ENTATTR_GEN"]},
            "horizons_days": [],
            "format": "csv",
        }
        export_dataset_artifacts(
            db_conn, manifest=manifest, out_dir=tmp_path,
            entity_attribute_specs=list(ENTITY_ATTRIBUTE_SPECS) + [industry_spec],
        )

        names = _feature_names(tmp_path, "ENTATTR_GEN")
        assert "industry_software_infrastructure" in names
        assert "industry_known" in names
        # The default (sector) spec still runs alongside the injected one.
        assert "sector_technology" in names
