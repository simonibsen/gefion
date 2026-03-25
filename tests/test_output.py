"""Tests for the unified CLI output interface."""
import json
from io import StringIO
from unittest.mock import patch

import pytest

from gefion.output import Column, Output, get_output


class TestColumn:
    """Tests for Column dataclass."""

    def test_column_defaults(self):
        """Column has sensible defaults."""
        col = Column("Name")
        assert col.name == "Name"
        assert col.style == "white"
        assert col.justify == "left"
        assert col.json_key is None

    def test_column_key_defaults_to_name(self):
        """key() returns name when json_key is not set."""
        col = Column("Display Name")
        assert col.key() == "Display Name"

    def test_column_key_returns_json_key(self):
        """key() returns json_key when set."""
        col = Column("Display Name", json_key="display_name")
        assert col.key() == "display_name"


class TestOutputRichMode:
    """Tests for Output in rich mode (json_mode=False)."""

    def test_success_prints_checkmark(self, capsys):
        """success() prints green checkmark in rich mode."""
        out = Output(json_mode=False)
        out.success("Task completed")
        captured = capsys.readouterr()
        assert "Task completed" in captured.out

    def test_error_prints_x(self, capsys):
        """error() prints red X in rich mode."""
        out = Output(json_mode=False)
        out.error("Something failed")
        captured = capsys.readouterr()
        assert "Something failed" in captured.out

    def test_info_prints_dim(self, capsys):
        """info() prints dim text in rich mode."""
        out = Output(json_mode=False)
        out.info("Some info")
        captured = capsys.readouterr()
        assert "Some info" in captured.out


class TestOutputJsonMode:
    """Tests for Output in JSON mode."""

    def test_success_outputs_json_with_status(self, capsys):
        """success() outputs JSON with status='ok'."""
        out = Output(json_mode=True)
        out.success("Done", {"count": 42})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "ok"
        assert data["count"] == 42
        assert "_meta" in data
        assert "timestamp" in data["_meta"]

    def test_error_outputs_json_with_error_status(self, capsys):
        """error() outputs JSON with status='error'."""
        out = Output(json_mode=True)
        out.error("Failed", {"code": 500})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "error"
        assert data["message"] == "Failed"
        assert data["code"] == 500

    def test_warning_outputs_json_with_warning_status(self, capsys):
        """warning() outputs JSON with status='warning'."""
        out = Output(json_mode=True)
        out.warning("Careful", {"detail": "low disk"})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "warning"
        assert data["message"] == "Careful"
        assert data["detail"] == "low disk"

    def test_info_skipped_in_json_mode(self, capsys):
        """info() produces no output in JSON mode."""
        out = Output(json_mode=True)
        out.info("Some info")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_header_skipped_in_json_mode(self, capsys):
        """header() produces no output in JSON mode."""
        out = Output(json_mode=True)
        out.header("Section")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_plain_skipped_in_json_mode(self, capsys):
        """plain() produces no output in JSON mode."""
        out = Output(json_mode=True)
        out.plain("Some text")
        captured = capsys.readouterr()
        assert captured.out == ""


class TestOutputJsonMetadata:
    """Tests for JSON metadata (_meta) in output."""

    def test_meta_includes_timestamp(self, capsys):
        """_meta always includes timestamp."""
        out = Output(json_mode=True)
        out.success("Done")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "timestamp" in data["_meta"]
        # Should be ISO format
        assert "T" in data["_meta"]["timestamp"]

    def test_meta_includes_command(self, capsys):
        """_meta includes command when set."""
        out = Output(json_mode=True, command="strategy list")
        out.success("Done")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["_meta"]["command"] == "strategy list"

    def test_meta_includes_params(self, capsys):
        """_meta includes params when set."""
        out = Output(json_mode=True, params={"limit": 10, "verbose": True})
        out.success("Done")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["_meta"]["params"]["limit"] == 10
        assert data["_meta"]["params"]["verbose"] is True

    def test_meta_includes_json_output_param(self, capsys):
        """_meta includes json_output param for reproducibility."""
        out = Output(json_mode=True, params={"json_output": True, "limit": 5})
        out.success("Done")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["_meta"]["params"]["json_output"] is True
        assert data["_meta"]["params"]["limit"] == 5

    def test_meta_includes_none_params(self, capsys):
        """_meta includes None params to document available options."""
        out = Output(json_mode=True, params={"limit": 10, "offset": None})
        out.success("Done")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["_meta"]["params"]["limit"] == 10
        assert data["_meta"]["params"]["offset"] is None

    def test_meta_excludes_internal_params(self, capsys):
        """_meta filters out params starting with underscore."""
        out = Output(json_mode=True, params={"limit": 10, "_internal": "secret"})
        out.success("Done")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "limit" in data["_meta"]["params"]
        assert "_internal" not in data["_meta"]["params"]


class TestOutputTable:
    """Tests for table() method."""

    def test_table_json_uses_column_keys(self, capsys):
        """table() uses column json_key for JSON output."""
        out = Output(json_mode=True)
        columns = [
            Column("Display Name", json_key="name"),
            Column("Value", json_key="value"),
        ]
        rows = [["foo", 42], ["bar", 99]]
        out.table(columns=columns, rows=rows)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["data"][0]["name"] == "foo"
        assert data["data"][0]["value"] == 42
        assert data["count"] == 2

    def test_table_json_custom_data_key(self, capsys):
        """table() uses custom data_key for JSON output."""
        out = Output(json_mode=True)
        columns = [Column("Name", json_key="name")]
        rows = [["foo"], ["bar"]]
        out.table(columns=columns, rows=rows, data_key="items")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "items" in data
        assert data["items"][0]["name"] == "foo"

    def test_table_json_uses_json_data(self, capsys):
        """table() uses json_data when provided."""
        out = Output(json_mode=True)
        columns = [Column("Name")]
        rows = [["display"]]  # This is for rich output
        json_data = [{"name": "actual", "extra": True}]
        out.table(columns=columns, rows=rows, json_data=json_data)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["data"][0]["name"] == "actual"
        assert data["data"][0]["extra"] is True


class TestOutputKeyValue:
    """Tests for key_value() method."""

    def test_key_value_json_outputs_dict(self, capsys):
        """key_value() outputs dict as JSON."""
        out = Output(json_mode=True)
        out.key_value({"name": "foo", "count": 42})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["name"] == "foo"
        assert data["count"] == 42


class TestOutputListItems:
    """Tests for list_items() method."""

    def test_list_items_json_outputs_array(self, capsys):
        """list_items() outputs list as JSON."""
        out = Output(json_mode=True)
        items = [{"name": "a"}, {"name": "b"}]
        out.list_items(items)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["data"] == items
        assert data["count"] == 2


class TestGetOutput:
    """Tests for get_output() factory function."""

    def test_get_output_explicit_json_mode(self):
        """get_output() respects explicit json_mode."""
        out = get_output(json_mode=True)
        assert out.json_mode is True

        out = get_output(json_mode=False)
        assert out.json_mode is False

    def test_get_output_defaults_to_rich_mode(self):
        """get_output() defaults to rich mode when no context."""
        out = get_output()
        assert out.json_mode is False
