"""
Helper functions for CLI commands.

These helpers consolidate repeated patterns across CLI commands to make
updates simpler and reduce code duplication.
"""
from typing import List, Optional


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
