"""Documentation drift checks.

Docs, the learning curriculum, and the MCP docs must track the real
command/tool surfaces. These tests introspect the actual CLI and MCP
server and fail when documentation lags — the same enforcement
philosophy as the data-dictionary drift checks.

Caught in the wild before this test existed: gefion-learn.md shipped
referencing `gefion health-check` and `gefion system-status`, neither
of which exists.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).parent.parent

# `gefion <token> [<subtoken>]` — tokens are kebab-case words
_CMD_RE = re.compile(
    r"\bgefion ([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"
    r"(?: ([a-z][a-z0-9]*(?:-[a-z0-9]+)*))?"
)

_SCANNED_DOCS = [
    ".claude/commands/gefion-learn.md",
    "specs/004-autonomous-experiments/quickstart.md",
    "README.md",
    "docs/USER_GUIDE.md",
]


def _cli_surface():
    """(top_level_commands, {group: subcommands}) from the real typer app."""
    from gefion.cli import app

    top = {c.name for c in app.registered_commands if c.name}
    groups = {}
    for g in app.registered_groups:
        groups[g.name] = {
            c.name for c in g.typer_instance.registered_commands if c.name
        }
    return top, groups


class TestCliCommandsDocumented:
    """Every experiment subcommand must appear in README or USER_GUIDE."""

    def test_experiment_subcommands_documented(self):
        _, groups = _cli_surface()
        corpus = (REPO / "README.md").read_text() + (
            REPO / "docs" / "USER_GUIDE.md").read_text()

        missing = [name for name in sorted(groups["experiment"])
                   if f"experiment {name}" not in corpus]
        assert not missing, (
            f"Undocumented `gefion experiment` subcommands: {missing} — "
            "add them to README.md or docs/USER_GUIDE.md"
        )


class TestDocumentedCommandsExist:
    """Every `gefion <cmd>` mentioned in key docs must actually exist."""

    @staticmethod
    def _code_contexts(text: str) -> str:
        """Fenced code blocks plus inline backtick spans — commands live
        there; prose like "what gefion is" must not trip the scanner."""
        fenced = re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)
        inline = re.findall(r"`([^`\n]+)`", text)
        return "\n".join(fenced + inline)

    @pytest.mark.parametrize("doc", _SCANNED_DOCS)
    def test_commands_exist(self, doc):
        top, groups = _cli_surface()
        text = self._code_contexts((REPO / doc).read_text())

        bogus = []
        for m in _CMD_RE.finditer(text):
            first, second = m.group(1), m.group(2)
            if first in groups:
                # Group alone is fine; a subtoken must be one of its commands
                if second and second not in groups[first]:
                    bogus.append(f"gefion {first} {second}")
            elif first not in top:
                bogus.append(f"gefion {first}")
        assert not bogus, (
            f"{doc} references commands that do not exist: {sorted(set(bogus))}"
        )


class TestMcpToolsDocumented:
    """New-generation MCP tools must appear in the MCP workflow docs."""

    def test_experiment_and_docs_tools_documented(self):
        server = (REPO / "mcp-server" / "server.py").read_text()
        tool_names = re.findall(r'name="((?:experiment_|docs_)[a-z_]+)"', server)
        corpus = (REPO / "docs" / "MCP_WORKFLOWS.md").read_text()

        missing = sorted({t for t in tool_names if t not in corpus})
        assert not missing, (
            f"MCP tools undocumented in docs/MCP_WORKFLOWS.md: {missing}"
        )
