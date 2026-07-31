"""Host capability inventory + posture derivation (#158).

`system_status` tailors its suggestions to what the host can actually afford.
Capability is *measured* (disk/memory/cpu), not hand-declared; the declared
GEFION_ENV (dev|production) only biases policy — a `dev` host is treated
conservatively even when a measurement looks fine (its free disk today says
nothing about tomorrow's bulk job on a shared box). These functions are
pure/measurable and importable; the MCP server is a thin caller that folds the
result into system_status.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List

from gefion.observability import create_span, set_attributes

_GB = 1_000_000_000  # decimal GB, matches human-facing disk sizes


def inventory(data_path: str = ".") -> Dict[str, Any]:
    """Measure host capabilities on the volume backing ``data_path``."""
    with create_span("host.inventory", data_path=data_path) as span:
        usage = shutil.disk_usage(data_path)
        cpu = os.cpu_count() or 1
        try:
            import psutil

            vm = psutil.virtual_memory()
            mem_total = vm.total / _GB
            mem_available = vm.available / _GB
        except Exception:
            # psutil unavailable/unreadable — report memory as unknown (0.0)
            mem_total = 0.0
            mem_available = 0.0

        inv = {
            "disk_free_gb": round(usage.free / _GB, 1),
            "disk_total_gb": round(usage.total / _GB, 1),
            "mem_total_gb": round(mem_total, 1),
            "mem_available_gb": round(mem_available, 1),
            "cpu_count": cpu,
        }
        set_attributes(
            span,
            disk_free_gb=inv["disk_free_gb"],
            mem_available_gb=inv["mem_available_gb"],
            cpu_count=cpu,
        )
        return inv


def assess(
    env: str,
    inv: Dict[str, Any],
    *,
    min_free_disk_gb: float = 20.0,
    min_free_mem_gb: float = 2.0,
) -> Dict[str, Any]:
    """Derive per-resource posture + policy from host identity + measurements.

    ``env`` biases policy: a non-production host bounds data operations
    regardless of measured disk headroom (conservative by default); production
    trusts the measurement. The result names *which* resource is tight rather
    than applying a single blanket 'constrained' label — every host is
    constrained somewhere.
    """
    disk_free = float(inv.get("disk_free_gb") or 0.0)
    mem_available = float(inv.get("mem_available_gb") or 0.0)
    cpu_count = int(inv.get("cpu_count") or 1)

    disk_tight = disk_free < min_free_disk_gb
    mem_tight = mem_available < min_free_mem_gb
    # Non-production is conservative by policy; production trusts the number.
    bounded_data_ops = (env != "production") or disk_tight

    notes: List[str] = []
    if bounded_data_ops:
        why = "disk is tight" if disk_tight else f"this is a '{env}' host"
        notes.append(
            "Refresh price data with --limit against existing symbols "
            f"(do not run an unbounded data-update): {why}."
        )
    if mem_tight:
        notes.append(
            f"Low available memory ({mem_available:.1f} GB) — bound concurrency "
            "on heavy jobs (respect the OOM class)."
        )

    return {
        "env": env,
        "disk": {"free_gb": round(disk_free, 1), "tight": disk_tight},
        "memory": {"available_gb": round(mem_available, 1), "tight": mem_tight},
        "cpu": {"count": cpu_count},
        "bounded_data_ops": bounded_data_ops,
        "notes": notes,
    }
