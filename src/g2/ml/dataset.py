from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List


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


def export_dataset_artifacts(conn, *, manifest: Dict[str, Any], out_dir: Path) -> None:
    """
    Export dataset artifacts.

    Exports:
      - prices (stock_ohlcv)
      - features (computed_features joined to feature_definitions)
      - labels (forward returns + 5-class labels per horizon)

    Supports CSV (default) and Parquet formats via manifest['format'].
    """
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

    price_rows: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cur:
            if symbols:
                cur.execute(
                    """
                    SELECT s.symbol, o.date, o.open, o.high, o.low, o.close, o.adjusted_close, o.volume
                    FROM stocks s
                    JOIN stock_ohlcv o ON o.data_id = s.id
                    WHERE s.symbol = ANY(%s)
                    ORDER BY s.symbol, o.date;
                    """,
                    (list(symbols),),
                )
            else:
                cur.execute(
                    """
                    SELECT s.symbol, o.date, o.open, o.high, o.low, o.close, o.adjusted_close, o.volume
                    FROM stocks s
                    JOIN stock_ohlcv o ON o.data_id = s.id
                    ORDER BY s.symbol, o.date;
                    """
                )
            for row in cur.fetchall():
                price_rows.append(
                    {
                        "symbol": row[0],
                        "date": row[1],
                        "open": row[2],
                        "high": row[3],
                        "low": row[4],
                        "close": row[5],
                        "adjusted_close": row[6],
                        "volume": row[7],
                    }
                )
    except Exception:
        price_rows = []

    _write_to_file(price_rows, prices_path, prices_header, export_format)

    feature_rows: list[dict[str, Any]] = []
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
                ORDER BY s.symbol, cf.date, fd.name;
            """

            if params:
                cur.execute(sql, tuple(params))
            else:
                cur.execute(sql)

            for row in cur.fetchall():
                feature_rows.append({"symbol": row[0], "date": row[1], "feature_name": row[2], "value": row[3]})
    except Exception:
        feature_rows = []

    _write_to_file(feature_rows, features_path, features_header, export_format)

    if price_rows and horizons_days:
        try:
            import pandas as pd

            from g2.ml.labels import classify_return_5class

            thresholds = (manifest.get("label_spec") or {}).get("thresholds") or {}
            df = pd.DataFrame(price_rows)
            if df.empty:
                return
            df["close_for_label"] = df["adjusted_close"].where(df["adjusted_close"].notna(), df["close"])
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
                labels = ret.apply(
                    lambda r: classify_return_5class(r, weak_threshold=weak, strong_threshold=strong).value
                    if pd.notna(r) and abs(r) != float("inf")
                    else None
                )
                tmp = df[["symbol", "date"]].copy()
                tmp["horizon_days"] = h
                tmp["forward_return"] = ret
                tmp["label"] = labels
                out.append(tmp)
            if out:
                labels_df = pd.concat(out, ignore_index=True)
                labels_df = labels_df.dropna(subset=["forward_return", "label"])
                if not labels_df.empty:
                    if export_format == "parquet":
                        labels_df.to_parquet(labels_path, index=False)
                    else:
                        labels_df.to_csv(labels_path, index=False)
        except Exception:
            return
