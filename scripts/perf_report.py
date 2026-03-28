#!/usr/bin/env python3
"""Performance report — query Tempo for traces and identify bottlenecks.

Usage:
    python scripts/perf_report.py              # Show slow traces (default thresholds)
    python scripts/perf_report.py 1000         # Custom minimum duration (ms)
    python scripts/perf_report.py dashboard    # Filter by span name
    python scripts/perf_report.py --detail     # Drill into slowest trace
    python scripts/perf_report.py --baseline   # Save current as baseline
    python scripts/perf_report.py --compare    # Compare against latest baseline
"""
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "gefion")
BASELINE_DIR = Path.home() / ".gefion" / "trace_baselines"

# Span-specific thresholds (ms)
THRESHOLDS = {
    "ui.": 500,
    "db.": 500,
    "charts.": 2000,
    "cli.": 5000,
}

# Spans to ignore (long-running parent spans, not actionable)
IGNORE_SPANS = {"cli.ui"}
DEFAULT_THRESHOLD = 1000


def get_threshold(name: str) -> int:
    for prefix, t in THRESHOLDS.items():
        if name.startswith(prefix):
            return t
    return DEFAULT_THRESHOLD


def query_tempo(min_duration_ms: int = 100, limit: int = 20, name_filter: str = "") -> list:
    """Query Tempo search API for recent traces."""
    url = f"{TEMPO_URL}/api/search?service.name={SERVICE_NAME}&limit={limit}&minDuration={min_duration_ms}ms"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            traces = data.get("traces", [])
            if name_filter:
                traces = [t for t in traces if name_filter.lower() in t.get("rootTraceName", "").lower()]
            return traces
    except Exception as e:
        print(f"Error querying Tempo: {e}")
        return []


def fetch_trace_detail(trace_id: str) -> list:
    """Fetch all spans for a trace."""
    url = f"{TEMPO_URL}/api/traces/{trace_id}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            spans = []
            for batch in data.get("batches", []):
                for scope in batch.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        name = span.get("name", "?")
                        dur_ns = int(span.get("endTimeUnixNano", 0)) - int(span.get("startTimeUnixNano", 0))
                        dur_ms = dur_ns / 1e6
                        attrs = {}
                        for a in span.get("attributes", []):
                            v = a.get("value", {})
                            val = v.get("stringValue", v.get("intValue", v.get("doubleValue", "?")))
                            attrs[a["key"]] = val
                        spans.append({"name": name, "duration_ms": dur_ms, "attrs": attrs})
            return sorted(spans, key=lambda x: -x["duration_ms"])
    except Exception:
        return []


def print_report(traces: list, show_detail: bool = False):
    """Print performance report."""
    ranked = sorted(traces, key=lambda x: -x.get("durationMs", 0))
    slow = []
    ok = []
    for t in ranked:
        name = t.get("rootTraceName", "?")
        if name in IGNORE_SPANS:
            continue
        dur = t.get("durationMs", 0)
        tid = t.get("traceID", "")
        thresh = get_threshold(name)
        if dur > thresh:
            slow.append((name, dur, thresh, tid))
        else:
            ok.append((name, dur))

    print("Performance Report")
    print("=" * 65)
    print(f"Traces: {len(traces)} total, {len(slow)} slow, {len(ok)} ok")
    print()

    if slow:
        print("SLOW (exceeding threshold):")
        for name, dur, thresh, tid in slow[:10]:
            print(f"  {dur:>8,}ms  {name:40} [{thresh}ms]")
        print()

        if show_detail and slow:
            print(f"Drilling into: {slow[0][0]} ({slow[0][1]:,}ms)")
            print("-" * 65)
            spans = fetch_trace_detail(slow[0][3])
            for s in spans[:10]:
                print(f"  {s['duration_ms']:>10,.0f}ms  {s['name']}")
                db_stmt = s["attrs"].get("db.statement", "")
                if db_stmt:
                    print(f"              SQL: {db_stmt[:80]}")
            print()

            # Identify bottleneck
            if spans:
                top = spans[0]
                if "db.statement" in top["attrs"]:
                    stmt = top["attrs"]["db.statement"]
                    print(f"Bottleneck: DB query ({top['duration_ms']:,.0f}ms)")
                    print(f"  {stmt[:120]}")
                    if "COUNT" in stmt.upper() and ("DISTINCT" in stmt.upper() or "computed_features" in stmt or "stock_ohlcv" in stmt):
                        print("  → Replace COUNT/COUNT(DISTINCT) on hypertable with pg_stat_user_tables.n_live_tup")
                    elif "JOIN" in stmt.upper() and "stock_ohlcv" in stmt:
                        print("  → Bound the JOIN with WHERE date >= CURRENT_DATE - INTERVAL '30 days'")
                else:
                    print(f"Bottleneck: {top['name']} ({top['duration_ms']:,.0f}ms)")
    else:
        print("All traces within thresholds.")

    print()
    print(f"OK ({len(ok)} traces):")
    for name, dur in ok[:5]:
        print(f"  {dur:>8,}ms  {name}")
    if len(ok) > 5:
        print(f"  ... and {len(ok) - 5} more")


