"""Universe provenance stamps (015, US4).

TDD: written FIRST. Every dataset, experiment, and model artifact records
which universe (name + definition fingerprint) its cross-section came from —
same rationale as device provenance (#146): reproduction requires recording
what actually ran. Results predating 015 are distinguishable by the stamp's
absence.
"""
import os

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_universe_definitions_table(c)
    schema.create_universe_exclusions_table(c)
    from gefion.universe.definitions import seed_default_universe
    seed_default_universe(c)
    yield c
    c.close()


class TestResolvedProvenance:
    def test_provenance_shape(self, conn):
        from gefion.universe import resolve_universe
        stamp = resolve_universe(conn, None).provenance()
        assert stamp["universe_name"] == "modeling_default"
        assert stamp["universe_fingerprint"].startswith("sha256:")

    def test_all_provenance_is_explicit(self, conn):
        from gefion.universe import resolve_universe
        stamp = resolve_universe(conn, "all").provenance()
        assert stamp == {"universe_name": "all",
                         "universe_fingerprint": None}


class TestDatasetStamp:
    def test_manifest_universe_gains_stamp(self, conn):
        """Resolving dataset symbols stamps the manifest's universe dict
        with name/fingerprint/resolved_count (rides ml_datasets.universe
        JSONB — no DDL)."""
        from gefion.ml.dataset import resolve_universe_symbols
        universe = {}
        symbols = resolve_universe_symbols(conn, universe)
        assert universe["universe_name"] == "modeling_default"
        assert universe["universe_fingerprint"].startswith("sha256:")
        assert universe["resolved_count"] == len(symbols)

    def test_explicit_symbols_stamp_as_explicit(self, conn):
        from gefion.ml.dataset import resolve_universe_symbols
        universe = {"symbols": ["QQQ_X"]}
        resolve_universe_symbols(conn, universe)
        assert universe["universe_name"] == "explicit"
        assert universe["universe_fingerprint"] is None


class TestModelArtifactStamp:
    def test_train_result_and_artifact_carry_universe(self, conn, tmp_path):
        import numpy as np
        import pandas as pd

        from gefion.ml import models
        from gefion.universe import resolve_universe
        rng = np.random.default_rng(11)
        X = pd.DataFrame({"a": rng.normal(size=60),
                          "b": rng.normal(size=60)})
        y = pd.Series(rng.normal(size=60))
        stamp = resolve_universe(conn, None).provenance()
        data = models.train_quantile_model(
            X, y, algorithm="quantile_regression", quantiles=[0.5],
            device="cpu", universe=stamp)
        assert data["universe"]["universe_name"] == "modeling_default"
        models.save_model_artifact(data, tmp_path / "m",
                                   {"algorithm": "quantile_regression"})
        loaded = models.load_model_artifact(tmp_path / "m")
        assert loaded["universe"]["universe_name"] == "modeling_default"

    def test_untrained_stamp_absent_not_fabricated(self):
        """No universe passed -> no stamp (pre-015 results distinguishable)."""
        import numpy as np
        import pandas as pd

        from gefion.ml import models
        rng = np.random.default_rng(12)
        X = pd.DataFrame({"a": rng.normal(size=60),
                          "b": rng.normal(size=60)})
        y = pd.Series(rng.normal(size=60))
        data = models.train_quantile_model(
            X, y, algorithm="quantile_regression", quantiles=[0.5],
            device="cpu")
        assert data.get("universe") is None
