"""Runtime observer (#162): proven-but-unexported machine feature functions.

A machine-generated feature function that is active, has survived probation,
and has either aged into durability (active >= N days) or feeds a production
model has earned a place in version control. The observer only RECORDS a
system_observation (the #144 philosophy — observations never act); a human
adopts it, runs `feat-fx-export`, reviews the code, and commits.

TDD: these tests are written before the implementation.
"""
import json
import os
from pathlib import Path

import pytest

DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"

OBSERVER = "feature_export"
# Distinct prefix so fixtures can clean up only their own rows.
PFX = "qexp162"


# --------------------------------------------------------------------------
# Unit tests (no database)
# --------------------------------------------------------------------------
class TestExportedPairs:
    """The 'is it in version control?' check scans the seed directory by the
    JSON body's name + version — filenames are not a reliable key (some
    committed seeds are bare-named)."""

    def test_reads_name_and_version_from_body(self, tmp_path):
        from gefion.features.export_observer import exported_pairs

        (tmp_path / "foo_v1.0.json").write_text(
            json.dumps({"name": "foo", "version": "1.0"}))
        # Bare-named file with the version only in the body.
        (tmp_path / "bar.json").write_text(
            json.dumps({"name": "bar", "version": "2.0"}))

        assert exported_pairs(tmp_path) == {("foo", "1.0"), ("bar", "2.0")}

    def test_missing_directory_is_empty(self, tmp_path):
        from gefion.features.export_observer import exported_pairs

        assert exported_pairs(tmp_path / "does-not-exist") == set()

    def test_ignores_unparseable_files(self, tmp_path):
        from gefion.features.export_observer import exported_pairs

        (tmp_path / "good.json").write_text(
            json.dumps({"name": "good", "version": "1.0"}))
        (tmp_path / "broken.json").write_text("{ not json")
        assert exported_pairs(tmp_path) == {("good", "1.0")}


class TestObservationText:
    """The observation text must match the shape specified in #162."""

    def test_text_shape(self):
        from gefion.features.export_observer import observation_text

        text = observation_text("indicator_kama", "cycle-42")
        assert "indicator_kama" in text
        assert "cycle-42" in text
        assert "proven winner" in text
        assert "not captured in version control" in text
        assert "feat-fx-export --functions indicator_kama" in text


