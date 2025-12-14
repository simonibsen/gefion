from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict


def export_dataset_artifacts(conn, *, manifest: Dict[str, Any], out_dir: Path) -> None:
    """
    Export dataset artifacts (MVP placeholder).

    The first implementation will export:
      - prices (stock_ohlcv)
      - features (computed_features joined to feature_definitions)
      - labels (forward returns + 5-class labels per horizon)

    This is intentionally a small surface so the CLI can call it when `--export` is set.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prices_path = out_dir / "prices.csv"
    features_path = out_dir / "features.csv"
    labels_path = out_dir / "labels.csv"

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

    with prices_path.open("w", newline="") as f:
        csv.writer(f).writerow(prices_header)
    with features_path.open("w", newline="") as f:
        csv.writer(f).writerow(features_header)
    with labels_path.open("w", newline="") as f:
        csv.writer(f).writerow(labels_header)

    universe = manifest.get("universe") or {}
    symbols = universe.get("symbols") or []
    horizons_days = manifest.get("horizons_days") or []

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

    if price_rows:
        with prices_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=prices_header)
            w.writerows(price_rows)

    feature_rows: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cur:
            if symbols:
                cur.execute(
                    """
                    SELECT s.symbol, cf.date, fd.name, cf.value
                    FROM computed_features cf
                    JOIN feature_definitions fd ON fd.id = cf.feature_id
                    JOIN stocks s ON s.id = cf.data_id
                    WHERE s.symbol = ANY(%s)
                    ORDER BY s.symbol, cf.date, fd.name;
                    """,
                    (list(symbols),),
                )
            else:
                cur.execute(
                    """
                    SELECT s.symbol, cf.date, fd.name, cf.value
                    FROM computed_features cf
                    JOIN feature_definitions fd ON fd.id = cf.feature_id
                    JOIN stocks s ON s.id = cf.data_id
                    ORDER BY s.symbol, cf.date, fd.name;
                    """
                )
            for row in cur.fetchall():
                feature_rows.append({"symbol": row[0], "date": row[1], "feature_name": row[2], "value": row[3]})
    except Exception:
        feature_rows = []

    if feature_rows:
        with features_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=features_header)
            w.writerows(feature_rows)

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
                    labels_df.to_csv(labels_path, mode="a", header=False, index=False)
        except Exception:
            return