def save_baseline(label: str = ""):
    """Save current trace durations as baseline."""
    if not label:
        label = datetime.now().strftime("%Y%m%d_%H%M%S")
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    traces = query_tempo(min_duration_ms=1)
    baseline = {"label": label, "timestamp": datetime.now().isoformat(), "spans": {}}
    for t in traces:
        name = t.get("rootTraceName", "")
        dur = t.get("durationMs", 0)
        if name and (name not in baseline["spans"] or dur < baseline["spans"][name]):
            baseline["spans"][name] = dur

    outfile = BASELINE_DIR / f"{label}.json"
    outfile.write_text(json.dumps(baseline, indent=2))
    print(f"Baseline saved: {outfile}")
    print(f"Spans: {len(baseline['spans'])}")
    for name, dur in sorted(baseline["spans"].items(), key=lambda x: -x[1]):
        print(f"  {dur:>8}ms  {name}")


def compare_baseline():
    """Compare current traces against latest baseline."""
    if not BASELINE_DIR.exists():
        print("No baselines found. Run with --baseline first.")
        return

    baselines = sorted(BASELINE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not baselines:
        print("No baselines found.")
        return

    baseline = json.loads(baselines[0].read_text())
    print(f"Comparing against: {baseline['label']} ({baseline['timestamp'][:19]})")
    print()

    traces = query_tempo(min_duration_ms=1)
    current = {}
    for t in traces:
        name = t.get("rootTraceName", "")
        dur = t.get("durationMs", 0)
        if name and (name not in current or dur < current[name]):
            current[name] = dur

    all_names = sorted(set(list(baseline["spans"].keys()) + list(current.keys())))
    print(f"{'Span':40} {'Baseline':>10} {'Current':>10} {'Change':>10}")
    print("-" * 75)
    for name in all_names:
        b = baseline["spans"].get(name)
        c = current.get(name)
        if b and c:
            pct = ((c - b) / b) * 100 if b > 0 else 0
            flag = " REGRESSION" if pct > 20 else (" IMPROVED" if pct < -20 else "")
            print(f"  {name:38} {b:>8}ms {c:>8}ms {pct:>+8.0f}%{flag}")
        elif b:
            print(f"  {name:38} {b:>8}ms {'—':>9} {'gone':>9}")
        elif c:
            print(f"  {name:38} {'—':>9} {c:>8}ms {'new':>9}")


def main():
    args = sys.argv[1:]
    show_detail = "--detail" in args
    if "--detail" in args:
        args.remove("--detail")

    if "--baseline" in args:
        label = ""
        idx = args.index("--baseline")
        if idx + 1 < len(args):
            label = args[idx + 1]
        save_baseline(label)
        return

    if "--compare" in args:
        compare_baseline()
        return

    min_duration = 100
    name_filter = ""
    for arg in args:
        if arg.isdigit():
            min_duration = int(arg)
        elif not arg.startswith("-"):
            name_filter = arg

    traces = query_tempo(min_duration_ms=min_duration, name_filter=name_filter)
    if not traces:
        print("No traces found. Is Tempo running? Is OTEL_ENABLED=true?")
        return

    print_report(traces, show_detail=show_detail)


if __name__ == "__main__":
    main()
