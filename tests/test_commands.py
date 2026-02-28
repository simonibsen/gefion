"""Tests for Claude Code slash command files (.claude/commands/)."""

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"


class TestServicesCommand:
    """Structural validation of the /services slash command."""

    command_path = COMMANDS_DIR / "services.md"

    def test_services_command_exists(self):
        """The services.md command file must exist."""
        assert self.command_path.exists(), (
            f"Expected command file at {self.command_path}"
        )

    def test_services_command_has_frontmatter(self):
        """The command file must have YAML frontmatter with a description."""
        content = self.command_path.read_text()
        assert content.startswith("---"), "Command file must start with YAML frontmatter"
        # Find closing frontmatter delimiter
        end = content.index("---", 3)
        frontmatter = content[3:end]
        assert "description:" in frontmatter, (
            "Frontmatter must contain a 'description' field"
        )

    def test_services_command_has_arguments(self):
        """The command file must contain $ARGUMENTS placeholder."""
        content = self.command_path.read_text()
        assert "$ARGUMENTS" in content, (
            "Command file must contain $ARGUMENTS placeholder"
        )
