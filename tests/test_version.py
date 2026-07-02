"""Version plumbing tests (issue #30).

The version has a single source of truth: git tags, surfaced through
package metadata by setuptools-scm at build/install time. Nothing in the
repo hardcodes a release version.
"""
from importlib import metadata

import gefion


def test_dunder_version_matches_installed_metadata():
    """gefion.__version__ must come from package metadata, not a hardcoded string."""
    assert gefion.__version__ == metadata.version("gefion")


def test_version_is_not_stale_hardcode():
    """Guard against the drifted hardcoded versions this test replaced."""
    assert gefion.__version__ not in ("0.1.0",), (
        "gefion.__version__ is the old hardcoded value — it must be derived "
        "from package metadata (setuptools-scm)"
    )
