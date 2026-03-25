#!/usr/bin/env python3
"""
Quick test to verify OpenTelemetry spans are being created and exported.
Run with: OTEL_ENABLED=true python tests/test_otel_smoke.py
"""
import os
import time
import logging

def main():
    # Configure logging first
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Set OTEL_ENABLED before importing observability module
    # (Use environment variables if already set, otherwise use defaults)
    os.environ.setdefault("OTEL_ENABLED", "true")
    os.environ.setdefault("OTEL_EXPORTER", "otlp")
    os.environ.setdefault("OTEL_OTLP_ENDPOINT", "http://localhost:4317")

    # Import observability after defaults are set
    from gefion.observability import create_span, is_enabled, shutdown

    print(f"OTEL enabled: {is_enabled()}")

    if not is_enabled():
        print("ERROR: OTEL not enabled!")
        return

    print("\nCreating test spans...")

    # Create a parent span
    with create_span("test_parent", operation="parent_operation") as parent_span:
        print("  Created parent span")
        time.sleep(0.1)

        # Create child span 1
        with create_span("test_child_1", operation="child_operation_1") as child1_span:
            print("    Created child span 1")
            time.sleep(0.1)

        # Create child span 2
        with create_span("test_child_2", operation="child_operation_2") as child2_span:
            print("    Created child span 2")
            time.sleep(0.1)

    print("\nSpans created. Shutting down to flush...")
    shutdown()
    print("Shutdown complete.")

    exporter = os.environ.get("OTEL_EXPORTER", "otlp")
    if exporter == "otlp":
        print("\nCheck Grafana Tempo UI at http://localhost:3000")
        print("Navigate to Explore → Tempo")
        print("Search for service 'g2' and spans: test_parent, test_child_1, test_child_2")
    else:
        print("\nExporter is not 'otlp'. If you're using Tempo locally, set OTEL_EXPORTER=otlp.")

if __name__ == "__main__":
    main()
