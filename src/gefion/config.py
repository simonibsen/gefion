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


_VALID_ENVS = ("dev", "production")
DEFAULT_MIN_FREE_DISK_GB = 20.0
DEFAULT_MIN_FREE_MEM_GB = 2.0


def _normalize_env(value: Optional[str]) -> str:
    """Host identity; unknown/invalid fails conservative to 'dev' (#158)."""
    candidate = (value or "").strip().lower()
    return candidate if candidate in _VALID_ENVS else "dev"


def _float_or(value: Optional[str], default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Core runtime settings."""

    alphavantage_api_key: Optional[str]
    database_url: Optional[str]
    gefion_env: str = "dev"
    min_free_disk_gb: float = DEFAULT_MIN_FREE_DISK_GB
    min_free_mem_gb: float = DEFAULT_MIN_FREE_MEM_GB
    env_file: Optional[Path] = None

    def __repr__(self) -> str:  # pragma: no cover - simple masking logic
        masked = "***" if self.alphavantage_api_key else None
        return (
            "Settings("
            f"alphavantage_api_key={masked}, "
            f"database_url={self.database_url!r}, "
            f"gefion_env={self.gefion_env!r}, "
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
            "GEFION_ENV": self.gefion_env,
            "GEFION_MIN_FREE_DISK_GB": str(self.min_free_disk_gb),
            "GEFION_MIN_FREE_MEM_GB": str(self.min_free_mem_gb),
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
        gefion_env=_normalize_env(env_map.get("GEFION_ENV")),
        min_free_disk_gb=_float_or(
            env_map.get("GEFION_MIN_FREE_DISK_GB"), DEFAULT_MIN_FREE_DISK_GB
        ),
        min_free_mem_gb=_float_or(
            env_map.get("GEFION_MIN_FREE_MEM_GB"), DEFAULT_MIN_FREE_MEM_GB
        ),
        env_file=file_path,
    )


def apply_dotenv(
    env_file: Optional[Path | str] = None,
    environ: Optional[MutableMapping[str, str]] = None,
    override: bool = False,
) -> Dict[str, str]:
    """Load a .env file into ``environ`` (``os.environ`` by default).

    Makes ``.env`` the single config source of truth for a process that reads
    ``os.environ`` directly (e.g. the MCP server, which resolves DATABASE_URL,
    GEFION_ENV, thresholds from the environment). Existing keys are preserved
    unless ``override`` — so an explicitly-set variable (e.g. the MCP client's
    ``env`` block) still wins over the file. Returns the keys actually applied.
    """
    target: MutableMapping[str, str] = os.environ if environ is None else environ
    path = Path(env_file) if env_file is not None else Path(".env")
    applied: Dict[str, str] = {}
    for key, value in _parse_env_file(path).items():
        if override or key not in target:
            target[key] = value
            applied[key] = value
    return applied
