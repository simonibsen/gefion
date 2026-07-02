"""
gefion package scaffold.

This project grows incrementally using TDD. Keep changes small and capture
decisions in docs/dev-journal.md.
"""

from importlib import metadata as _metadata

__all__ = ["__version__"]

try:
    # Single source of truth: git tags, via setuptools-scm at install time.
    __version__ = _metadata.version("gefion")
except _metadata.PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"
