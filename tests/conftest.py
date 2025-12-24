"""
Shared test fixtures and helpers.
"""
import json
from pathlib import Path
from typing import Optional, List

from g2.cli_helpers import upsert_feature_function


def load_feature_function_from_json(conn, json_path: str) -> None:
    """
    Load a feature function from a JSON file into the feature_functions table.

    Args:
        conn: Database connection
        json_path: Path to JSON file relative to project root

    Example:
        load_feature_function_from_json(conn, "feature-functions/indicator_rsi.json")
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Feature function file not found: {json_path}")

    payload = json.loads(path.read_text())
    upsert_feature_function(conn, payload)


def load_feature_functions(conn, function_names: Optional[List[str]] = None) -> int:
    """
    Load feature functions from the feature-functions directory.

    Args:
        conn: Database connection
        function_names: Optional list of function names to load (loads all if None)

    Returns:
        Number of functions loaded

    Example:
        # Load all functions
        load_feature_functions(conn)

        # Load specific functions
        load_feature_functions(conn, ["indicator_rsi", "indicator_adx"])
    """
    from g2.cli import import_functions_from_directory
    from pathlib import Path

    feature_functions_dir = Path("feature-functions")
    if not feature_functions_dir.exists():
        raise FileNotFoundError(f"Feature functions directory not found: {feature_functions_dir}")

    return import_functions_from_directory(conn, feature_functions_dir, function_names)
