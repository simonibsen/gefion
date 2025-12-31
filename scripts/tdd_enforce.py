#!/usr/bin/env python3
"""
TDD Enforcement Hook for Claude Code.

This script is called by Claude Code's PreToolUse hook before Write/Edit operations.
It tracks which files have been modified in the session and blocks src/ writes
that don't have corresponding tests/ writes first.

Exit codes:
- 0: Allow the operation
- 2: Block the operation (stderr sent to Claude)
"""

import json
import os
import sys
from pathlib import Path

# Session tracking file - unique per terminal session
SESSION_FILE = Path(f"/tmp/claude_tdd_session_{os.getppid()}.json")

# Paths that require TDD
SRC_PATTERNS = ["src/g2/"]
TEST_PATTERNS = ["tests/"]

# Files exempt from TDD (config, docs, etc.)
EXEMPT_PATTERNS = [
    "__init__.py",
    "CLAUDE.md",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".gitignore",
    "scripts/",
    "docs/",
    ".claude/",
]


def load_session() -> dict:
    """Load session state from temp file."""
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"tests_written": [], "src_written": []}


def save_session(session: dict) -> None:
    """Save session state to temp file."""
    SESSION_FILE.write_text(json.dumps(session, indent=2))


def is_exempt(file_path: str) -> bool:
    """Check if file is exempt from TDD requirements."""
    for pattern in EXEMPT_PATTERNS:
        if pattern in file_path:
            return True
    return False


def is_test_file(file_path: str) -> bool:
    """Check if file is a test file."""
    for pattern in TEST_PATTERNS:
        if pattern in file_path:
            return True
    return False


def is_src_file(file_path: str) -> bool:
    """Check if file is a source file requiring TDD."""
    for pattern in SRC_PATTERNS:
        if pattern in file_path:
            return True
    return False


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Can't parse input, allow operation
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    # Load session state
    session = load_session()

    # If this is a test file, record it and allow
    if is_test_file(file_path):
        if file_path not in session["tests_written"]:
            session["tests_written"].append(file_path)
            save_session(session)
        sys.exit(0)

    # If exempt, allow
    if is_exempt(file_path):
        sys.exit(0)

    # If this is a src file, check TDD compliance
    if is_src_file(file_path):
        if not session["tests_written"]:
            # No tests written in this session - BLOCK
            print(
                """
═══════════════════════════════════════════════════════════════
❌ TDD VIOLATION: Attempting to write src/ before tests/
═══════════════════════════════════════════════════════════════

You are trying to modify: {file_path}

But NO TEST FILES have been written in this session.

TDD REQUIRES:
1. Write a failing test FIRST (in tests/)
2. Run the test and verify it FAILS
3. THEN implement the code (in src/)
4. Run the test and verify it PASSES

This operation has been BLOCKED.

To proceed:
1. Write your test file first
2. Then retry this operation

═══════════════════════════════════════════════════════════════
""".format(file_path=file_path),
                file=sys.stderr,
            )
            sys.exit(2)  # Block the operation

        # Tests were written, record src file and allow
        if file_path not in session["src_written"]:
            session["src_written"].append(file_path)
            save_session(session)
        sys.exit(0)

    # Not a tracked file type, allow
    sys.exit(0)


if __name__ == "__main__":
    main()
