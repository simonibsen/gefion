"""
Tests for CLI helper functions.

These helpers consolidate repeated patterns across CLI commands.
"""
import pytest
from g2.cli_helpers import parse_comma_separated


class TestParseCommaSeparated:
    """Test comma-separated string parsing helper."""

    def test_parse_simple_list(self):
        """Test parsing a simple comma-separated list."""
        result = parse_comma_separated("foo,bar,baz")
        assert result == ["foo", "bar", "baz"]

    def test_parse_with_spaces(self):
        """Test that extra spaces are stripped."""
        result = parse_comma_separated("  foo  ,  bar  ,  baz  ")
        assert result == ["foo", "bar", "baz"]

    def test_parse_with_empty_values(self):
        """Test that empty values are filtered out."""
        result = parse_comma_separated("foo,,bar,  ,baz")
        assert result == ["foo", "bar", "baz"]

    def test_parse_none_returns_none(self):
        """Test that None input returns None."""
        result = parse_comma_separated(None)
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """Test that empty string returns None."""
        result = parse_comma_separated("")
        assert result is None

    def test_parse_with_lowercase(self):
        """Test lowercase conversion."""
        result = parse_comma_separated("FOO,Bar,baz", lowercase=True)
        assert result == ["foo", "bar", "baz"]

    def test_parse_required_with_values(self):
        """Test required flag with valid values."""
        result = parse_comma_separated("foo,bar", required=True)
        assert result == ["foo", "bar"]

    def test_parse_required_with_none_raises(self):
        """Test required flag with None raises ValueError."""
        with pytest.raises(ValueError, match="At least one value required"):
            parse_comma_separated(None, required=True)

    def test_parse_required_with_empty_raises(self):
        """Test required flag with empty string raises ValueError."""
        with pytest.raises(ValueError, match="At least one value required"):
            parse_comma_separated("", required=True)

    def test_parse_required_with_only_empty_values_raises(self):
        """Test required flag with only empty values raises ValueError."""
        with pytest.raises(ValueError, match="At least one value required"):
            parse_comma_separated("  ,  ,  ", required=True)

    def test_parse_single_value(self):
        """Test parsing a single value."""
        result = parse_comma_separated("foo")
        assert result == ["foo"]

    def test_parse_preserves_case_by_default(self):
        """Test that case is preserved by default."""
        result = parse_comma_separated("Foo,BAR,baz")
        assert result == ["Foo", "BAR", "baz"]
