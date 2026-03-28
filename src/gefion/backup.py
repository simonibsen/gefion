"""Database backup and restore functionality.

Supports:
- Full and incremental backups
- Date range and symbol filtering
- Multiple data types (ohlcv, features, definitions, functions)
- Parquet format with optional compression
- Size estimation and disk space checking
"""

import json
import hashlib
import shutil
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from gefion.observability import create_span, set_attributes


# Average bytes per row estimates for size calculation
BYTES_PER_ROW = {
    "stock_ohlcv": 120,  # date, open, high, low, close, volume, adjusted, etc.
    "computed_features": 60,  # data_id, date, feature_id, value
    "feature_definitions": 500,  # JSON params, text fields
    "feature_functions": 2000,  # function_body can be large
    "stocks": 200,  # symbol, name, sector, industry, etc.
    "strategy_registry": 500,
    "strategy_configs": 300,
    "ml_datasets": 500,
    "ml_runs": 1000,
    "ml_models": 500,
    "predictions": 150,
    "prediction_outcomes": 80,
    "model_performance": 200,
    "volatility_thresholds": 100,
    "experiments": 2000,
    "experiment_trials": 500,
    "schema_migrations": 100,
}

# Data type to table mapping
DATA_TYPE_TABLES = {
    "ohlcv": ["stocks", "stock_ohlcv"],
    "features": ["computed_features"],
    "definitions": ["feature_definitions"],
    "functions": ["feature_functions"],
    "strategies": ["strategy_registry", "strategy_configs"],
    "ml": ["ml_datasets", "ml_runs", "ml_models"],
    "predictions": ["predictions", "prediction_outcomes", "model_performance"],
    "experiments": ["experiments", "experiment_trials"],
    "meta": ["schema_migrations", "volatility_thresholds"],
    "all": [
        "stocks", "stock_ohlcv", "computed_features", "feature_definitions", "feature_functions",
        "strategy_registry", "strategy_configs",
        "ml_datasets", "ml_runs", "ml_models",
        "predictions", "prediction_outcomes", "model_performance",
        "volatility_thresholds", "experiments", "experiment_trials",
        "schema_migrations",
    ],
}


def estimate_backup_size(
    conn,
    data_types: List[str],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Estimate the size of a backup without creating it.

    Args:
        conn: Database connection
        data_types: List of data types to include ('ohlcv', 'features', 'definitions', 'functions', 'all')
        start_date: Optional start date filter
        end_date: Optional end date filter
        symbols: Optional list of symbols to filter

    Returns:
        Dict with table row counts and estimated sizes
    """
    # Resolve data types to tables
    tables = set()
    for dt in data_types:
        tables.update(DATA_TYPE_TABLES.get(dt, []))

    with create_span("backup.estimate_backup_size", data_types=str(data_types)) as span:
        result = {"tables": {}, "total_rows": 0, "total_bytes": 0}

        with conn.cursor() as cur:
            for table in tables:
                # Check if table exists
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = %s
                    )
                """, (table,))
                if not cur.fetchone()[0]:
                    # Table doesn't exist, skip it
                    continue

                # Build count query with filters
                query = f"SELECT COUNT(*) FROM {table}"
                params = []
                conditions = []

                if table in ("stock_ohlcv", "computed_features") and (start_date or end_date):
                    if start_date:
                        conditions.append("date >= %s")
                        params.append(start_date)
                    if end_date:
                        conditions.append("date <= %s")
                        params.append(end_date)

                if symbols and table in ("stocks",):
                    placeholders = ",".join(["%s"] * len(symbols))
                    conditions.append(f"symbol IN ({placeholders})")
                    params.extend(symbols)
                elif symbols and table in ("stock_ohlcv", "computed_features"):
                    # Need to join with stocks to filter by symbol
                    if table == "stock_ohlcv":
                        query = """
                            SELECT COUNT(*) FROM stock_ohlcv o
                            JOIN stocks s ON o.data_id = s.id
                        """
                    else:
                        query = """
                            SELECT COUNT(*) FROM computed_features cf
                            JOIN stocks s ON cf.data_id = s.id
                        """
                    placeholders = ",".join(["%s"] * len(symbols))
                    conditions.append(f"s.symbol IN ({placeholders})")
                    params.extend(symbols)

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                cur.execute(query, params)
                row_count = cur.fetchone()[0]

                estimated_bytes = row_count * BYTES_PER_ROW.get(table, 100)

                result["tables"][table] = {
                    "rows": row_count,
                    "estimated_bytes": estimated_bytes,
                }
                result["total_rows"] += row_count
                result["total_bytes"] += estimated_bytes

        set_attributes(span, total_rows=result["total_rows"], total_bytes=result["total_bytes"], table_count=len(result["tables"]))
        return result


