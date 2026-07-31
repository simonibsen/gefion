"""#158: host capability inventory + posture derivation.

The MCP `system_status` tailors its suggestions to what the host can actually
afford. Capability is *measured* (disk/memory/cpu), not hand-declared; the
declared `GEFION_ENV` (dev|production) only biases policy — a `dev` host is
treated conservatively even when a number looks fine. These are the pure,
importable functions behind that; server.py is a thin caller.
"""
from gefion import host


def test_inventory_returns_numeric_capabilities():
    inv = host.inventory(".")
    for key in ("disk_free_gb", "disk_total_gb", "mem_total_gb",
                "mem_available_gb", "cpu_count"):
        assert key in inv, f"inventory missing {key}"
    assert inv["disk_free_gb"] > 0
    assert inv["mem_total_gb"] > 0
    assert inv["cpu_count"] >= 1


def _inv(disk_free_gb=100.0, mem_available_gb=8.0, cpu_count=8):
    return {
        "disk_free_gb": disk_free_gb, "disk_total_gb": 500.0,
        "mem_total_gb": 16.0, "mem_available_gb": mem_available_gb,
        "cpu_count": cpu_count,
    }


def test_disk_tight_when_below_threshold():
    p = host.assess("production", _inv(disk_free_gb=5.0), min_free_disk_gb=20.0)
    assert p["disk"]["tight"] is True
    assert p["bounded_data_ops"] is True


def test_disk_ample_in_production_is_unbounded():
    p = host.assess("production", _inv(disk_free_gb=100.0), min_free_disk_gb=20.0)
    assert p["disk"]["tight"] is False
    assert p["bounded_data_ops"] is False


def test_dev_bounds_data_ops_even_with_ample_disk():
    """env=dev is conservative by policy regardless of measured headroom."""
    p = host.assess("dev", _inv(disk_free_gb=500.0), min_free_disk_gb=20.0)
    assert p["disk"]["tight"] is False
    assert p["bounded_data_ops"] is True


def test_memory_tight_flag_and_note():
    p = host.assess("production", _inv(mem_available_gb=1.0), min_free_mem_gb=2.0)
    assert p["memory"]["tight"] is True
    assert any("concurrency" in n.lower() for n in p["notes"])


def test_bounded_note_mentions_limit():
    p = host.assess("dev", _inv(), min_free_disk_gb=20.0)
    assert any("--limit" in n for n in p["notes"])


def test_env_is_echoed_in_posture():
    assert host.assess("production", _inv())["env"] == "production"
