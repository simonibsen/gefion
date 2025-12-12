"""
Helper functions for CLI commands.

These helpers consolidate repeated patterns across CLI commands to make
updates simpler and reduce code duplication.
"""
from typing import List, Optional, Dict, Any
import psycopg
from psycopg.types.json import Json


def parse_comma_separated(
    value: Optional[str],
    lowercase: bool = False,
    required: bool = False
) -> Optional[List[str]]:
    """
    Parse comma-separated string into list of trimmed values.

    Args:
        value: Comma-separated string or None
        lowercase: Apply .lower() to each value
        required: Raise error if result is empty

    Returns:
        List of parsed values or None if value is None/empty

    Raises:
        ValueError: If required=True and no values found

    Examples:
        >>> parse_comma_separated("foo,bar,baz")
        ['foo', 'bar', 'baz']

        >>> parse_comma_separated("  foo  ,  bar  ")
        ['foo', 'bar']

        >>> parse_comma_separated("FOO,Bar", lowercase=True)
        ['foo', 'bar']

        >>> parse_comma_separated(None)
        None

        >>> parse_comma_separated("", required=True)
        Traceback (most recent call last):
            ...
        ValueError: At least one value required
    """
    if not value:
        if required:
            raise ValueError("At least one value required")
        return None

    items = [s.strip() for s in value.split(",") if s.strip()]

    if required and not items:
        raise ValueError("At least one value required")

    if lowercase:
        items = [s.lower() for s in items]

    return items if items else None


def upsert_feature_function(
    conn: psycopg.Connection,
    payload: Dict[str, Any],
    return_id: bool = False
) -> Optional[int]:
    """
    Insert or update a feature function in the database.

    This consolidates the duplicate logic between register_function() and
    _upsert_feature_function() CLI commands.

    Args:
        conn: Database connection
        payload: Feature function definition dict with fields:
            - name (required): Function name
            - version (required): Version string
            - language (required): Programming language (e.g., "python")
            - function_body (required): Function code
            - status (optional): Status (default: "active")
            - description (optional): Function description
            - inputs (optional): Input schema as dict
            - output_name (optional): Output column name (default: "value")
            - output_type (optional): SQL type (default: "double precision")
            - param_schema (optional): Parameter schema as dict
            - defaults (optional): Default parameters as dict
            - dependencies (optional): Dependencies as dict
            - checksum (optional): Code checksum
            - tags (optional): Tags array
            - min_app_version (optional): Minimum app version
            - enabled (optional): Whether enabled (default: True)
            - created_by (optional): Creator (default: "cli")
        return_id: If True, return the function ID

    Returns:
        Function ID if return_id=True, otherwise None

    Example:
        >>> payload = {
        ...     "name": "my_indicator",
        ...     "version": "1.0",
        ...     "language": "python",
        ...     "function_body": "def compute(rows, specs): return []"
        ... }
        >>> func_id = upsert_feature_function(conn, payload, return_id=True)
    """
    params = {
        "name": payload.get("name"),
        "version": payload.get("version"),
        "status": payload.get("status", "active"),
        "description": payload.get("description"),
        "language": payload.get("language"),
        "function_body": payload.get("function_body"),
        "inputs": Json(payload.get("inputs")) if payload.get("inputs") is not None else None,
        "output_name": payload.get("output_name", "value"),
        "output_type": payload.get("output_type", "double precision"),
        "param_schema": Json(payload.get("param_schema")) if payload.get("param_schema") is not None else None,
        "defaults": Json(payload.get("defaults")) if payload.get("defaults") is not None else None,
        "dependencies": Json(payload.get("dependencies")) if payload.get("dependencies") is not None else None,
        "checksum": payload.get("checksum"),
        "tags": payload.get("tags"),
        "min_app_version": payload.get("min_app_version"),
        "enabled": payload.get("enabled", True),
        "created_by": payload.get("created_by", "cli"),
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions
            (name, version, status, description, language, function_body, inputs, output_name, output_type,
             param_schema, defaults, dependencies, checksum, tags, min_app_version, enabled, created_by)
            VALUES (%(name)s, %(version)s, %(status)s, %(description)s, %(language)s, %(function_body)s,
                    %(inputs)s, %(output_name)s, %(output_type)s, %(param_schema)s, %(defaults)s,
                    %(dependencies)s, %(checksum)s, %(tags)s, %(min_app_version)s, %(enabled)s, %(created_by)s)
            ON CONFLICT (name, version) DO UPDATE SET
                status = EXCLUDED.status,
                description = EXCLUDED.description,
                language = EXCLUDED.language,
                function_body = EXCLUDED.function_body,
                inputs = EXCLUDED.inputs,
                output_name = EXCLUDED.output_name,
                output_type = EXCLUDED.output_type,
                param_schema = EXCLUDED.param_schema,
                defaults = EXCLUDED.defaults,
                dependencies = EXCLUDED.dependencies,
                checksum = EXCLUDED.checksum,
                tags = EXCLUDED.tags,
                min_app_version = EXCLUDED.min_app_version,
                enabled = EXCLUDED.enabled,
                created_by = EXCLUDED.created_by,
                updated_at = NOW()
            """ + ("RETURNING id;" if return_id else ";"),
            params,
        )
        if return_id:
            return cur.fetchone()[0]
        return None