def check_disk_space(path: str, required_bytes: int) -> bool:
    """Check if enough disk space is available at the given path.

    Args:
        path: Directory path to check
        required_bytes: Required space in bytes

    Returns:
        True if enough space is available
    """
    try:
        # Get the directory (create if needed for the check)
        check_path = Path(path)
        if check_path.is_file():
            check_path = check_path.parent

        # Find an existing parent directory
        while not check_path.exists():
            check_path = check_path.parent
            if check_path == check_path.parent:
                return False  # Reached root without finding existing dir

        usage = shutil.disk_usage(str(check_path))
        # Add 10% buffer
        return usage.free >= required_bytes * 1.1
    except Exception:
        return False


def create_manifest(
    tables: Dict[str, Dict],
    date_range: Optional[Tuple[str, str]] = None,
    symbols: Optional[List[str]] = None,
    incremental_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a backup manifest with metadata.

    Args:
        tables: Dict of table info {name: {rows, checksum, ...}}
        date_range: Optional (start_date, end_date) tuple
        symbols: Optional list of symbols included
        incremental_from: Date of last backup if incremental

    Returns:
        Manifest dict
    """
    return {
        "version": "1.0",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "date_range": date_range,
        "symbols": symbols,
        "incremental_from": incremental_from,
        "tables": tables,
    }


def get_last_backup_info(backup_dir: str) -> Optional[Dict[str, Any]]:
    """Get info about the last backup in a directory.

    Args:
        backup_dir: Directory to search for backups

    Returns:
        Dict with last backup info or None if no backups found
    """
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        return None

    # Look for manifest files
    manifests = list(backup_path.glob("**/manifest.json"))
    if not manifests:
        return None

    # Find most recent
    latest = None
    latest_date = None

    for manifest_path in manifests:
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
                created = manifest.get("created_at")
                if created:
                    if latest_date is None or created > latest_date:
                        latest_date = created
                        latest = {
                            "path": str(manifest_path.parent),
                            "manifest": manifest,
                            "created_at": created,
                        }
        except Exception:
            continue

    return latest


def _compute_checksum(data: bytes) -> str:
    """Compute SHA256 checksum of data."""
    return hashlib.sha256(data).hexdigest()[:16]


def create_backup(
    conn,
    output_path: str,
    data_types: List[str],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    symbols: Optional[List[str]] = None,
    incremental: bool = False,
    last_backup_date: Optional[str] = None,
    compress: bool = True,
    progress_callback: Optional[callable] = None,
) -> Dict[str, Any]:
    """Create a backup of selected data.

    Args:
        conn: Database connection
        output_path: Output directory path
        data_types: List of data types to backup
        start_date: Optional start date filter
        end_date: Optional end date filter
        symbols: Optional list of symbols to filter
        incremental: If True, only backup data since last backup
        last_backup_date: Date to use for incremental (ISO format)
        compress: Whether to compress parquet files
        progress_callback: Optional callback(table, rows_done, rows_total)

    Returns:
        Dict with backup results
    """
    with create_span("backup.create_backup", output_path=output_path, data_types=str(data_types), incremental=incremental) as span:
        output_dir = Path(output_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Resolve tables
        tables = set()
        for dt in data_types:
            tables.update(DATA_TYPE_TABLES.get(dt, []))

        results = {"tables": {}, "total_rows": 0, "total_bytes": 0}

        # For incremental, adjust start_date
        if incremental and last_backup_date:
            inc_date = datetime.fromisoformat(last_backup_date.replace("Z", "")).date()
            if start_date is None or inc_date > start_date:
                start_date = inc_date

        with conn.cursor() as cur:
            for table in sorted(tables):
                # Check if table exists
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = %s
                    )
                """, (table,))
                if not cur.fetchone()[0]:
                    # Table doesn't exist, skip it
                    continue

                table_result = _backup_table(
                    cur=cur,
                    table=table,
                    output_dir=output_dir,
                    start_date=start_date,
                    end_date=end_date,
                    symbols=symbols,
                    compress=compress,
                    progress_callback=progress_callback,
                )

                results["tables"][table] = table_result
                results["total_rows"] += table_result.get("rows", 0)
                results["total_bytes"] += table_result.get("bytes", 0)

        # Write manifest
        date_range = None
        if start_date or end_date:
            date_range = (
                str(start_date) if start_date else None,
                str(end_date) if end_date else None,
            )

        manifest = create_manifest(
            tables=results["tables"],
            date_range=date_range,
            symbols=symbols,
            incremental_from=last_backup_date if incremental else None,
        )

        manifest_path = output_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        results["manifest_path"] = str(manifest_path)
        results["success"] = True

        set_attributes(span, total_rows=results["total_rows"], total_bytes=results["total_bytes"], table_count=len(results["tables"]))
        return results


