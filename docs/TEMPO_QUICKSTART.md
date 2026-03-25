# Grafana Tempo Quick Start

Modern tracing with Grafana Tempo.

## Why Tempo?

- **Better UI**: Grafana's modern interface with better search and filtering
- **More efficient**: Designed for high-volume traces
- **Integrated**: Works seamlessly with Grafana dashboards
- **Object storage ready**: Can use S3, GCS, Azure Blob (though we use local for simplicity)

## Setup (5 minutes)

### 1. Start Tempo + Grafana

```bash
docker compose -f docker/tempo/docker-compose.tempo.yml up -d
```

This starts:
- **Tempo** on port 3200 (traces backend)
  - Accepts OTLP on ports 4317 (gRPC) and 4318 (HTTP)
- **Grafana** on port 3000 (UI)

### 2. Verify Services

```bash
# Check containers are running
docker compose -f docker/tempo/docker-compose.tempo.yml ps

# Check Grafana is accessible
curl http://localhost:3000/api/health
```

### 3. Enable Tracing in g2

```bash
export $(cat .env.example | xargs)
# or:
# export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_OTLP_ENDPOINT=http://localhost:4317
```

### 4. Run g2 Command

```bash
g2 feat-compute --symbols AAPL --function-names indicator
```

### 5. View Traces in Grafana

1. Open http://localhost:3000 in your browser
2. Click "Explore" (compass icon on left sidebar)
3. Select "Tempo" from the datasource dropdown
4. Click "Search" tab
5. Set "Service Name" = "g2"
6. Click "Run query"
7. Click on any trace to see the waterfall view

## Grafana UI Features

### Search Capabilities

**By service:**
- Service Name = "g2"

**By operation:**
- Span Name = "compute_features"
- Span Name = "insert_computed_features"

**By duration:**
- Min Duration = "1s" (find slow operations)

**By tags/attributes:**
- Tags: `symbol="AAPL"`
- Tags: `function_name="indicator"`
- Tags: `error_rate>0.1`

### Trace View Features

- **Waterfall view**: See timing of all spans
- **Span details**: Click any span to see attributes, events, logs
- **Service graph**: Visual map of service interactions
- **Linked metrics**: Jump to related metrics (if configured)

### Useful Queries

**Find slow feature computations:**
1. Search tab
2. Service Name = "g2"
3. Span Name = "compute_features"
4. Min Duration = "5s"

**Find errors:**
1. Search tab
2. Service Name = "g2"
3. Tags: `error=true` or `status.code=ERROR`

**Compare symbols:**
1. Search for traces with `symbol="AAPL"`
2. Note the duration
3. Search for traces with `symbol="MSFT"`
4. Compare durations

## Test the Setup

Run the test script:

```bash
.venv/bin/python tests/test_otel_smoke.py
```

Then in Grafana:
1. Go to Explore → Tempo
2. Search for service "g2"
3. You should see a trace with spans:
   - `test_parent`
   - `test_child_1`
   - `test_child_2`

You can also sanity-check via the CLI:

```bash
g2 span-check
```

## Configuration Options

### Adjust Retention

Edit `docker/tempo/tempo-config.yaml`:

```yaml
compactor:
  compaction:
    block_retention: 24h  # Keep traces for 24 hours (default: 1h)
```

Then restart:

```bash
docker compose -f docker/tempo/docker-compose.tempo.yml restart tempo
```

## Troubleshooting

### No traces appearing

**Check Tempo is receiving data:**
```bash
# Should show trace ingestion metrics
curl http://localhost:3200/metrics | grep tempo_distributor_spans_received_total
```

**Check g2 configuration:**
```bash
echo $OTEL_ENABLED  # Should be: true
echo $OTEL_EXPORTER  # Should be: otlp
echo $OTEL_OTLP_ENDPOINT  # Should be: http://localhost:4317
```

**Check Tempo logs:**
```bash
docker compose -f docker/tempo/docker-compose.tempo.yml logs tempo
```

### Grafana not connecting to Tempo

**Check datasource configuration:**
1. Grafana → Configuration (gear icon) → Data sources
2. Click "Tempo"
3. URL should be: `http://tempo:3200`
4. Click "Test" - should show success

**Restart Grafana:**
```bash
docker compose -f docker/tempo/docker-compose.tempo.yml restart grafana
```

### Traces are delayed

Tempo batches traces for efficiency. Wait 10-30 seconds after running a command, then refresh the search.

## Shutdown

```bash
# Stop services
docker compose -f docker/tempo/docker-compose.tempo.yml down

# Stop and remove data
docker compose -f docker/tempo/docker-compose.tempo.yml down -v
```

## Integration with Existing Postgres Setup

If you already have a `docker-compose.yml` for Postgres, you can merge them:

```bash
# Start both Postgres and Tempo
docker compose -f docker-compose.yml -f docker/tempo/docker-compose.tempo.yml up -d
```

Or add Tempo services to your existing `docker-compose.yml`.

## Next Steps

- **Enable profiling**: Use `gefion feat-compute --profile` to get detailed timing breakdowns
- **Create dashboards**: Build Grafana dashboards showing trace metrics over time
- **Add metrics**: Send metrics to Prometheus and correlate with traces
- **Production setup**: Configure S3/GCS storage for long-term retention

## Resources

- [Grafana Tempo Documentation](https://grafana.com/docs/tempo/latest/)
- [OpenTelemetry OTLP](https://opentelemetry.io/docs/reference/specification/protocol/otlp/)
- [g2 Observability Documentation](docs/OBSERVABILITY.md)
