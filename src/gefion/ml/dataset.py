from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes

DEFAULT_SYMBOL_BATCH_SIZE = 200
"""Symbols processed per chunk while exporting prices/labels (#209). Bounds
peak memory to roughly one batch's price history regardless of universe
size — a NYSE-scale build (3000+ symbols, multi-year window) no longer
holds the whole universe's price/label data in memory at once.

There is no central per-host resource budget (#205) wired into this module
to derive a batch size automatically. Override per-build via
manifest['symbol_batch_size'] (or --symbol-batch-size on the CLI) if a
host's memory profile calls for something smaller or larger.
"""

DEFAULT_MAX_BATCH_PRICE_ROWS = 3_000_000
"""Guardrail (#209): refuse a build whose estimated per-batch row count
would still exceed this even after chunking — e.g. a single symbol's own
price history is pathologically long, so no batch_size can help. Fail with
guidance rather than invite the OOM killer. Override via
manifest['max_batch_price_rows'].
"""


class DatasetBuildTooLargeError(ValueError):
    """Even a single symbol-batch would exceed the row guardrail — narrow
    --start-date/--end-date, or lower --symbol-batch-size, and retry."""


def _write_to_file(
    data: List[Dict[str, Any]], path: Path, header: List[str], format: str = "csv"
) -> None:
    """Helper to write data in CSV or Parquet format."""
    if not data:
        # Write empty file with header
        if format == "parquet":
            import pandas as pd

            pd.DataFrame(columns=header).to_parquet(path, index=False)
        else:
            with path.open("w", newline="") as f:
                csv.writer(f).writerow(header)
        return

    if format == "parquet":
        import pandas as pd

        df = pd.DataFrame(data)
        df.to_parquet(path, index=False)
    else:  # csv
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(data)


def _stream_to_csv(cursor, path: Path, header: List[str], row_mapper) -> int:
    """Stream cursor results directly to CSV without loading all into memory.

    Returns the number of rows written.
    """
    count = 0
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in cursor:
            writer.writerow(row_mapper(row))
            count += 1
    return count


