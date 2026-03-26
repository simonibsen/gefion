# OpenTelemetry Observability (Grafana Tempo)

This document describes how to use OpenTelemetry with Grafana Tempo for performance monitoring and investigation in the Gefion system.

## Quick Links

- **[Tempo Quick Start](TEMPO_QUICKSTART.md)** - Start Tempo + Grafana locally
- **[Performance Optimization Workflow](PERFORMANCE_WORKFLOW.md)** - Use traces to drive tuning

## Overview

The Gefion system includes toggle-able OpenTelemetry instrumentation that can be enabled on-demand. When enabled, it traces:

- **CLI commands** (e.g. `cli.feat-compute`, `cli.data-update`)
- **Feature computation pipeline** (e.g. `compute_features`, `process_function_group`, `insert_computed_features`)
- **Database activity** (auto-instrumented psycopg spans + `db.get_connection`)
- **External API calls** (e.g. `alphavantage.api_call`)

Observability is **zero overhead** when `OTEL_ENABLED` is not set or is `false` (the default).

## Quick Start

1. Start Tempo + Grafana:

```bash
docker compose -f docker/tempo/docker-compose.tempo.yml up -d
```

2. Enable tracing:

```bash
export $(cat .env.example | xargs)
```

3. Run a command:

```bash
gefion feat-compute --symbols AAPL --function-names indicator --profile
```

4. Sanity-check ingestion:

```bash
gefion span-check
```

`gefion span-check` prints recent traces plus a direct Tempo API link for the selected trace, which is handy during development performance checks.

5. View traces:

- Open http://localhost:3000
- Explore → Tempo
- Query: `service.name = "gefion"`

## Development Use

After performance-sensitive changes, run:

```bash
gefion span-check
```

Use the printed Tempo API link to inspect the selected trace details without hunting in the UI, then compare traces before/after your change.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `false` | Enable/disable OpenTelemetry |
| `OTEL_SERVICE_NAME` | `gefion` | Service name in traces |
| `OTEL_EXPORTER` | `otlp` | Exporter type: `otlp` or `console` |
| `OTEL_OTLP_ENDPOINT` | `http://localhost:4317` | Tempo OTLP gRPC endpoint |
| `OTEL_SAMPLING_RATE` | `1.0` | Sampling rate (0.0-1.0) |

### Example configurations

Full tracing (investigation mode):

```bash
export $(cat .env.example | xargs)
```

Low-overhead sampling (1%):

```bash
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SAMPLING_RATE=0.01
```

Console output (no Tempo required):

```bash
export OTEL_ENABLED=true
export OTEL_EXPORTER=console
```

## Tempo API checks

Quick checks to confirm the Tempo API is responding and receiving traces:

```bash
curl -s http://localhost:3200/api/search
curl -s "http://localhost:3200/api/search?tags=service.name=gefion&limit=5"
```

## Troubleshooting

No traces appearing:

- Confirm services: `docker compose -f docker/tempo/docker-compose.tempo.yml ps`
- Confirm Grafana: `curl -s http://localhost:3000/api/health`
- Confirm Tempo API: `curl -s http://localhost:3200/api/search`
- Confirm Gefion config: `env | rg '^OTEL_'`
- Run: `gefion span-check` (shows trace counts + span counts)
- Check Tempo logs: `docker compose -f docker/tempo/docker-compose.tempo.yml logs tempo`