# --------------------------------------------------------------------------
# Database integration tests
# --------------------------------------------------------------------------
@pytest.mark.skipif(not DB_TESTS_ENABLED, reason="Database tests disabled")
class TestExportObserverDB:
    @pytest.fixture
    def conn(self):
        import psycopg
        from gefion.db import schema

        try:
            c = psycopg.connect(schema.test_db_url())
        except psycopg.OperationalError as exc:
            pytest.skip(f"DB not available: {exc}")
        c.autocommit = True
        # Ensure every table this observer touches exists (CI collection
        # order differs from local; call the canonical creators).
        schema.create_feature_functions_table(c)
        schema.create_feature_definitions_table(c)
        schema.create_ml_datasets_table(c)
        schema.create_ml_runs_table(c)
        schema.create_ml_models_table(c)
        schema.create_system_observations_table(c)

        self._cleanup(c)
        yield c
        self._cleanup(c)
        c.close()

    @staticmethod
    def _cleanup(c):
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM system_observations WHERE observer = %s "
                "AND evidence->>'function' LIKE %s", (OBSERVER, f"exp_{PFX}%"))
            cur.execute("DELETE FROM experiments WHERE name LIKE %s",
                        (f"{PFX}-exp-%",))
            cur.execute("DELETE FROM ml_models WHERE name LIKE %s", (f"{PFX}%",))
            cur.execute("DELETE FROM ml_datasets WHERE name LIKE %s", (f"{PFX}%",))
            cur.execute("DELETE FROM ml_runs WHERE notes LIKE %s", (f"{PFX}%",))
            cur.execute("DELETE FROM feature_definitions WHERE name LIKE %s",
                        (f"exp_{PFX}%",))
            cur.execute("DELETE FROM feature_functions WHERE name LIKE %s",
                        (f"exp_{PFX}%",))

    # -- helpers ----------------------------------------------------------
    def _make_function(self, conn, tag, *, version="cycle-1",
                       promoted_days_ago=40, status="active",
                       demoted=False, probation_status="passed",
                       created_by="cycle_runner",
                       tags=("ai-generated", "experimental")):
        """Insert a machine-generated feature function plus the experiment
        that promoted it. Returns the function name."""
        from psycopg.types.json import Json

        base = f"{PFX}_{tag}"          # experiment's feature function_name
        fn_name = f"exp_{base}"        # promoted function is exp_<base>
        results = {"best_params": {}}
        if probation_status is not None:
            results["probation"] = {"status": probation_status}
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiments
                    (name, experiment_type, config, results, status,
                     best_score, objective_metric, promoted_at, demoted_at,
                     probation_until)
                VALUES (%s, 'feature_engineering', %s, %s, 'completed',
                        0.03, 'quantile_loss',
                        NOW() - make_interval(days => %s::int),
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        NOW() - make_interval(days => 1))
                """,
                (f"{PFX}-exp-{tag}",
                 Json({"feature_config": {"function_name": base}}),
                 Json(results), promoted_days_ago, demoted),
            )
            cur.execute(
                """
                INSERT INTO feature_functions
                    (name, version, status, language, function_body,
                     created_by, tags)
                VALUES (%s, %s, %s, 'python', 'def compute(rows, specs): ...',
                        %s, %s)
                """,
                (fn_name, version, status, created_by, list(tags)),
            )
        return fn_name, version

    def _consume_by_production_model(self, conn, fn_name):
        """Wire a production (active) model that consumes fn_name via a
        feature definition + dataset."""
        def_name = f"exp_{PFX}_defn_{fn_name[-4:]}"
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feature_definitions (name, function_name, active)
                   VALUES (%s, %s, TRUE)""", (def_name, fn_name))
            cur.execute(
                """INSERT INTO ml_runs (run_type, run_config, notes)
                   VALUES ('train', '{}'::jsonb, %s) RETURNING id""",
                (f"{PFX}-run",))
            run_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO ml_datasets
                       (name, version, feature_names, lookback_days,
                        horizons_days, label_spec, split_spec, artifact_uri)
                   VALUES (%s, '1', %s, 30, ARRAY[20],
                           '{}'::jsonb, '{}'::jsonb, 'memory://x')
                   RETURNING id""",
                (f"{PFX}-ds", [def_name]))
            ds_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO ml_models
                       (name, version, train_run_id, dataset_id, artifact_uri,
                        active)
                   VALUES (%s, '1', %s, %s, 'memory://m', TRUE)""",
                (f"{PFX}-model", run_id, ds_id))

    @staticmethod
    def _observations_for(conn, fn_name):
        from gefion import observations
        return [r for r in observations.list_observations(conn, observer=OBSERVER)
                if r["evidence"].get("function") == fn_name]

    # -- tests ------------------------------------------------------------
    def test_proven_unexported_function_emits(self, conn, tmp_path):
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, ver = self._make_function(conn, "a", promoted_days_ago=40)
        oids = record_unexported_function_observations(conn, seed_dir=tmp_path)

        rows = self._observations_for(conn, fn)
        assert len(rows) == 1
        assert rows[0]["id"] in oids
        assert rows[0]["category"] in (
            "improvement", "anomaly", "tuning", "hypothesis")
        assert rows[0]["evidence"]["version"] == ver
        assert "proven winner" in rows[0]["observation"]
        assert f"feat-fx-export --functions {fn}" in rows[0]["observation"]

    def test_exported_function_does_not_emit(self, conn, tmp_path):
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, ver = self._make_function(conn, "b", promoted_days_ago=40)
        (tmp_path / f"{fn}_v{ver}.json").write_text(
            json.dumps({"name": fn, "version": ver}))

        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert self._observations_for(conn, fn) == []

    def test_experimental_function_does_not_emit(self, conn, tmp_path):
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "c", status="experimental")
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert self._observations_for(conn, fn) == []

    def test_demoted_function_does_not_emit(self, conn, tmp_path):
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "d", status="demoted", demoted=True,
                                    probation_status="demoted")
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert self._observations_for(conn, fn) == []

    def test_not_probation_passed_does_not_emit(self, conn, tmp_path):
        """Merely applied / still monitoring is not 'survived probation'."""
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "e", probation_status=None)
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert self._observations_for(conn, fn) == []

    def test_recent_and_unconsumed_does_not_emit(self, conn, tmp_path):
        """Age branch AND consumed branch both false -> not proven enough."""
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "f", promoted_days_ago=5)
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert self._observations_for(conn, fn) == []

    def test_age_branch_emits(self, conn, tmp_path):
        """Active >= 30 days, not consumed by any model -> qualifies."""
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "g", promoted_days_ago=45)
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert len(self._observations_for(conn, fn)) == 1

    def test_consumed_by_prod_model_branch_emits(self, conn, tmp_path):
        """Recently promoted (< 30d) but feeding a production model -> qualifies."""
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "h", promoted_days_ago=3)
        self._consume_by_production_model(conn, fn)
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert len(self._observations_for(conn, fn)) == 1

    def test_consumed_by_inactive_model_does_not_emit(self, conn, tmp_path):
        """Only ACTIVE (production) models count for the consumed branch."""
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "i", promoted_days_ago=3)
        self._consume_by_production_model(conn, fn)
        with conn.cursor() as cur:
            cur.execute("UPDATE ml_models SET active = FALSE WHERE name LIKE %s",
                        (f"{PFX}%",))
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        assert self._observations_for(conn, fn) == []

    def test_idempotent_across_runs(self, conn, tmp_path):
        from gefion.features.export_observer import (
            record_unexported_function_observations)

        fn, _ = self._make_function(conn, "j", promoted_days_ago=40)
        record_unexported_function_observations(conn, seed_dir=tmp_path)
        second = record_unexported_function_observations(conn, seed_dir=tmp_path)

        assert second == []  # nothing new the second time
        assert len(self._observations_for(conn, fn)) == 1

    def test_run_probation_checks_wires_observer(self):
        import inspect
        from gefion.experiments import probation
        src = inspect.getsource(probation.run_probation_checks)
        assert "record_unexported_function_observations" in src
