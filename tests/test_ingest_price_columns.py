"""Price ingest must persist every column the provider returns (issue #264).

`split_coefficient` and `dividend_amount` were NULL in 100% of `stock_ohlcv`
(0 of 29,190,617 rows) because the writer that all price ingest actually uses
built a 9-column INSERT and silently omitted both -- while the module imported
a complete 11-column writer it never called.

The provider returns the fields and the parser reads them; only the write
dropped them. That absence is the upstream cause of #262 (unrecorded reverse
splits killed six A/B runs) and it also made short dividend costs free.

Follow-up (converge writers): the two implementations have since been merged
into the single `insert_stock_ohlcv` -- these tests now exercise that
survivor directly, plus the two behaviors ported over from the deleted
`_batch_insert_prices` (the >=1e12 corruption guard and date-string parsing).
"""
from __future__ import annotations

import pathlib
from datetime import date

import pytest

from gefion.db.ingest import insert_stock_ohlcv


class _Cur:
    """Captures the SQL and params instead of executing them."""

    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    @property
    def rowcount(self):
        return 1


class _Conn:
    def __init__(self):
        self.statements = []
        self.committed = False

    def cursor(self):
        return _Cur(self.statements)

    def commit(self):
        self.committed = True


def _row(**overrides):
    row = {
        "symbol": "WMT",
        "date": "2024-02-26",
        "open": 175.0,
        "high": 176.0,
        "low": 174.0,
        "close": 59.60,
        "adjusted_close": 59.60,
        "volume": 22_054_703,
        "dividend_amount": 0.83,
        "split_coefficient": 3.0,
    }
    row.update(overrides)
    return row


def _sql_of(conn):
    assert conn.statements, "no INSERT was issued"
    return conn.statements[0][0]


def _params_of(conn):
    return conn.statements[0][1]


# --------------------------------------------------------------------------- #
# The defect: two columns never reached the database.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("column", ["split_coefficient", "dividend_amount"])
def test_insert_names_the_dropped_column(column):
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[_row()], update_existing=False)

    assert column in _sql_of(conn)


def test_insert_carries_eleven_columns_not_nine():
    """The 9-placeholder tuple is the bug's signature."""
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[_row()], update_existing=False)

    sql = _sql_of(conn)
    assert "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)" in sql
    assert "(%s, %s, %s, %s, %s, %s, %s, %s, %s)" not in sql


def test_values_round_trip_into_params():
    """A 3:1 split must arrive as 3.0, not be dropped on the floor."""
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=7, rows=[_row()], update_existing=False)

    params = _params_of(conn)
    assert 3.0 in params or "3.0" in [str(p) for p in params]
    assert 0.83 in params or "0.83" in [str(p) for p in params]


def test_params_align_with_declared_column_order():
    """Column list and params must agree, or values land in wrong columns."""
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=7, rows=[_row()], update_existing=False)

    sql, params = conn.statements[0]
    cols = sql.split("stock_ohlcv", 1)[1].split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in cols.split(",")]

    assert len(names) == len(params), (names, params)
    assert params[names.index("split_coefficient")] == 3.0
    assert params[names.index("dividend_amount")] == 0.83
    assert params[names.index("close")] == 59.60


# --------------------------------------------------------------------------- #
# Backfill depends on the conflict clause.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("column", ["split_coefficient", "dividend_amount"])
def test_update_existing_backfills_the_column(column):
    """Re-ingesting history is how the 29.2M NULL rows get repaired. Without
    these in DO UPDATE SET, a re-ingest silently leaves them NULL forever."""
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[_row()], update_existing=True)

    sql = _sql_of(conn)
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert f"{column} = EXCLUDED.{column}" in update_clause


def test_without_update_existing_conflicts_do_nothing():
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[_row()], update_existing=False)

    assert "DO NOTHING" in _sql_of(conn)


# --------------------------------------------------------------------------- #
# Absent is absent -- never a plausible-looking number.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("column,idx_name", [
    ("split_coefficient", "split_coefficient"),
    ("dividend_amount", "dividend_amount"),
])
def test_absent_field_stores_null_not_zero(column, idx_name):
    """A payload without the field means "unknown", not "no split" (1.0) and
    not "no dividend" (0.0). Per failure semantics, absence stays absent."""
    row = _row()
    del row[column]
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[row], update_existing=False)

    sql, params = conn.statements[0]
    cols = sql.split("stock_ohlcv", 1)[1].split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in cols.split(",")]

    assert params[names.index(idx_name)] is None


def test_empty_rows_issue_no_statement():
    conn = _Conn()

    assert insert_stock_ohlcv(conn, data_id=1, rows=[],
                                update_existing=False) == 0
    assert conn.statements == []


def test_date_string_is_parsed():
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[_row()], update_existing=False)

    assert date(2024, 2, 26) in _params_of(conn)


def test_unparseable_date_is_skipped_not_crashed():
    """`_batch_insert_prices` skipped rows whose date string wouldn't parse
    instead of crashing. The bulk path feeds ISO strings from
    `parse_daily_adjusted`, so the converged writer must keep this behavior."""
    conn = _Conn()

    inserted = insert_stock_ohlcv(
        conn, data_id=1, rows=[_row(date="not-a-date")], update_existing=False
    )

    assert inserted == 0
    assert conn.statements == []


# --------------------------------------------------------------------------- #
# Corruption guard (>= 1e12) must survive the merge.
# --------------------------------------------------------------------------- #
def test_corrupt_adjusted_close_over_1e12_stored_as_null():
    """Serial reverse-splitters corrupt adjusted_close (production holds
    values like SMX at $3,452,451,366). The guard trips at 1e12; scale a
    realistic corrupt value up to that magnitude to exercise it."""
    row = _row(adjusted_close=3_452_451_366_780.78)
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[row], update_existing=False)

    sql, params = conn.statements[0]
    cols = sql.split("stock_ohlcv", 1)[1].split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in cols.split(",")]

    assert params[names.index("adjusted_close")] is None


def test_value_just_under_1e12_is_kept():
    row = _row(adjusted_close=999_999_999_999.0)
    conn = _Conn()

    insert_stock_ohlcv(conn, data_id=1, rows=[row], update_existing=False)

    sql, params = conn.statements[0]
    cols = sql.split("stock_ohlcv", 1)[1].split("(", 1)[1].split(")", 1)[0]
    names = [c.strip() for c in cols.split(",")]

    assert params[names.index("adjusted_close")] == 999_999_999_999.0


# --------------------------------------------------------------------------- #
# Guard against future re-divergence (this task's own regression test).
# --------------------------------------------------------------------------- #
def test_only_one_stock_ohlcv_batch_writer_in_source():
    """Only one function may build the dynamic multi-row INSERT INTO
    stock_ohlcv column list from parsed provider rows. (`migrate.py`'s
    one-time legacy-table copy and `timescale.py`'s chunk-creation dummy
    insert are distinct, static SQL for unrelated purposes -- not this
    writer -- so this checks for the specific literal those writers share,
    not every mention of the table name.)"""
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "gefion"
    marker = '"INSERT INTO stock_ohlcv "'

    hits = sorted(
        path.relative_to(root)
        for path in root.rglob("*.py")
        if marker in path.read_text()
    )

    assert hits == [pathlib.Path("db/ingest.py")], hits
