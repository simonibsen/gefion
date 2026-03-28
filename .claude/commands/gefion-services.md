---
description: Start or stop all gefion development/production services (PostgreSQL, Tempo, Grafana)
---

## Arguments

$ARGUMENTS

## Instructions

Parse the arguments provided above. Supported forms:

| Command | Meaning |
|---------|---------|
| *(empty)* or `start` or `start dev` | Start all services in **dev** mode |
| `start prod` | Start all services in **prod** mode |
| `stop` | Stop all services |
| `status` | Show service status |

### Start Services

1. **Determine env file** based on mode:
   - **Dev** (default): `--env-file .env`
   - **Prod**: `--env-file .env.prod`

2. **Start PostgreSQL** (from repo root):
   ```bash
   docker compose --env-file <ENV_FILE> up -d postgres
   ```

3. **Start Tempo + Grafana** (observability stack):
   ```bash
   docker compose --env-file <ENV_FILE> -f docker/tempo/docker-compose.tempo.yml up -d
   ```

4. **Wait for health** — poll `docker compose ps` until postgres is healthy (up to 30s).

5. **OTEL smoke test** — verify tracing works end-to-end:
   ```bash
   bash scripts/otel_smoke_test.sh
   ```
   This emits a test span and confirms it arrives in Tempo.

6. **Report status** — show which services are running and on which ports. For dev mode, remind the user that postgres is on the non-standard port from `.env` (e.g., 6432). Also report OTEL status (connected/disconnected).

### Stop Services

Stop in reverse order:

1. Stop Tempo + Grafana:
   ```bash
   docker compose -f docker/tempo/docker-compose.tempo.yml down
   ```

2. Stop PostgreSQL:
   ```bash
   docker compose down
   ```

Report that all services have been stopped.

### Status

Run both compose files' `ps` commands and present a unified table:

```bash
docker compose ps
docker compose -f docker/tempo/docker-compose.tempo.yml ps
```

Show service name, status, and mapped ports.
