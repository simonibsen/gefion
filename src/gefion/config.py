"""
Minimal settings loader.

- Reads ALPHAVANTAGE_API_KEY and DATABASE_URL from environment or a supplied .env file
- Avoids printing secrets (repr masks sensitive values)
- Keeps dependencies to stdlib so early tests can run without installs
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, MutableMapping, Optional


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Parse simple KEY=VALUE lines from a .env-style file."""
    env: Dict[str, str] = {}
    if not path.exists():
        return env

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@dataclass
class Settings:
    """Core runtime settings."""

    alphavantage_api_key: Optional[str]
    database_url: Optional[str]
    env_file: Optional[Path] = None

    def __repr__(self) -> str:  # pragma: no cover - simple masking logic
        masked = "***" if self.alphavantage_api_key else None
        return (
            "Settings("
            f"alphavantage_api_key={masked}, "
            f"database_url={self.database_url!r}, "
            f"env_file={str(self.env_file) if self.env_file else None}"
            ")"
        )

    def as_dict(self, mask_secrets: bool = True) -> Dict[str, Optional[str]]:
        """Return settings as a dict, masking secrets by default."""
        api_key = self.alphavantage_api_key
        if mask_secrets and api_key:
            api_key = "***"
        return {
            "ALPHAVANTAGE_API_KEY": api_key,
            "DATABASE_URL": self.database_url,
        }


def load_settings(
    env: Optional[Mapping[str, str]] = None,
    env_file: Optional[Path | str] = None,
    include_os_env: bool = True,
) -> Settings:
    """Load settings from environment and optional .env file.

    Args:
        env: Optional mapping to layer on top of file/os env (e.g., for tests)
        env_file: Optional path to a .env file
        include_os_env: When False, ignore os.environ (useful for isolated tests)
    """
    env_map: MutableMapping[str, str] = {}

    file_path: Optional[Path] = None
    if env_file is not None:
        file_path = Path(env_file)
    else:
        default_path = Path(".env")
        if default_path.exists():
            file_path = default_path

    if file_path:
        env_map.update(_parse_env_file(file_path))

    if include_os_env:
        env_map.update(os.environ)

    if env:
        env_map.update(env)

    return Settings(
        alphavantage_api_key=env_map.get("ALPHAVANTAGE_API_KEY"),
        database_url=env_map.get("DATABASE_URL"),
        env_file=file_path,
    )
