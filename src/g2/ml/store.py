from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

import psycopg
from psycopg.types.json import Json


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_ml_dataset(conn: psycopg.Connection, name: str, version: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a dataset manifest by name and version.

    Returns None if not found, otherwise a dict with all dataset fields.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, version, created_at, universe, feature_names, lookback_days,
                   horizons_days, label_spec, split_spec, artifact_uri, checksum
            FROM ml_datasets
            WHERE name = %s AND version = %s;
            """,
            (name, version),
        )
        row = cur.fetchone()
        if not row:
            return None

        return {
            "id": row[0],
            "name": row[1],
            "version": row[2],
            "created_at": row[3],
            "universe": row[4],
            "feature_names": row[5],
            "lookback_days": row[6],
            "horizons_days": row[7],
            "label_spec": row[8],
            "split_spec": row[9],
            "artifact_uri": row[10],
            "checksum": row[11],
        }


def upsert_ml_dataset(conn: psycopg.Connection, payload: Dict[str, Any]) -> int:
    """
    Insert/update a dataset manifest row. Returns the dataset id.

    Expected payload keys:
      - name, version, universe, feature_names, lookback_days, horizons_days
      - label_spec, split_spec, artifact_uri, checksum
    """
    params = {
        "name": payload["name"],
        "version": payload["version"],
        "universe": Json(payload.get("universe")) if payload.get("universe") is not None else None,
        "feature_names": payload.get("feature_names") or [],  # TEXT[] array
        "lookback_days": payload["lookback_days"],
        "horizons_days": payload["horizons_days"],  # INTEGER[] array
        "label_spec": Json(payload["label_spec"]),
        "split_spec": Json(payload["split_spec"]),
        "artifact_uri": payload["artifact_uri"],
        "checksum": payload.get("checksum"),
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ml_datasets
              (name, version, universe, feature_names, lookback_days, horizons_days, label_spec, split_spec, artifact_uri, checksum)
            VALUES
              (%(name)s, %(version)s, %(universe)s, %(feature_names)s, %(lookback_days)s, %(horizons_days)s,
               %(label_spec)s, %(split_spec)s, %(artifact_uri)s, %(checksum)s)
            ON CONFLICT (name, version) DO UPDATE SET
              universe = EXCLUDED.universe,
              feature_names = EXCLUDED.feature_names,
              lookback_days = EXCLUDED.lookback_days,
              horizons_days = EXCLUDED.horizons_days,
              label_spec = EXCLUDED.label_spec,
              split_spec = EXCLUDED.split_spec,
              artifact_uri = EXCLUDED.artifact_uri,
              checksum = EXCLUDED.checksum
            RETURNING id;
            """,
            params,
        )
        return int(cur.fetchone()[0])

