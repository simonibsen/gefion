"""Safety checks for experiment execution.

Pre-flight and periodic checks for disk space, memory, and database health.
Experiments pause gracefully if any resource threshold is breached.
"""
import logging
import shutil
from typing import Optional

from gefion.observability import create_span

logger = logging.getLogger(__name__)


def check_disk_space(min_free_gb: float = 1.0) -> dict:
    """Check available disk space.

    Returns dict with ok, free_gb, message.
    """
    usage = shutil.disk_usage("/")
    free_gb = usage.free / (1024 ** 3)
    ok = free_gb >= min_free_gb
    message = f"Disk: {free_gb:.1f} GB free" if ok else f"Low disk space: {free_gb:.1f} GB free (need {min_free_gb:.1f} GB)"
    return {"ok": ok, "free_gb": round(free_gb, 2), "message": message}


def check_memory(max_used_pct: float = 90.0) -> dict:
    """Check memory usage.

    Returns dict with ok, used_pct, available_mb, message.
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        used_pct = mem.percent
        available_mb = mem.available / (1024 ** 2)
    except ImportError:
        # Fallback: read /proc/meminfo on Linux
        try:
            with open("/proc/meminfo") as f:
                lines = {l.split(":")[0]: l.split(":")[1].strip() for l in f}
            total_kb = int(lines["MemTotal"].split()[0])
            available_kb = int(lines["MemAvailable"].split()[0])
            used_pct = ((total_kb - available_kb) / total_kb) * 100
            available_mb = available_kb / 1024
        except Exception:
            # Cannot determine memory — assume ok
            return {"ok": True, "used_pct": 0.0, "available_mb": 0.0, "message": "Memory check unavailable"}

    ok = used_pct <= max_used_pct
    message = f"Memory: {used_pct:.1f}% used ({available_mb:.0f} MB available)" if ok else f"High memory: {used_pct:.1f}% used (threshold {max_used_pct:.1f}%)"
    return {"ok": ok, "used_pct": round(used_pct, 1), "available_mb": round(available_mb, 1), "message": message}


def check_db_health(conn) -> dict:
    """Check database connection health.

    Returns dict with ok, message.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"ok": True, "message": "Database connection healthy"}
    except Exception as e:
        return {"ok": False, "message": f"Database check failed: {e}"}


def run_preflight_checks(
    conn=None,
    min_free_gb: float = 1.0,
    max_memory_pct: float = 90.0,
) -> dict:
    """Run all pre-flight safety checks before experiment execution.

    Returns dict with ok (all checks passed) and checks (list of individual results).
    """
    with create_span("experiments.safety.preflight"):
        checks = []

        checks.append(check_disk_space(min_free_gb))
        checks.append(check_memory(max_memory_pct))

        if conn is not None:
            checks.append(check_db_health(conn))

        all_ok = all(c["ok"] for c in checks)
        return {"ok": all_ok, "checks": checks}
