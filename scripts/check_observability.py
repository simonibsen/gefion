#!/usr/bin/env python3
"""Pre-commit check: significant modules must import from gefion.observability.

Constitution Section IV requires tracing on all significant operations.
This script checks staged Python files in src/gefion/ for observability imports.

A file is "significant" if it contains database queries, subprocess calls,
or ML operations — identified by marker patterns.
"""
import subprocess
import sys

# Patterns that indicate a file does significant work requiring tracing
SIGNIFICANCE_MARKERS = [
    "cur.execute(",       # Database queries
    "conn.cursor()",      # Database operations
    "subprocess.run(",    # Subprocess calls
    "subprocess.Popen(",  # Subprocess calls
    "create_span(",       # Already has some tracing (should have import)
    "def train(",         # ML training
    "def predict(",       # ML prediction
    "def compute(",       # Feature computation
]

# Files/paths exempt from this check
EXEMPT_PATHS = [
    "src/gefion/observability.py",   # The module itself
    "src/gefion/__init__.py",        # Package init
    "src/gefion/ui/views/",          # UI views (optional tracing)
    "src/gefion/ui/components/",     # UI components (optional, except chat.py)
    "tests/",                        # Test files
]

REQUIRED_IMPORT = "from gefion.observability import"


def check_file(filepath: str) -> bool:
    """Check if a significant file has observability imports. Returns True if OK."""
    # Check exemptions
    for exempt in EXEMPT_PATHS:
        if exempt in filepath:
            return True

    try:
        with open(filepath) as f:
            content = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return True

    # Is this a significant file?
    is_significant = any(marker in content for marker in SIGNIFICANCE_MARKERS)
    if not is_significant:
        return True

    # Does it have the required import?
    if REQUIRED_IMPORT in content:
        return True

    return False


def main():
    """Check staged files for observability compliance."""
    # Get staged Python files in src/gefion/
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True
    )
    staged_files = [
        f for f in result.stdout.strip().split("\n")
        if f.startswith("src/gefion/") and f.endswith(".py") and f.strip()
    ]

    if not staged_files:
        return 0

    violations = []
    for filepath in staged_files:
        if not check_file(filepath):
            violations.append(filepath)

    if violations:
        print("\n" + "=" * 65)
        print("  OBSERVABILITY VIOLATION (Constitution Section IV)")
        print("=" * 65)
        print()
        print("These files do significant work but don't import from")
        print("gefion.observability:")
        print()
        for v in violations:
            print(f"  {v}")
        print()
        print("Add: from gefion.observability import create_span, set_attributes")
        print()
        print("To bypass: git commit --no-verify")
        print("=" * 65 + "\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