def _backup_table(
    cur,
    table: str,
    output_dir: Path,
    start_date: Optional[date],
    end_date: Optional[date],
    symbols: Optional[List[str]],
    compress: bool,
    progress_callback: Optional[callable],
) -> Dict[str, Any]:
    """Backup a single table to parquet.

    Returns dict with rows, bytes, checksum.
    """
    # Build query
    if table == "stocks":
        query = "SELECT * FROM stocks"
        params = []
        if symbols:
            placeholders = ",".join(["%s"] * len(symbols))
            query += f" WHERE symbol IN ({placeholders})"
            params = symbols
    elif table == "stock_ohlcv":
        if symbols:
            query = """
                SELECT o.* FROM stock_ohlcv o
                JOIN stocks s ON o.data_id = s.id
            """
            conditions = []
            params = []
            placeholders = ",".join(["%s"] * len(symbols))
            conditions.append(f"s.symbol IN ({placeholders})")
            params.extend(symbols)
            if start_date:
                conditions.append("o.date >= %s")
                params.append(start_date)
            if end_date:
                conditions.append("o.date <= %s")
                params.append(end_date)
            query += " WHERE " + " AND ".join(conditions)
        else:
            query = "SELECT * FROM stock_ohlcv"
            params = []
            conditions = []
            if start_date:
                conditions.append("date >= %s")
                params.append(start_date)
            if end_date:
                conditions.append("date <= %s")
                params.append(end_date)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
    elif table == "computed_features":
        if symbols:
            query = """
                SELECT cf.* FROM computed_features cf
                JOIN stocks s ON cf.data_id = s.id
            """
            conditions = []
            params = []
            placeholders = ",".join(["%s"] * len(symbols))
            conditions.append(f"s.symbol IN ({placeholders})")
            params.extend(symbols)
            if start_date:
                conditions.append("cf.date >= %s")
                params.append(start_date)
            if end_date:
                conditions.append("cf.date <= %s")
                params.append(end_date)
            query += " WHERE " + " AND ".join(conditions)
        else:
            query = "SELECT * FROM computed_features"
            params = []
            conditions = []
            if start_date:
                conditions.append("date >= %s")
                params.append(start_date)
            if end_date:
                conditions.append("date <= %s")
                params.append(end_date)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
    else:
        # feature_definitions, feature_functions - no date/symbol filter
        query = f"SELECT * FROM {table}"
        params = []

    # Execute and fetch
    cur.execute(query, params)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    if not rows:
        return {"rows": 0, "bytes": 0, "checksum": None}

    # Convert to PyArrow table
    # Handle special types (dates, JSON, etc.)
    data = {}
    for i, col in enumerate(columns):
        col_data = [row[i] for row in rows]
        # Convert dates to strings for parquet compatibility
        if col_data and isinstance(col_data[0], (date, datetime)):
            col_data = [str(v) if v else None for v in col_data]
        # Convert dicts/lists to JSON strings
        elif col_data and isinstance(col_data[0], (dict, list)):
            col_data = [json.dumps(v) if v else None for v in col_data]
        data[col] = col_data

    arrow_table = pa.table(data)

    # Write to parquet
    output_file = output_dir / f"{table}.parquet"
    compression = "gzip" if compress else None
    pq.write_table(arrow_table, output_file, compression=compression)

    # Get file size and checksum
    file_bytes = output_file.stat().st_size
    with open(output_file, "rb") as f:
        checksum = _compute_checksum(f.read())

    if progress_callback:
        progress_callback(table, len(rows), len(rows))

    return {
        "rows": len(rows),
        "bytes": file_bytes,
        "checksum": checksum,
        "file": str(output_file),
    }


