"""Conversation history persistence for the AI Actions UI.

Stores exchanges (prompt + response pairs) as JSONL in ~/.gefion/ai_history.jsonl.
Bounded to MAX_EXCHANGES to prevent unbounded growth.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

HISTORY_FILE = Path.home() / ".gefion" / "ai_history.jsonl"
MAX_EXCHANGES = 100


def append_exchange(
    prompt: str,
    mode: str,
    response: str,
    success: bool,
    duration_sec: float,
) -> None:
    """Append an exchange to the history file, truncating oldest if over MAX_EXCHANGES."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "mode": mode,
        "response": response,
        "success": success,
        "duration_sec": duration_sec,
    }

    # Read existing, append, truncate if needed
    exchanges = read_exchanges()
    exchanges.append(record)
    if len(exchanges) > MAX_EXCHANGES:
        exchanges = exchanges[-MAX_EXCHANGES:]

    # Write all back
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        for ex in exchanges:
            f.write(json.dumps(ex) + "\n")


def read_exchanges() -> List[Dict[str, Any]]:
    """Read all exchanges from the history file."""
    if not HISTORY_FILE.exists():
        return []
    exchanges = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    exchanges.append(json.loads(line))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read history file: %s", e)
        return []
    return exchanges


def clear_history() -> None:
    """Delete the history file."""
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()
