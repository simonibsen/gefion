# Production Deployment Runbook

How to deploy gefion to a fresh Linux production host. Written from the first real
deployment (Ubuntu, 8 cores / 16 GB / 2 TB); update it as the process evolves.

## Architecture: three tiers

| Tier | Where | Role |
|---|---|---|
| **Dev** | workstation | Write code, TDD, tiny dataset — fast iteration |
| **Staging** (optional) | prod host, non-prod ports | Validate against real-data *samples* drawn from prod locally |
| **Prod** | prod host | Full data, ingestion, training, discovery runs |

Code flows through git only (dev → PR → release → prod `git pull`). Never edit code on
the prod host; if a deploy uncovers a bug, fix it in the dev repo and pull the merge.

## Fresh install

```bash
# 1. Clone at the latest release tag (never main)
cd ~/src && git clone https://github.com/simonibsen/gefion.git
cd gefion && git checkout vX.Y.Z

# 2. Environment — strong password, never the .env.example default
PW=$(openssl rand -hex 24)
cat > .env <<EOF
POSTGRES_USER=gefion
POSTGRES_PASSWORD=$PW
POSTGRES_DB=gefion
POSTGRES_PORT=5432
POSTGRES_DATA_DIR=./db
DATABASE_URL=postgresql://gefion:$PW@localhost:5432/gefion
OTEL_ENABLED=true
ALPHAVANTAGE_API_KEY=<key>
EOF
chmod 600 .env

# 3. Services (images are version-pinned in the compose files — keep it that way)
docker compose up -d postgres
docker compose -f docker/tempo/docker-compose.tempo.yml up -d

# 4. Python env
python3 -m venv .venv || python3 -m venv --without-pip .venv   # see gotcha below
.venv/bin/pip --version || curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -e ".[ml_extended,ui]"

# 5. Initialize + verify
.venv/bin/gefion db-init          # schema + migrations + seeds, idempotent
docker exec -i gefion-postgres psql -U gefion -d gefion < sql/enable_compression.sql
.venv/bin/gefion db-health        # expect: pending migrations 0, compression 2/2
.venv/bin/gefion health           # expect: postgres/tempo/grafana healthy
```

**Enable compression BEFORE ingesting** — at full-history scale the features hypertable
is the disk driver, and compressing after the fact is a slow rewrite.

## Data build

Run long ingestion in tmux so it survives the SSH session; expect block-buffered
output (progress appears in bursts — check the DB for a live pulse):

```bash
tmux new-session -d -s ingest \
  '.venv/bin/gefion universe-ingest --exchange NASDAQ --timeframe auto \
     --max-workers 4 --writer-workers 1 2>&1 | tee ~/ingest-nasdaq.log'

# Live pulse (works even while the log looks quiet):
docker exec gefion-postgres psql -U gefion -d gefion -t -c \
  "SELECT (SELECT count(*) FROM stocks), (SELECT count(*) FROM stock_ohlcv)"
```

Measured costs (first NASDAQ production ingest, premium key):
- **Daily prices, full history**: 1 call/symbol at ~69 symbols/min sustained →
  **all of NASDAQ (6,193 symbols, 12.4M bars, 26.7 years) ≈ 90 minutes**, zero errors,
  2.46 GB with compression enabled
- **Quarterly financials backfill**: 4–5 calls/symbol → the same universe ≈ days
- **Feature computation**: no API cost, CPU-bound; `feat-compute --all-features`
  (note: no `--exchange` flag — scope with `--symbols`/`--listings-file` or run over
  everything); ~200M rows for the full NASDAQ history ≈ hours

Order: prices → (optionally fundamentals/financials) → `feat-compute` → datasets.

**Universe quality**: an exchange's "Active" listing is bigger than its common-stock
universe — ETFs, warrants, units, and NASDAQ **test tickers** (ZVZZT, ZWZZT, ZXZZT…)
all come along. Filter before using it as a research/backtest universe (see the
exchange-filter backlog item).

## Remote access

Prefer SSH tunnels over exposing services:

```bash
ssh prodhost -N -L 3000:localhost:3000   # Grafana
ssh prodhost -N -L 8501:localhost:8501   # gefion UI
ssh prodhost -N -L 5433:localhost:5432   # prod DB, mapped to a local port
```

## Staging on the prod host (optional)

A second, resource-capped environment on non-prod ports for validating against real
data. Guardrails are the point:

- Separate checkout (`~/src/gefion-staging`), separate postgres container on **6432**
  with a **different password**; prod credentials never appear in the staging `.env`
- **No API key in staging** — it populates only by sampling from the prod DB locally
  (zero API quota, seconds instead of hours)
- Cap containers (`--memory`, `--cpus`); don't run staging training during prod ingest

## Gotchas (learned the hard way)

- **Ubuntu venv**: `python3 -m venv` fails without the `python3.X-venv` apt package;
  without sudo, bootstrap via `python3 -m venv --without-pip` + get-pip.py.
- **Unpinned images**: `grafana/tempo:latest` on a fresh pull ≠ the stale `latest` a
  dev machine cached months ago. Compose files pin versions; keep them pinned.
- **Undeclared transitive deps**: a fresh resolve is the only honest dependency test —
  dev machines hide missing declarations (e.g. `click` arriving via other packages).
- **Old deployments**: decommission fully (containers, volumes, repo → `.old`), but
  keep the old volume/dir until the new stack is verified. Fresh install + re-ingest
  beats in-place migration once schemas have drifted.
