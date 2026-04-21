# Observability (OpenTelemetry + Grafana Tempo)

Gefion uses OpenTelemetry for distributed tracing, exported to Grafana Tempo. All significant operations are instrumented — CLI commands, feature computation, database queries, API calls, UI page loads, and MCP tool invocations.

Tracing is **zero overhead** when disabled (`OTEL_ENABLED=false`, the default).

## Setup

### Start Tempo + Grafana

```bash
docker compose -f docker/tempo/docker-compose.tempo.yml up -d
```

This starts:
- **Tempo** on port 3200 (traces backend), accepting OTLP on ports 4317 (gRPC) and 4318 (HTTP)
- **Grafana** on port 3000 (UI for trace visualization)

Verify:

```bash
curl -s http://localhost:3200/ready    # Should return "ready"
curl -s http://localhost:3000/api/health  # Grafana health
```

### Enable Tracing

Set in your `.env` file (CLI auto-loads this):

```bash
OTEL_ENABLED=true
OTEL_SERVICE_NAME=gefion
OTEL_EXPORTER=otlp
OTEL_OTLP_ENDPOINT=http://localhost:4317
OTEL_SAMPLING_RATE=1.0
```

The CLI calls `reinitialize()` after loading `.env`, so tracing works even though the observability module is imported before `.env` is read.

### Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `false` | Enable/disable tracing |
| `OTEL_SERVICE_NAME` | `gefion` | Service name in traces |
| `OTEL_EXPORTER` | `otlp` | `otlp` (Tempo) or `console` (stdout) |
| `OTEL_OTLP_ENDPOINT` | `http://localhost:4317` | Tempo OTLP gRPC endpoint |
| `OTEL_SAMPLING_RATE` | `1.0` | Sampling rate (0.0 to 1.0) |

## Querying Traces

### Tempo MCP Server

Tempo has a built-in MCP server (enabled in `docker/tempo/tempo-config.yaml`). This allows querying traces directly via TraceQL without leaving the terminal.

In Claude Code, use `/gefion-perf` to query traces:

| Command | What it does |
|---------|-------------|
| `/gefion-perf` | Show slow traces (>100ms) from the last hour |
| `/gefion-perf 500` | Show traces slower than 500ms |
| `/gefion-perf ui` | All UI page load traces |
| `/gefion-perf db` | All database operation traces |
| `/gefion-perf fix` | Find slowest trace, investigate, and fix |

### Useful TraceQL Queries

```
{duration > 500ms}                          # All slow spans
{span.name =~ "ui." && duration > 500ms}    # Slow page loads
{span.name =~ "db." && duration > 200ms}    # Slow DB operations
{span.name =~ "alphavantage"}               # API calls
{status = error}                            # Errors
{} | count() > 50                           # High span count (N+1 pattern)
```

### CLI

```bash
gefion span-check          # Check recent traces for slow operations
gefion span-check --limit 20  # Show more traces
```

### Grafana UI

1. Open http://localhost:3000
2. Navigate to Explore → Tempo
3. Query by service name `gefion` or use TraceQL

## Instrumenting Code

Every significant operation should be wrapped in a span:

```python
from gefion.observability import create_span, set_attributes

with create_span("module.function_name", key_param=value) as span:
    result = do_work()
    set_attributes(span, result_count=len(result))
```

Key functions from `gefion.observability`:

| Function | Purpose |
|----------|---------|
| `create_span(name, **attrs)` | Context manager that creates a traced span |
| `set_attributes(span, **attrs)` | Add attributes to a span |
| `add_event(span, name, **attrs)` | Add a timestamped event to a span |
| `get_current_span()` | Get the active span from context |
| `is_enabled()` | Check if tracing is active |
| `flush_telemetry()` | Force-flush pending spans |
| `reinitialize()` | Re-check env and initialize OTEL (for late `.env` loading) |

### Pre-commit Enforcement

The pre-commit hook blocks commits of significant source files that don't import from `gefion.observability`. This ensures all new code is instrumented.

## Troubleshooting

**No traces appearing:**

1. Check services: `docker compose -f docker/tempo/docker-compose.tempo.yml ps`
2. Check Tempo: `curl -s http://localhost:3200/ready`
3. Check OTEL config: `grep OTEL .env`
4. Check endpoint: `curl -s -o /dev/null -w "%{http_code}" http://localhost:4318/v1/traces` (should return 405)
5. Check logs: `docker compose -f docker/tempo/docker-compose.tempo.yml logs tempo`

**Traces not appearing for CLI commands:**

The CLI loads `.env` in `entrypoint()` and calls `reinitialize()`. If running Python directly (not via `gefion` command), set `OTEL_ENABLED=true` before importing `gefion.observability`.

## Performance Thresholds

See [DEVELOPMENT.md](DEVELOPMENT.md) for span naming conventions and performance thresholds.
