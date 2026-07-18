"""System-observations ledger (#144).

TDD: written FIRST. The ledger holds OBSERVATIONS, never actions: nothing
reads it programmatically to change behavior; adoption is a human act.
Ledger semantics match the house pattern: append-only in spirit,
supersede-never-erase, terminal review states, provenance on every row.
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
    schema.create_system_observations_table(c)

    def _cleanup(cur):
        cur.execute("DELETE FROM system_observations WHERE observer LIKE 'qob_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


# --- schema (owner-approved DDL) ---------------------------------------------------

class TestSchema:
    def test_table_exists_with_expected_columns(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'system_observations'")
            cols = {r[0] for r in cur.fetchall()}
        expected = {"id", "observer", "category", "observation", "evidence",
                    "suggested_action", "review_state", "reviewed_by",
                    "reviewed_at", "review_reason", "created_at"}
        assert expected.issubset(cols), f"missing: {expected - cols}"

    def test_creator_idempotent_and_constrained(self, conn):
        schema.create_system_observations_table(conn)
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO system_observations (observer, category, "
                    "observation) VALUES ('qob_x', 'musing', 'x')")

    def test_schema_sql_carries_the_table(self):
        from pathlib import Path
        import gefion
        root = Path(gefion.__file__).parent.parent.parent
        assert "system_observations" in (root / "sql" / "schema.sql").read_text()
        assert list((root / "sql" / "migrations").glob("*system_observations*"))


# --- store + review gate -----------------------------------------------------------

class TestStore:
    def _record(self, conn, **kw):
        from gefion import observations
        defaults = dict(observer="qob_test", category="tuning",
                        observation="effective-N floor dominates at h=20",
                        evidence={"refused": 8, "total": 10},
                        suggested_action="consider longer holdout geometry")
        defaults.update(kw)
        return observations.record(conn, **defaults)

    def test_record_and_get(self, conn):
        from gefion import observations
        oid = self._record(conn)
        o = observations.get(conn, oid)
        assert o["review_state"] == "open"
        assert o["observer"] == "qob_test"
        assert o["evidence"]["refused"] == 8
        assert o["created_at"] is not None

    def test_list_defaults_to_open_newest_first(self, conn):
        from gefion import observations
        self._record(conn, observation="first")
        self._record(conn, observation="second")
        rows = observations.list_observations(conn)
        texts = [r["observation"] for r in rows if r["observer"] == "qob_test"]
        assert texts.index("second") < texts.index("first")

    def test_review_transitions(self, conn):
        from gefion import observations
        oid = self._record(conn)
        observations.review(conn, oid, "acknowledged", reviewer="simon")
        assert observations.get(conn, oid)["review_state"] == "acknowledged"
        # acknowledged -> adopted is legal (ack is intermediate)
        observations.review(conn, oid, "adopted", reviewer="simon",
                            reason="filed as issue")
        o = observations.get(conn, oid)
        assert o["review_state"] == "adopted"
        assert o["reviewed_by"] == "simon"
        # terminal states are immutable — supersede, never rewrite
        with pytest.raises(ValueError, match="terminal"):
            observations.review(conn, oid, "rejected", reviewer="simon",
                                reason="no")

    def test_reject_requires_reason(self, conn):
        from gefion import observations
        oid = self._record(conn)
        with pytest.raises(ValueError, match="reason"):
            observations.review(conn, oid, "rejected", reviewer="simon")

    def test_open_count_for_db_health(self, conn):
        from gefion import observations
        self._record(conn)
        assert observations.open_count(conn) >= 1


# --- the cycle-runner observer -----------------------------------------------------

class TestCycleObserver:
    def test_zero_survivor_cycle_records_observation(self, conn):
        """A completed cycle with experiments but zero FDR survivors is a
        geometry/power fact the system already computes — record it."""
        from gefion.experiments.cycle_runner import record_cycle_observation
        record_cycle_observation(conn, cycle_id=999001,
                                 summary={"proposed": 5, "completed": 5,
                                          "failed": 0, "fdr_survivors": 0})
        from gefion import observations
        rows = [r for r in observations.list_observations(conn)
                if r["observer"] == "cycle_runner"
                and r["evidence"].get("cycle_id") == 999001]
        assert len(rows) == 1
        assert rows[0]["category"] == "tuning"
        with conn.cursor() as cur:   # cleanup this non-qob row
            cur.execute("DELETE FROM system_observations WHERE id = %s",
                        (rows[0]["id"],))

    def test_surviving_cycle_records_nothing(self, conn):
        from gefion.experiments.cycle_runner import record_cycle_observation
        from gefion import observations
        before = observations.open_count(conn)
        record_cycle_observation(conn, cycle_id=999002,
                                 summary={"proposed": 5, "completed": 5,
                                          "failed": 0, "fdr_survivors": 2})
        assert observations.open_count(conn) == before

    def test_run_cycle_wires_the_observer(self):
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner
        assert "record_cycle_observation" in inspect.getsource(CycleRunner.run_cycle)


# --- surfaces ----------------------------------------------------------------------

class TestSurfaces:
    def test_cli_commands_exist(self):
        from typer.testing import CliRunner
        from gefion.cli import app
        r = CliRunner().invoke(app, ["observe", "--help"])
        assert r.exit_code == 0 and "--category" in r.output
        r = CliRunner().invoke(app, ["observations", "list", "--help"])
        assert r.exit_code == 0
        for verb in ("ack", "adopt", "reject"):
            r = CliRunner().invoke(app, ["observations", verb, "--help"])
            assert r.exit_code == 0, verb

    def test_mcp_tools_exist_with_agentic_guidance(self):
        from pathlib import Path
        import gefion
        server = (Path(gefion.__file__).parent.parent.parent /
                  "mcp-server" / "server.py").read_text()
        for tool in ("observe", "observations_list", "observations_review"):
            assert f'name="{tool}"' in server, tool
            assert f'name == "{tool}"' in server, tool
        # the observe description carries the when-to-use guidance
        assert "operating" in server[server.index('name="observe"'):
                                     server.index('name="observe"') + 2000].lower()

    def test_operator_skills_carry_standing_instruction(self):
        from pathlib import Path
        import gefion
        root = Path(gefion.__file__).parent.parent.parent
        gef = (root / ".claude" / "commands" / "gefion.md").read_text()
        assert "observe" in gef
        exp = (root / ".claude" / "commands" / "gefion-experiment.md").read_text()
        assert "observe" in exp

    def test_db_health_reports_open_observations(self):
        """db-health carries the open-observations count + warning line —
        the ledger is only useful if it reaches the operator's eyes."""
        from pathlib import Path
        import gefion
        src = (Path(gefion.__file__).parent / "cli.py").read_text()
        start = src.index("def db_health(")
        block = src[start:start + 20000]
        assert "open_observations" in block
        assert "gefion observations list" in block