def _check_batch_feasibility(conn, symbols: List[str], *, start_date, end_date,
                             batch_size: int, max_batch_rows: int) -> None:
    """Estimate price rows per symbol-batch up front and refuse (rather than
    let the OOM killer find out) if even one batch cannot fit (#209).

    Runs a single COUNT(*) scoped to the same WHERE clause as the real price
    export. Deliberately called OUTSIDE any broad try/except in the caller —
    a refusal here must propagate, never degrade into a silent empty-file
    "success".
    """
    if not symbols:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM stocks s
            JOIN stock_ohlcv o ON o.data_id = s.id
            WHERE s.symbol = ANY(%s)
              AND (%s::date IS NULL OR o.date <= %s)
              AND (%s::date IS NULL OR o.date >= %s);
            """,
            (list(symbols), end_date, end_date, start_date, start_date),
        )
        rows = cur.fetchall()
    total_rows = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
    if not total_rows:
        return
    avg_rows_per_symbol = total_rows / len(symbols)
    estimated_batch_rows = avg_rows_per_symbol * batch_size
    if estimated_batch_rows <= max_batch_rows:
        return
    if avg_rows_per_symbol > max_batch_rows:
        raise DatasetBuildTooLargeError(
            f"Even a single symbol averages ~{avg_rows_per_symbol:,.0f} price "
            f"rows across {len(symbols):,} symbols ({total_rows:,} total) — "
            f"over the {max_batch_rows:,}-row symbol-batch guardrail. "
            "Chunking cannot help; narrow --start-date/--end-date to shrink "
            "the window."
        )
    raise DatasetBuildTooLargeError(
        f"Estimated ~{estimated_batch_rows:,.0f} rows for a {batch_size}-"
        f"symbol batch (~{avg_rows_per_symbol:,.0f} rows/symbol across "
        f"{total_rows:,} total rows) exceeds the {max_batch_rows:,}-row "
        "guardrail. Lower --symbol-batch-size, or narrow "
        "--start-date/--end-date to shrink the window."
    )


def _iter_symbol_batches(symbols: List[str], batch_size: int):
    """Yield symbols in fixed-size chunks — chunking by SYMBOL, never by
    date. Labels come from a forward shift within each symbol's own series
    (see _compute_batch_labels), so every symbol's full history must stay in
    one batch; date-chunking would truncate the forward window at each seam.
    """
    step = max(1, batch_size)
    for i in range(0, len(symbols), step):
        yield list(symbols[i:i + step])


def _valid_horizons(horizons_days: List[int], thresholds: Dict[str, Any]) -> List[int]:
    """Horizons whose weak/strong thresholds are both positive — matches the
    original per-horizon guard in the label loop."""
    valid = []
    for h in horizons_days:
        h = int(h)
        t = thresholds.get(str(h)) or {}
        if float(t.get("weak", 0.0)) > 0 and float(t.get("strong", 0.0)) > 0:
            valid.append(h)
    return valid


def _fetch_price_batch_df(conn, batch_symbols: Optional[List[str]], *,
                          start_date, end_date, header: List[str]):
    """Fetch one batch's price rows as a DataFrame (bounded to this batch).

    ``batch_symbols=None`` preserves the legacy unfiltered fallback (no
    universe resolved to any symbols) as a single unbounded query — the
    same behavior it always had, just routed through the shared batch loop.
    """
    import pandas as pd

    with conn.cursor() as cur:
        if batch_symbols:
            cur.execute(
                """
                SELECT s.symbol, o.date, o.open, o.high, o.low, o.close, o.adjusted_close, o.volume
                FROM stocks s
                JOIN stock_ohlcv o ON o.data_id = s.id
                WHERE s.symbol = ANY(%s)
                  AND (%s::date IS NULL OR o.date <= %s)
                  AND (%s::date IS NULL OR o.date >= %s)
                ORDER BY s.symbol, o.date;
                """,
                (list(batch_symbols), end_date, end_date, start_date, start_date),
            )
        else:
            cur.execute(
                """
                SELECT s.symbol, o.date, o.open, o.high, o.low, o.close, o.adjusted_close, o.volume
                FROM stocks s
                JOIN stock_ohlcv o ON o.data_id = s.id
                WHERE (%s::date IS NULL OR o.date <= %s)
                  AND (%s::date IS NULL OR o.date >= %s)
                ORDER BY s.symbol, o.date;
                """,
                (end_date, end_date, start_date, start_date),
            )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=header)


def _compute_batch_labels(price_df, horizons_days: List[int],
                          thresholds: Dict[str, Any], labels_header: List[str]):
    """Forward-return labels for ONE batch's price rows.

    Identical math to the original whole-universe computation — a
    per-symbol groupby().shift() is blind to which OTHER symbols are (or
    aren't) present in the same call, so computing it one symbol-batch at a
    time is exactly equivalent to computing it once over everything.
    """
    import pandas as pd

    df = price_df.copy()
    df["close_for_label"] = df["adjusted_close"].where(df["adjusted_close"].notna(), df["close"])
    df["close_for_label"] = pd.to_numeric(df["close_for_label"], errors="coerce")
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    out = []
    for h in horizons_days:
        h = int(h)
        t = thresholds.get(str(h)) or {}
        weak = float(t.get("weak", 0.0))
        strong = float(t.get("strong", 0.0))
        if weak <= 0 or strong <= 0:
            continue
        shifted = df.groupby("symbol")["close_for_label"].shift(-h)
        ret = (shifted / df["close_for_label"]) - 1.0

        labels = pd.Series(index=ret.index, dtype=object)
        valid_mask = ret.notna() & (ret.abs() != float("inf"))
        labels[~valid_mask] = None
        labels[valid_mask & (ret <= -strong)] = "strong_down"
        labels[valid_mask & (ret > -strong) & (ret <= -weak)] = "weak_down"
        labels[valid_mask & (ret > -weak) & (ret < weak)] = "flat"
        labels[valid_mask & (ret >= weak) & (ret < strong)] = "weak_up"
        labels[valid_mask & (ret >= strong)] = "strong_up"

        out.append(pd.DataFrame({
            "symbol": df["symbol"],
            "date": df["date"],
            "horizon_days": h,
            "forward_return": ret,
            "label": labels,
        }))

    if not out:
        return pd.DataFrame(columns=labels_header)
    labels_df = pd.concat(out, ignore_index=True)
    return labels_df.dropna(subset=["forward_return", "label"])


def _accumulate_grid_labels(grid_labels: Dict[Any, float], batch_labels_df) -> None:
    """Fold one batch's labels into the running (symbol, date) -> mean
    forward_return grid the coverage audit needs (#191).

    Each grid key belongs to exactly one symbol, and every symbol lives in
    exactly one batch, so this is a plain accumulation — no cross-batch
    merge logic required. Keeping only this compact grid (instead of the
    full per-horizon labels frame) is what keeps the coverage audit off the
    (H+1)x-materialization blowup this ticket removes from the write path,
    without requiring the audit itself to change (see #209 decisions.md).
    """
    if batch_labels_df is None or batch_labels_df.empty:
        return
    tmp = batch_labels_df[["symbol", "date", "forward_return"]].copy()
    tmp["date"] = tmp["date"].astype(str)
    grouped = tmp.groupby(["symbol", "date"])["forward_return"].mean()
    for key, val in grouped.items():
        grid_labels[key] = float(val)


class _CsvBatchWriter:
    """Streams DataFrame batches to CSV: header written once, each batch
    appended thereafter. Never holds more than one batch in memory."""

    def __init__(self, path: Path, header: List[str]):
        import pandas as pd

        self._path = path
        self._header = header
        pd.DataFrame(columns=header).to_csv(path, index=False)

    def write_df(self, df) -> None:
        if df is None or df.empty:
            return
        df.to_csv(self._path, mode="a", header=False, index=False,
                  columns=self._header)

    def close(self) -> None:
        pass


class _ParquetBatchWriter:
    """Streams DataFrame batches to Parquet via one open ParquetWriter (row
    groups per batch) so the schema is written once and no batch's rows are
    ever combined with another's in memory."""

    def __init__(self, path: Path, header: List[str]):
        self._path = path
        self._header = header
        self._writer = None

    def write_df(self, df) -> None:
        if df is None or df.empty:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df[self._header], preserve_index=False)
        if self._writer is None:
            self._writer = pq.ParquetWriter(str(self._path), table.schema)
        self._writer.write_table(table)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        else:
            import pandas as pd
            pd.DataFrame(columns=self._header).to_parquet(self._path, index=False)


def _open_batch_writer(export_format: str, path: Path, header: List[str]):
    if export_format == "parquet":
        return _ParquetBatchWriter(path, header)
    return _CsvBatchWriter(path, header)


def resolve_universe_symbols(conn, universe: Dict[str, Any]) -> List[str]:
    """
    Resolve a manifest universe spec to a concrete symbol list.

    Explicit universe['symbols'] wins verbatim (documented bypass). Otherwise
    the base population is the modeling universe named by universe['universe']
    (default: the default universe; 'all' = unfiltered — spec 015), filtered
    by universe['exchange'] (stocks.exchange) and capped by universe['limit']
    when provided.
    """
    explicit = universe.get("symbols") or []
    if explicit:
        # Provenance stamp (spec 015): explicit lists are their own universe
        universe["universe_name"] = "explicit"
        universe["universe_fingerprint"] = None
        universe["resolved_count"] = len(explicit)
        return list(explicit)

    exchange = universe.get("exchange")
    limit = universe.get("limit")
    universe_name = universe.get("universe")

    with create_span("ml.dataset.resolve_universe_symbols",
                     exchange=exchange or "", limit=limit or 0,
                     universe=universe_name or "default") as span:
        from gefion.universe import resolve_universe, universe_exclusion_clause
        resolved = resolve_universe(conn, universe_name)
        clause, uparams = universe_exclusion_clause(
            resolved.universe_id, "CURRENT_DATE", "s.id")
        query = f"SELECT s.symbol FROM stocks s WHERE {clause}"
        params: List[Any] = list(uparams)
        if exchange:
            query += " AND s.exchange = %s"
            params.append(exchange)
        query += " ORDER BY s.symbol"
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        with conn.cursor() as cur:
            cur.execute(query + ";", params or None)
            symbols = [row[0] for row in cur.fetchall()]

        # Research universes are quality-filtered by default (spec 008): NASDAQ
        # test tickers never belong in a dataset. Explicit universe['symbols']
        # above bypasses this (the caller asked for exactly those).
        try:
            from gefion.quality.universe import exclude_test_tickers
            symbols = exclude_test_tickers(symbols)
        except Exception:  # pragma: no cover - defensive
            pass

        # Provenance stamp (spec 015): rides ml_datasets.universe JSONB —
        # results record exactly which population they were measured on
        universe.update(resolved.provenance())
        universe["resolved_count"] = len(symbols)

        set_attributes(span, symbol_count=len(symbols))
        return symbols


def export_dataset_artifacts(
    conn,
    *,
    manifest: Dict[str, Any],
    out_dir: Path,
    on_progress: Any = None,
) -> None:
    """
    Export dataset artifacts.

    Exports:
      - prices (stock_ohlcv)
      - features (computed_features joined to feature_definitions)
      - labels (forward returns + 5-class labels per horizon)

    Supports CSV (default) and Parquet formats via manifest['format'].

    Args:
        on_progress: Optional callback(message: str) for progress updates.
    """
    n_symbols = len(manifest.get("symbols", []))
    n_horizons = len(manifest.get("horizons", []))
    with create_span("ml.dataset_export", symbols=n_symbols, horizons=n_horizons,
                      format=manifest.get("format", "csv")):
        _export_dataset_artifacts_impl(conn, manifest=manifest, out_dir=out_dir,
                                        on_progress=on_progress)


def _run_coverage_audit(conn, *, manifest, symbols, labels_df,
                        feature_presence, on_progress=None) -> None:
    """Run the #191 coverage-bias audit over the assembled dataset.

    ``feature_presence`` is the in-memory feature matrix the export just
    assembled (feature name -> set of ``(symbol, date_str)`` present keys), so
    the audit computes coverage without re-scanning ``computed_features`` (#196).

    Advisory and strictly NON-BLOCKING: any failure is swallowed (logged via
    the progress callback) so it can never fail an otherwise-good build. Skips
    silently when the dataset has no name/version to stamp provenance against
    (the audit records into ``ml_datasets.universe`` keyed by name+version).
    """
    name = manifest.get("name")
    version = manifest.get("version")
    if not name or not version:
        return
    try:
        from gefion.ml.coverage import audit_dataset_coverage

        overrides = manifest.get("coverage_audit") or {}
        report = audit_dataset_coverage(
            conn, name=name, version=version, symbols=symbols,
            labels_df=labels_df, feature_presence=feature_presence,
            universe=manifest.get("universe"), **overrides)
        n_flagged = len(report.get("flagged", []))
        if on_progress:
            if n_flagged:
                on_progress(
                    f"⚠️  Coverage audit: {n_flagged} feature flag(s) — see "
                    "`gefion observations list` (non-blocking, review before "
                    "training)")
            else:
                on_progress("Coverage audit: no bias flagged")
    except Exception as e:  # pragma: no cover - defensive, must not block build
        if on_progress:
            on_progress(f"⚠️  Coverage audit skipped: {e}")


def _export_dataset_artifacts_impl(conn, *, manifest, out_dir, on_progress=None):
    def emit_progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine export format (default to CSV for backward compatibility)
    export_format = manifest.get("format", "csv").lower()
    file_ext = f".{export_format}"

    prices_path = out_dir / f"prices{file_ext}"
    features_path = out_dir / f"features{file_ext}"
    labels_path = out_dir / f"labels{file_ext}"

    prices_header = [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
    ]
    features_header = ["symbol", "date", "feature_name", "value"]
    labels_header = ["symbol", "date", "horizon_days", "forward_return", "label"]

    universe = manifest.get("universe") or {}
    symbols = universe.get("symbols") or []
    horizons_days = manifest.get("horizons_days") or []
    feature_names = manifest.get("feature_names") or []
    exclude_features = manifest.get("exclude_features") or []

    # Resolve the population when no explicit symbols were provided. Always
    # goes through the universe chokepoint (spec 015) — the old fallback
    # exported EVERY stock in the database, bypassing any filtering.
    if not symbols:
        symbols = resolve_universe_symbols(conn, universe)

    # Vintage bound (spec 012): a declared end_date is the TRAINING CUTOFF —
    # nothing after it may enter the dataset. Labels bound themselves: rows
    # whose forward window would cross the cutoff get null returns (prices
    # end there) and are dropped below.
    end_date = manifest.get("end_date")
    # Window start (spec 012 follow-up): a plain recency/size bound — no
    # causality weight, just "what the model saw" provenance.
    start_date = manifest.get("start_date")

    symbol_batch_size = max(1, int(manifest.get("symbol_batch_size") or DEFAULT_SYMBOL_BATCH_SIZE))
    max_batch_price_rows = int(manifest.get("max_batch_price_rows") or DEFAULT_MAX_BATCH_PRICE_ROWS)

    # Guardrail (#209): must run BEFORE the try/except below — a refusal
    # here must propagate, never get swallowed into a silent empty export.
    _check_batch_feasibility(conn, symbols, start_date=start_date, end_date=end_date,
                             batch_size=symbol_batch_size, max_batch_rows=max_batch_price_rows)

    thresholds = (manifest.get("label_spec") or {}).get("thresholds") or {}
    valid_horizons = _valid_horizons(horizons_days, thresholds)

    # Export prices AND compute labels together, batched by symbol (#209):
    # never more than one batch's price history in memory at a time, for
    # either CSV or Parquet. Chunking by symbol (not date) is what keeps
    # forward-return labels correct at every batch boundary — see
    # _iter_symbol_batches / _compute_batch_labels.
    emit_progress(f"Exporting prices for {len(symbols)} symbols...")
    price_count = 0
    labels_count = 0
    labels_error: Optional[Exception] = None
    grid_labels: Dict[Any, float] = {}

    price_writer: Any = None
    labels_writer: Any = None
    try:
        price_writer = _open_batch_writer(export_format, prices_path, prices_header)
        labels_writer = (_open_batch_writer(export_format, labels_path, labels_header)
                         if valid_horizons else None)

        batches = list(_iter_symbol_batches(symbols, symbol_batch_size)) if symbols else [None]
        for batch_symbols in batches:
            batch_df = _fetch_price_batch_df(conn, batch_symbols, start_date=start_date,
                                             end_date=end_date, header=prices_header)
            price_writer.write_df(batch_df)
            price_count += len(batch_df)

            if labels_writer is not None and labels_error is None and not batch_df.empty:
                try:
                    batch_labels_df = _compute_batch_labels(batch_df, valid_horizons,
                                                             thresholds, labels_header)
                except Exception as e:  # a bug in label math must not corrupt prices
                    labels_error = e
                    continue
                if not batch_labels_df.empty:
                    labels_writer.write_df(batch_labels_df)
                    labels_count += len(batch_labels_df)
                    _accumulate_grid_labels(grid_labels, batch_labels_df)

        price_writer.close()
        if labels_writer is not None:
            labels_writer.close()
    except Exception:
        for w in (price_writer, labels_writer):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
        price_count = 0
        labels_count = 0
        labels_error = None
        grid_labels = {}
        _write_to_file([], prices_path, prices_header, export_format)
        if valid_horizons:
            labels_path.unlink(missing_ok=True)

    emit_progress(f"Exported {price_count:,} price records")

    # Export features - stream directly for CSV, load for parquet
    emit_progress("Exporting features...")
    feature_rows: list[dict[str, Any]] = []
    feature_count = 0
    # Presence accumulated in the SAME pass that writes the feature matrix, so
    # the coverage audit (#191) reads it from memory instead of re-scanning the
    # computed_features hypertable a second time (#196). feature name -> set of
    # (symbol, date_str) where the feature is present (non-null).
    feature_presence: dict[str, set] = {}

    def _record_presence(mapped_row: dict[str, Any]) -> None:
        if mapped_row["value"] is not None:
            feature_presence.setdefault(mapped_row["feature_name"], set()).add(
                (mapped_row["symbol"], str(mapped_row["date"])))

    try:
        # First check if computed_features has any data
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM computed_features LIMIT 1")
            total_features = cur.fetchone()[0]
            if total_features == 0:
                emit_progress("⚠️  WARNING: computed_features table is empty. Run 'gefion feat-compute' first.")
    except Exception:
        pass  # Table might not exist yet

    try:
        with conn.cursor() as cur:
            # Build WHERE clause based on feature selection
            where_clauses = []
            params: list[Any] = []

            # Symbol filtering
            if symbols:
                where_clauses.append("s.symbol = ANY(%s)")
                params.append(list(symbols))

            # Feature filtering (whitelist mode: include only specified features)
            if feature_names:
                where_clauses.append("fd.name = ANY(%s)")
                params.append(list(feature_names))
            # Feature filtering (blacklist mode: exclude specified features)
            elif exclude_features:
                where_clauses.append("fd.name != ALL(%s)")
                params.append(list(exclude_features))

            where_clause = " AND ".join(where_clauses) if where_clauses else "TRUE"

            sql = f"""
                SELECT s.symbol, cf.date, fd.name, cf.value
                FROM computed_features cf
                JOIN feature_definitions fd ON fd.id = cf.feature_id
                JOIN stocks s ON s.id = cf.data_id
                WHERE {where_clause}
                  AND (%s::date IS NULL OR cf.date <= %s)
                  AND (%s::date IS NULL OR cf.date >= %s)
                ORDER BY s.symbol, cf.date, fd.name;
            """
            params.extend([end_date, end_date, start_date, start_date])

            if params:
                cur.execute(sql, tuple(params))
            else:
                cur.execute(sql)

            def feature_mapper(row):
                return {"symbol": row[0], "date": row[1], "feature_name": row[2], "value": row[3]}

            # Tap the single export pass to record presence for the audit.
            def feature_mapper_recording(row):
                mapped = feature_mapper(row)
                _record_presence(mapped)
                return mapped

            if export_format == "csv":
                # Stream directly to CSV - much lower memory usage
                feature_count = _stream_to_csv(cur, features_path, features_header, feature_mapper_recording)
            else:
                # For parquet, need all data in memory
                for row in cur:
                    mapped = feature_mapper_recording(row)
                    feature_rows.append(mapped)
                feature_count = len(feature_rows)
                _write_to_file(feature_rows, features_path, features_header, export_format)
    except Exception:
        _write_to_file([], features_path, features_header, export_format)

    if feature_count == 0:
        emit_progress("⚠️  WARNING: No features exported. Training will fail without features.")
    else:
        emit_progress(f"Features exported: {feature_count:,} records")

    # Labels were computed (or attempted) while streaming price batches above
    # — report the outcome and run the coverage audit now that feature
    # presence (just assembled by the features block) is also available.
    if horizons_days:
        if not valid_horizons:
            if price_count > 0:
                emit_progress("⚠️  WARNING: No labels computed. Check label_spec thresholds in manifest.")
        elif labels_error is not None:
            emit_progress(f"⚠️  WARNING: Failed to compute labels: {labels_error}")
        elif labels_count == 0:
            if price_count > 0:
                emit_progress("⚠️  WARNING: No labels computed (insufficient price history for horizons).")
        else:
            emit_progress(f"Labels computed: {labels_count:,} records")
            import pandas as pd

            grid_labels_df = pd.DataFrame(
                [(sym, dt, val) for (sym, dt), val in grid_labels.items()],
                columns=["symbol", "date", "forward_return"])
            # Coverage-bias audit (#191): the feature matrix and labels are
            # already assembled for this universe/date-range, so the audit
            # reads presence from memory (feature_presence, #196) rather
            # than re-scanning computed_features. grid_labels_df is the
            # compact (symbol, date) -> mean forward_return grid accumulated
            # batch-by-batch (#209) — NOT the full per-horizon frame — so
            # the audit doesn't reintroduce the memory blowup this ticket
            # removed from the write path. Advisory + NON-BLOCKING — never
            # fail the build.
            _run_coverage_audit(conn, manifest=manifest, symbols=symbols,
                                labels_df=grid_labels_df,
                                feature_presence=feature_presence,
                                on_progress=emit_progress)
