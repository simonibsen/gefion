from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Deque, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table


class ProgressReporter:
    """
    Rich-based progress reporter with rate, ETA, successes/errors, and recent examples.
    Falls back to JSON events when json_output is True.
    """

    def __init__(
        self,
        total: int,
        json_output: bool = False,
        enabled: bool = True,
        max_examples: int = 5,
        max_recent: int = 10,
    ):
        self.total = max(total, 1)
        self.done = 0
        self.successes = 0
        self.errors = 0  # Total errors (data + resource)
        self.data_errors = 0  # Benign data availability errors
        self.resource_errors = 0  # Resource/performance errors that affect scaling
        self.inserted_total = 0
        self.last_ok: Optional[str] = None
        self.last_ok_inserted: Optional[int] = None
        self.last_err: Optional[str] = None
        self.json_output = json_output
        self.enabled = enabled
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._rate_avg = 0.0
        self.error_examples: Deque[tuple[str, str]] = deque(maxlen=max_examples)
        self.recent_updates: Deque[str] = deque(maxlen=max_recent)
        self.failed_features: Deque[tuple[str, str]] = deque(maxlen=max_examples)  # (symbol, failed features)
        self.console = Console()
        self.live: Optional[Live] = None
        self.queue_depth: Optional[int] = None
        self.fetch_completed: Optional[int] = None
        self.write_latencies: Deque[float] = deque(maxlen=100)
        self.avg_write_latency: float = 0.0

    def record_write_latency(self, duration: float) -> None:
        """Record a write operation latency in seconds."""
        with self._lock:
            self.write_latencies.append(duration)
            # Use simple average for small samples, EMA for larger samples
            if len(self.write_latencies) <= 5:
                # Simple average for small sample sizes
                self.avg_write_latency = sum(self.write_latencies) / len(self.write_latencies)
            else:
                # Use exponential moving average for responsiveness
                if self.avg_write_latency == 0.0:
                    self.avg_write_latency = duration
                else:
                    alpha = 0.3  # Weight for new values
                    self.avg_write_latency = (alpha * duration) + ((1 - alpha) * self.avg_write_latency)

    def get_avg_write_latency(self) -> float:
        """Get the average write latency in seconds."""
        with self._lock:
            return self.avg_write_latency

    def _eta(self, rate: float) -> Optional[float]:
        remaining = max(self.total - self.done, 0)
        if rate <= 0:
            return None
        return remaining / rate

    def _format_time(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def _build_table(self) -> Table:
        now = time.monotonic()
        elapsed = max(now - self._start, 1e-6)
        inst_rate = self.done / elapsed
        if self._rate_avg == 0.0:
            self._rate_avg = inst_rate
        else:
            self._rate_avg = (self._rate_avg * 0.8) + (inst_rate * 0.2)
        rate = self._rate_avg
        eta = self._eta(rate)
        overall_pct = (self.done / self.total) * 100
        success_pct = (self.successes / self.total * 100) if self.total else 0
        error_pct = (self.errors / self.total * 100) if self.total else 0

        table = Table(title=f"Ingestion Progress ({self.done}/{self.total})", title_style="bold blue")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right", style="green")

        table.add_row("Overall", f"{overall_pct:.1f}%")
        table.add_row("Success", f"{self.successes} ({success_pct:.1f}%)")
        table.add_row("Errors", f"{self.errors} ({error_pct:.1f}%)")
        if self.data_errors > 0 or self.resource_errors > 0:
            table.add_row("  Data errors", f"{self.data_errors}")
            table.add_row("  Resource errors", f"{self.resource_errors}")
        table.add_row("Inserted total", f"{self.inserted_total}")
        table.add_row("Rate", f"{rate:.2f}/s")
        table.add_row("Elapsed", self._format_time(elapsed))
        if eta is not None:
            table.add_row("ETC", self._format_time(eta))
        if self.avg_write_latency > 0:
            latency_ms = self.avg_write_latency * 1000
            table.add_row("Avg Write Latency", f"{latency_ms:.1f}ms")
        if hasattr(self, "workers") and self.workers:
            label = "Workers"
            if hasattr(self, "max_workers") and getattr(self, "max_workers", None):
                label = "Workers (cur/max)"
                table.add_row(label, f"{self.workers}/{self.max_workers}")
            else:
                table.add_row(label, str(self.workers))
        if hasattr(self, "writer_workers") and self.writer_workers:
            table.add_row("Writers", str(self.writer_workers))
        if hasattr(self, "mode") and self.mode:
            table.add_row("Mode", self.mode)
        if hasattr(self, "batch_size") and self.batch_size:
            table.add_row("Batch size", str(self.batch_size))
        if self.queue_depth is not None:
            table.add_row("Queue", f"{self.queue_depth}")
        if self.fetch_completed is not None:
            table.add_row("Fetched", f"{self.fetch_completed}")
        if self.last_ok:
            table.add_row("Last OK", f"{self.last_ok} ({self.last_ok_inserted or 0})")
        if self.last_err:
            table.add_row("Last Err", f"{self.last_err}")

        if self.error_examples:
            table.add_row("─" * 8, "─" * 8)
            table.add_row("[bold red]Recent Errors[/bold red]", "")
            for sym, reason in list(self.error_examples):
                table.add_row(f"  {sym}", reason)

        if self.failed_features:
            table.add_row("─" * 8, "─" * 8)
            table.add_row("[bold yellow]Failed Features[/bold yellow]", "")
            for sym, failed in list(self.failed_features):
                table.add_row(f"  {sym}", failed)

        if self.recent_updates:
            table.add_row("─" * 8, "─" * 8)
            table.add_row("[bold]Recent Updates[/bold]", "")
            for upd in list(self.recent_updates):
                table.add_row("  ", upd)

        return table

    def start_live(self) -> Optional[Live]:
        if self.json_output or not self.enabled:
            return None
        self.live = Live(self._build_table(), console=self.console, refresh_per_second=4)
        return self.live

    def _emit_json(self, label: Optional[str] = None, meta: Optional[dict] = None, status: str = "progress") -> None:
        now = time.monotonic()
        elapsed = max(now - self._start, 1e-6)
        rate = self.done / elapsed if elapsed > 0 else 0
        eta = self._eta(rate)
        payload = {
            "status": status,
            "done": self.done,
            "total": self.total,
            "percent": round((self.done / self.total) * 100, 2),
            "rate_per_sec": rate,
            "eta_seconds": eta,
            "errors": self.errors,
            "data_errors": self.data_errors,
            "resource_errors": self.resource_errors,
            "successes": self.successes,
            "inserted_total": self.inserted_total,
            "label": label,
            "last_ok": self.last_ok,
            "last_ok_inserted": self.last_ok_inserted,
            "last_err": self.last_err,
            "queue_depth": self.queue_depth,
            "fetch_completed": self.fetch_completed,
            "avg_write_latency_ms": round(self.avg_write_latency * 1000, 1) if self.avg_write_latency > 0 else None,
        }
        if hasattr(self, "workers"):
            payload["workers"] = getattr(self, "workers", None)
        if hasattr(self, "max_workers"):
            payload["max_workers"] = getattr(self, "max_workers", None)
        if hasattr(self, "writer_workers"):
            payload["writer_workers"] = getattr(self, "writer_workers", None)
        if meta:
            payload.update(meta)
        typer.echo(json.dumps(payload))

    def _categorize_error(self, reason: Optional[str]) -> str:
        """Categorize error as 'data' (benign) or 'resource' (affects scaling)."""
        if not reason:
            return "resource"  # Unknown errors default to resource

        reason_lower = reason.lower()

        # Data availability errors (benign - don't affect scaling)
        data_error_keywords = [
            "no price data",
            "no change",
            "empty indicators",
            "features failed",
            "invalid symbol",
            "delisted",
            "not found",
        ]

        for keyword in data_error_keywords:
            if keyword in reason_lower:
                return "data"

        # Resource/performance errors (affect scaling)
        # These include: deadlock, timeout, connection errors, memory errors, etc.
        return "resource"

    def step_done(self, label: Optional[str] = None, error: bool = False, meta: Optional[dict] = None, error_type: Optional[str] = None) -> None:
        """
        Record completion of a processing step.

        Args:
            label: Symbol or identifier being processed
            error: Whether this step resulted in an error
            meta: Additional metadata (inserted count, error reason, etc.)
            error_type: Optional override for error categorization ('data' or 'resource')
                       If not provided, will auto-categorize based on error reason
        """
        with self._lock:
            self.done += 1
            if error:
                self.errors += 1
                reason = meta.get("reason") if meta else None

                # Categorize error type (auto-detect if not specified)
                if error_type is None:
                    error_type = self._categorize_error(reason)

                # Track error by type
                if error_type == "data":
                    self.data_errors += 1
                else:  # resource or unknown
                    self.resource_errors += 1

                if reason:
                    self.error_examples.append((label or "", reason))
                    self.last_err = f"{label}: {reason}"
            else:
                self.successes += 1
                if meta and "inserted" in meta:
                    self.inserted_total += meta["inserted"]
                    self.last_ok_inserted = meta["inserted"]
                self.last_ok = label
                # Track partial failures (when some features failed but overall succeeded)
                if meta and "failed_features" in meta and meta["failed_features"]:
                    failed_list = meta["failed_features"]
                    if isinstance(failed_list, list) and failed_list:
                        # failed_list is [(feature, error_msg), ...] tuples
                        # Deduplicate by feature name (keep first occurrence)
                        seen = set()
                        unique_failures = []
                        for feat, err in failed_list:
                            if feat not in seen:
                                seen.add(feat)
                                unique_failures.append((feat, err))
                        # Format as "feature: error" for display
                        formatted = ", ".join([f"{feat}: {err}" for feat, err in unique_failures])
                        self.failed_features.append((label or "", formatted))
            if meta and "inserted" in meta and not error:
                self.recent_updates.append(f"{label} inserted {meta['inserted']}")

        if self.json_output:
            self._emit_json(label, meta, status="progress" if self.done < self.total else "complete")
        else:
            if self.live:
                self.live.update(self._build_table())

    def complete(self, live: Optional[Live] = None) -> None:
        if self.json_output:
            self._emit_json(status="complete")
        elif live or self.live:
            target_live = live or self.live
            if target_live:
                target_live.update(self._build_table())

    def update_stats(self, queue_depth: Optional[int] = None, fetch_completed: Optional[int] = None) -> None:
        with self._lock:
            if queue_depth is not None:
                self.queue_depth = queue_depth
            if fetch_completed is not None:
                self.fetch_completed = fetch_completed
        if self.live:
            self.live.update(self._build_table())
