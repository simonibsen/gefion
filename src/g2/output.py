"""
Unified CLI output interface.

Provides consistent output formatting across all CLI commands,
supporting both rich/colored terminal output and JSON output.

Usage:
    from g2.output import get_output, Column

    out = get_output()
    out.success("Operation completed", {"count": 42})
    out.table(
        columns=[Column("Name", style="cyan"), Column("Value")],
        rows=[["foo", "bar"], ["baz", "qux"]],
    )
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)


@dataclass
class Column:
    """Table column definition."""

    name: str
    style: str = "white"
    justify: str = "left"
    json_key: Optional[str] = None  # Key to use in JSON output (defaults to name)

    def key(self) -> str:
        """Get the key to use in JSON output."""
        return self.json_key if self.json_key is not None else self.name


@dataclass
class Output:
    """
    Unified CLI output interface supporting both rich and JSON modes.

    In JSON mode, all output is emitted as JSON objects with metadata.
    In rich mode, output uses colored formatting via Rich library.
    """

    json_mode: bool = False
    console: Console = field(default_factory=Console)
    command: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    _collected: List[Dict] = field(default_factory=list)

    # === Messages ===

    def success(self, message: str, data: Optional[Dict] = None) -> None:
        """
        Emit a success message (green checkmark).

        In JSON mode, data fields are at the top level (not nested under message).
        """
        if self.json_mode:
            # Data at top level for machine parsing
            self._emit_json({**(data or {}), "status": "ok"})
        else:
            self.console.print(f"[bold green]\u2713[/] {message}")
            self._print_data(data)

    def error(self, message: str, data: Optional[Dict] = None) -> None:
        """Emit an error message (red X)."""
        if self.json_mode:
            self._emit_json({"status": "error", "message": message, **(data or {})})
        else:
            self.console.print(f"[bold red]\u2717[/] {message}")
            self._print_data(data)

    def warning(self, message: str, data: Optional[Dict] = None) -> None:
        """Emit a warning message (yellow triangle)."""
        if self.json_mode:
            self._emit_json({"status": "warning", "message": message, **(data or {})})
        else:
            self.console.print(f"[bold yellow]\u26a0[/] {message}")
            self._print_data(data)

    def info(self, message: str) -> None:
        """Emit an informational message (dim, skipped in JSON mode)."""
        if not self.json_mode:
            self.console.print(message, style="dim")

    def header(self, title: str) -> None:
        """Emit a section header (bold, skipped in JSON mode)."""
        if not self.json_mode:
            self.console.print(f"\n[bold]{title}[/]")

    def plain(self, message: str) -> None:
        """Emit plain text (skipped in JSON mode)."""
        if not self.json_mode:
            self.console.print(message)

    # === Structured Data ===

    def table(
        self,
        columns: List[Column],
        rows: List[List[Any]],
        title: Optional[str] = None,
        data_key: str = "data",
        json_data: Optional[List[Dict]] = None,
    ) -> None:
        """
        Render tabular data.

        In JSON mode, outputs as {"data_key": [...], "count": N}
        In rich mode, outputs a formatted table.

        Args:
            columns: Column definitions for the table
            rows: Row data (used for rich output, and JSON if json_data not provided)
            title: Table title (rich mode only)
            data_key: Key for the data array in JSON output
            json_data: Optional raw data for JSON output (avoids stringification)
        """
        if self.json_mode:
            if json_data is not None:
                # Use raw data directly
                self._emit_json({data_key: json_data, "count": len(json_data)})
            else:
                # Convert rows to dicts using column keys
                data = [
                    {col.key(): self._serialize(row[i]) for i, col in enumerate(columns)}
                    for row in rows
                ]
                self._emit_json({data_key: data, "count": len(rows)})
        else:
            table = Table(title=title)
            for col in columns:
                table.add_column(col.name, style=col.style, justify=col.justify)
            for row in rows:
                table.add_row(*[str(v) if v is not None else "" for v in row])
            self.console.print(table)

    def key_value(
        self, data: Dict[str, Any], title: Optional[str] = None
    ) -> None:
        """
        Render key-value pairs.

        In JSON mode, outputs the dict directly.
        In rich mode, outputs formatted key: value lines.
        """
        if self.json_mode:
            self._emit_json({k: self._serialize(v) for k, v in data.items()})
        else:
            if title:
                self.console.print(f"\n[bold]{title}[/]")
            max_key_len = max(len(str(k)) for k in data.keys()) if data else 0
            for k, v in data.items():
                self.console.print(f"  [cyan]{k:<{max_key_len}}[/] : {v}")

    def dict(self, data: Dict[str, Any]) -> None:
        """
        Output a dictionary directly.

        In JSON mode, outputs the dict as JSON.
        In rich mode, outputs as key-value pairs.
        """
        self.key_value(data)

    def list_items(
        self,
        items: List[Dict[str, Any]],
        format_fn: Optional[callable] = None,
        data_key: str = "data",
    ) -> None:
        """
        Output a list of items.

        In JSON mode, outputs as {"data": [...], "count": N}
        In rich mode, uses format_fn to render each item, or prints as-is.
        """
        if self.json_mode:
            self._emit_json({data_key: items, "count": len(items)})
        else:
            for item in items:
                if format_fn:
                    format_fn(self, item)
                else:
                    self.console.print(item)

    # === Raw JSON ===

    def json(self, data: Any) -> None:
        """Output raw JSON (used when json_mode is already known to be True)."""
        self._emit_json(data)

    # === Internal ===

    def _emit_json(self, payload: Dict) -> None:
        """Print JSON to stdout with metadata."""
        meta = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.command:
            meta["command"] = self.command
        if self.params:
            # Keep all params including None (documents available options)
            # Filter only internal params (underscore prefix)
            meta["params"] = {
                k: v for k, v in self.params.items()
                if not k.startswith("_")
            }

        output = {"_meta": meta, **payload}
        print(json.dumps(output, default=str, indent=2))

    def _print_data(self, data: Optional[Dict]) -> None:
        """Print supplementary data in rich mode."""
        if data:
            for k, v in data.items():
                self.console.print(f"  [dim]{k}:[/] {v}")

    def _serialize(self, value: Any) -> Any:
        """Serialize a value for JSON output."""
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, (list, tuple)):
            return [self._serialize(v) for v in value]
        if isinstance(value, dict):
            return {k: self._serialize(v) for k, v in value.items()}
        return str(value)


def get_output(json_mode: Optional[bool] = None) -> Output:
    """
    Get an Output instance configured from the current CLI context.

    If json_mode is not explicitly provided, attempts to read from
    the click/typer context's obj["json_output"] setting.

    Also extracts command name and parameters for JSON metadata.
    """
    command = None
    params = None

    try:
        import click

        ctx = click.get_current_context(silent=True)
        if ctx:
            # Extract command name (walk up to build full command path)
            command_parts = []
            current = ctx
            while current:
                if current.info_name:
                    command_parts.append(current.info_name)
                current = current.parent
            # Reverse and skip the root 'g2' if present
            command_parts = list(reversed(command_parts))
            if command_parts and command_parts[0] == "g2":
                command_parts = command_parts[1:]
            command = " ".join(command_parts) if command_parts else None

            # Extract parameters
            if ctx.params:
                params = dict(ctx.params)

            # Get json_mode from context if not provided
            if json_mode is None and ctx.obj:
                json_mode = ctx.obj.get("json_output", False)
    except Exception:
        pass

    if json_mode is None:
        json_mode = False

    return Output(
        json_mode=bool(json_mode),
        command=command,
        params=params,
    )