def restore_backup(
    conn,
    input_path: str,
    mode: str = "merge",
    data_types: Optional[List[str]] = None,
    progress_callback: Optional[callable] = None,
) -> Dict[str, Any]:
    """Restore data from a backup.

    Args:
        conn: Database connection
        input_path: Path to backup directory
        mode: 'merge' (skip conflicts) or 'replace' (overwrite)
        data_types: Optional filter for which data types to restore
        progress_callback: Optional callback(table, rows_done, rows_total)

    Returns:
        Dict with restore results
    """
    with create_span("backup.restore_backup", input_path=input_path, mode=mode) as span:
        input_dir = Path(input_path)

        # Read manifest
        manifest_path = input_dir / "manifest.json"
        if not manifest_path.exists():
            return {"success": False, "error": "No manifest.json found"}

        with open(manifest_path) as f:
            manifest = json.load(f)

        results = {"tables": {}, "total_rows": 0, "mode": mode}

        # Determine tables to restore
        tables_in_backup = list(manifest.get("tables", {}).keys())

        if data_types:
            # Filter to requested types
            allowed_tables = set()
            for dt in data_types:
                allowed_tables.update(DATA_TYPE_TABLES.get(dt, []))
            tables_to_restore = [t for t in tables_in_backup if t in allowed_tables]
        else:
            tables_to_restore = tables_in_backup

        # Restore order matters (parent tables before children with foreign keys)
        restore_order = [
            "schema_migrations",  # Meta first
            "stocks",  # Base data
            "feature_definitions", "feature_functions",
            "stock_ohlcv", "computed_features",
            "strategy_registry", "strategy_configs",
            "ml_datasets", "ml_models", "ml_runs",
            "predictions",
            "prediction_outcomes", "model_performance",
            "volatility_thresholds",
            "experiments", "experiment_trials",
        ]
        tables_to_restore = sorted(tables_to_restore, key=lambda t: restore_order.index(t) if t in restore_order else 99)

        with conn.cursor() as cur:
            for table in tables_to_restore:
                parquet_file = input_dir / f"{table}.parquet"
                if not parquet_file.exists():
                    # Check if this was an empty table (no file expected)
                    table_info = manifest.get("tables", {}).get(table, {})
                    if table_info.get("rows", 0) == 0:
                        results["tables"][table] = {"rows_restored": 0, "rows_skipped": 0}
                        continue
                    results["tables"][table] = {"error": "File not found", "rows": 0}
                    continue

                table_result = _restore_table(
                    cur=cur,
                    conn=conn,
                    table=table,
                    parquet_file=parquet_file,
                    mode=mode,
                    progress_callback=progress_callback,
                )

                results["tables"][table] = table_result
                results["total_rows"] += table_result.get("rows_restored", 0)

        results["success"] = True
        set_attributes(span, total_rows=results["total_rows"], table_count=len(results["tables"]))
        return results


