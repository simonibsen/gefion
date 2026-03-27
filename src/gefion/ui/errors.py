"""UI error logger — persists errors to ~/.gefion/ui_errors.jsonl for session summaries."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _error_file() -> Path:
    path = Path.home() / ".g2" / "ui_errors.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_ui_error(source: str, message: str, context: Optional[dict] = None) -> None:
    """Append an error entry to ~/.gefion/ui_errors.jsonl."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "message": message,
    }
    if context:
        entry["context"] = context
    try:
        with open(_error_file(), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.debug("Failed to write UI error log", exc_info=True)


def read_session_errors(since: Optional[datetime] = None) -> List[dict]:
    """Read error entries, optionally filtered by timestamp."""
    path = _error_file()
    if not path.exists():
        return []
    errors = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if since:
                ts = datetime.fromisoformat(entry["timestamp"])
                if ts < since:
                    continue
            errors.append(entry)
        except (json.JSONDecodeError, KeyError):
            continue
    return errors


def clear_errors() -> None:
    """Remove the error log file."""
    path = _error_file()
    if path.exists():
        path.unlink()
