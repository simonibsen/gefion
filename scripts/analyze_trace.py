#!/usr/bin/env python3
"""Analyze a Tempo trace JSON file for bottlenecks and errors.

Usage:
    python scripts/analyze_trace.py <trace_file.json>
    python scripts/analyze_trace.py <trace_file.json> --errors
    python scripts/analyze_trace.py <trace_file.json> --slow 1000
"""
import json
import sys
from collections import defaultdict


def load_trace(path):
    """Load and parse a Tempo trace file (MCP tool output format)."""
    with open(path) as f:
        data = json.load(f)

    # MCP format: [{type: "text", text: "<json>"}]
    if isinstance(data, list) and data and "text" in data[0]:
        return json.loads(data[0]["text"]).get("trace", {})
    return data.get("trace", data)


def extract_spans(trace):
    """Extract all spans from a trace."""
    spans = []
    for svc in trace.get("services", []):
        svc_name = svc.get("serviceName", "?")
        for scope in svc.get("scopes", []):
            scope_name = scope.get("name", "?")
            for span in scope.get("spans", []):
                spans.append({
                    "service": svc_name,
                    "scope": scope_name,
                    "name": span.get("name", "?"),
                    "spanId": span.get("spanId", ""),
                    "parentSpanId": span.get("parentSpanId", ""),
                    "duration_ms": span.get("durationMs", 0),
                    "attributes": span.get("attributes", {}),
                    "events": span.get("events", []),
                    "status": span.get("status", {}),
                })
    return spans


def analyze(spans, slow_threshold_ms=1000, show_errors=False):
    """Analyze spans for bottlenecks and errors."""
    # Group by name
    by_name = defaultdict(lambda: {"count": 0, "total_ms": 0, "max_ms": 0, "errors": 0})
    errors = []

    for span in spans:
        name = span["name"]
        dur = span["duration_ms"]
        by_name[name]["count"] += 1
        by_name[name]["total_ms"] += dur
        by_name[name]["max_ms"] = max(by_name[name]["max_ms"], dur)

        # Check for errors
        attrs = span["attributes"]
        has_error = (
            attrs.get("error") not in (None, False, "False", "false")
            or attrs.get("total_errors", 0) > 0
            or span["status"].get("code") == 2
        )
        if has_error:
            by_name[name]["errors"] += 1
            errors.append({
                "name": name,
                "dur_ms": dur,
                "attrs": {k: v for k, v in attrs.items()
                         if any(kw in k.lower() for kw in ("error", "message", "exception"))},
            })

    # Print summary
    print(f"Total spans: {len(spans)}")
    print(f"Unique span names: {len(by_name)}")
    print(f"Errors: {len(errors)}")
    print()

    # Slowest spans
    print(f"{'SLOWEST SPANS':=^70}")
    print(f"{'Max ms':>10}  {'Avg ms':>8}  {'Count':>6}  {'Errors':>6}  Name")
    print("-" * 70)
    for name, info in sorted(by_name.items(), key=lambda x: -x[1]["max_ms"])[:20]:
        avg = info["total_ms"] / info["count"]
        err = f"{info['errors']}" if info["errors"] > 0 else ""
        flag = " <<<" if info["max_ms"] > slow_threshold_ms else ""
        print(f"{info['max_ms']:10.0f}  {avg:8.0f}  {info['count']:6d}  {err:>6}  {name}{flag}")

    # N+1 detection
    print()
    print(f"{'POTENTIAL N+1 PATTERNS':=^70}")
    for name, info in sorted(by_name.items(), key=lambda x: -x[1]["count"]):
        if info["count"] > 20:
            print(f"  {info['count']:4d}x  {name}  (total: {info['total_ms']:.0f}ms)")

    # Errors
    if show_errors and errors:
        print()
        print(f"{'ERRORS':=^70}")
        seen = set()
        for e in errors:
            key = f"{e['name']}:{e.get('attrs', {})}"
            if key not in seen:
                seen.add(key)
                print(f"  {e['name']}: {e['attrs']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_trace.py <trace_file.json> [--errors] [--slow N]")
        sys.exit(1)

    path = sys.argv[1]
    show_errors = "--errors" in sys.argv
    slow_threshold = 1000
    if "--slow" in sys.argv:
        idx = sys.argv.index("--slow")
        if idx + 1 < len(sys.argv):
            slow_threshold = int(sys.argv[idx + 1])

    trace = load_trace(path)
    spans = extract_spans(trace)

    if not spans:
        print("No spans found. Check trace format.")
        sys.exit(1)

    analyze(spans, slow_threshold_ms=slow_threshold, show_errors=show_errors)