def _restore_table(
    cur,
    conn,
    table: str,
    parquet_file: Path,
    mode: str,
    progress_callback: Optional[callable],
) -> Dict[str, Any]:
    """Restore a single table from parquet."""
    # Read parquet
    arrow_table = pq.read_table(parquet_file)
    df = arrow_table.to_pandas()

    if df.empty:
        return {"rows_restored": 0, "rows_skipped": 0}

    rows_restored = 0
    rows_skipped = 0

    # Get column names
    columns = list(df.columns)

    # Build insert query based on mode
    # Define conflict columns for each table (unique constraints)
    conflict_map = {
        "stocks": ["symbol"],
        "stock_ohlcv": ["data_id", "date"],
        "computed_features": ["feature_id", "data_id", "date"],
        "feature_definitions": ["name"],
        "feature_functions": ["name", "version"],
        "strategy_registry": ["name"],
        "strategy_configs": ["name"],
        "ml_datasets": ["name", "version"],
        "ml_models": ["name", "version"],
        "ml_runs": ["id"],
        "predictions": ["model_id", "data_id", "prediction_date", "horizon_days", "prediction_type"],
        "prediction_outcomes": ["prediction_id"],
        "model_performance": ["model_id", "evaluation_date", "horizon_days"],
        "volatility_thresholds": ["symbol", "horizon_days", "calculated_date"],
        "experiments": ["id"],
        "experiment_trials": ["id"],
        "schema_migrations": ["version"],
    }

    if mode == "replace":
        # Use ON CONFLICT DO UPDATE
        conflict_cols = conflict_map.get(table, ["id"])

        update_cols = [c for c in columns if c not in conflict_cols and c != "id"]
        update_clause = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])

        placeholders = ", ".join(["%s"] * len(columns))
        insert_cols = ", ".join(columns)

        if update_clause:
            query = f"""
                INSERT INTO {table} ({insert_cols})
                VALUES ({placeholders})
                ON CONFLICT ({", ".join(conflict_cols)}) DO UPDATE SET {update_clause}
            """
        else:
            query = f"""
                INSERT INTO {table} ({insert_cols})
                VALUES ({placeholders})
                ON CONFLICT ({", ".join(conflict_cols)}) DO NOTHING
            """
    else:
        # Merge mode - skip conflicts
        conflict_cols = conflict_map.get(table, ["id"])

        placeholders = ", ".join(["%s"] * len(columns))
        insert_cols = ", ".join(columns)
        query = f"""
            INSERT INTO {table} ({insert_cols})
            VALUES ({placeholders})
            ON CONFLICT ({", ".join(conflict_cols)}) DO NOTHING
        """

    # Insert rows in batches
    batch_size = 1000
    total_rows = len(df)

    for i in range(0, total_rows, batch_size):
        batch = df.iloc[i:i + batch_size]

        for _, row in batch.iterrows():
            # Convert row values, handling JSON fields
            values = []
            for col in columns:
                val = row[col]
                # Handle NaN/None
                if val is None or (isinstance(val, float) and str(val) == "nan"):
                    values.append(None)
                # Handle JSON string fields that need to be dicts
                elif col in ("params", "param_schema", "defaults", "dependencies", "inputs", "source_tables", "source_columns"):
                    if isinstance(val, str):
                        try:
                            values.append(json.loads(val))
                        except Exception:
                            values.append(val)
                    else:
                        values.append(val)
                else:
                    values.append(val)

            try:
                cur.execute(query, values)
                if cur.rowcount > 0:
                    rows_restored += 1
                else:
                    rows_skipped += 1
            except Exception as e:
                rows_skipped += 1

        conn.commit()

        if progress_callback:
            progress_callback(table, min(i + batch_size, total_rows), total_rows)

    return {"rows_restored": rows_restored, "rows_skipped": rows_skipped}


def verify_backup(backup_path: str) -> Dict[str, Any]:
    """Verify integrity of a backup.

    Args:
        backup_path: Path to backup directory

    Returns:
        Dict with verification results
    """
    with create_span("backup.verify_backup", backup_path=backup_path) as span:
        backup_dir = Path(backup_path)

        if not backup_dir.exists():
            return {"valid": False, "error": "Backup path does not exist"}

        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            return {"valid": False, "error": "No manifest.json found"}

        with open(manifest_path) as f:
            manifest = json.load(f)

        results = {"valid": True, "tables": {}}

        for table, info in manifest.get("tables", {}).items():
            parquet_file = backup_dir / f"{table}.parquet"

            # Empty tables don't have files - that's valid
            expected_rows = info.get("rows", 0)
            if not parquet_file.exists():
                if expected_rows == 0:
                    results["tables"][table] = {"valid": True, "rows": 0, "expected_rows": 0}
                    continue
                results["tables"][table] = {"valid": False, "error": "File missing"}
                results["valid"] = False
                continue

            # Verify checksum if present
            expected_checksum = info.get("checksum")
            if expected_checksum:
                with open(parquet_file, "rb") as f:
                    actual_checksum = _compute_checksum(f.read())

                if actual_checksum != expected_checksum:
                    results["tables"][table] = {
                        "valid": False,
                        "error": f"Checksum mismatch: expected {expected_checksum}, got {actual_checksum}",
                    }
                    results["valid"] = False
                    continue

            # Try to read the parquet file
            try:
                arrow_table = pq.read_table(parquet_file)
                row_count = arrow_table.num_rows
                results["tables"][table] = {
                    "valid": True,
                    "rows": row_count,
                    "expected_rows": info.get("rows"),
                }
            except Exception as e:
                results["tables"][table] = {"valid": False, "error": str(e)}
                results["valid"] = False

        set_attributes(span, valid=results["valid"], table_count=len(results["tables"]))
        return results
