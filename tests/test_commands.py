"""Tests for Claude Code slash command files (.claude/commands/)."""

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"


class TestServicesCommand:
    """Structural validation of the /gefion-services slash command."""

    command_path = COMMANDS_DIR / "gefion-services.md"

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


class TestDevCommand:
    """Structural validation of the /gefion-dev slash command."""

    command_path = COMMANDS_DIR / "gefion-dev.md"

    def test_dev_command_exists(self):
        """The gefion-dev.md command file must exist."""
        assert self.command_path.exists(), (
            f"Expected command file at {self.command_path}"
        )

    def test_dev_command_has_frontmatter(self):
        """The command file must have YAML frontmatter with a description."""
        content = self.command_path.read_text()
        assert content.startswith("---"), "Command file must start with YAML frontmatter"
        end = content.index("---", 3)
        frontmatter = content[3:end]
        assert "description:" in frontmatter, (
            "Frontmatter must contain a 'description' field"
        )

    def test_dev_command_has_arguments(self):
        """The command file must contain $ARGUMENTS placeholder."""
        content = self.command_path.read_text()
        assert "$ARGUMENTS" in content, (
            "Command file must contain $ARGUMENTS placeholder"
        )

    def test_dev_command_has_modes(self):
        """The command file must document status, next, and run modes."""
        content = self.command_path.read_text()
        assert "status" in content.lower(), "Must document 'status' mode"
        assert "next" in content.lower(), "Must document 'next' mode"
        assert "run" in content.lower(), "Must document 'run' mode"

    def test_dev_command_references_project_files(self):
        """The command must reference key project state files."""
        content = self.command_path.read_text()
        assert "backlog.md" in content, "Must reference backlog.md"
        assert "progress.md" in content, "Must reference progress.md"
        assert "constitution.md" in content, "Must reference constitution.md"
