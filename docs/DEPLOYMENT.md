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

## Scheduled metadata maintenance (prod crontab)

Price ingest keeps `stock_ohlcv` fresh, but the **dimension metadata** on `stocks`
(sector, industry, asset_type) comes from different endpoints and silently stays NULL
unless something refreshes it — prod ran for weeks that way, which blocked asset-type
universe filters and sector-scoped work. Two guards now exist:

1. **Detection**: `gefion db-health` reports `dimension_coverage` (sector/industry/
   asset_type % populated + latest fundamentals date) and emits actionable warnings
   naming the fixing command. Check it after any fresh install or big ingest.
2. **Automation**: the prod user's crontab (installed 2026-07-07) runs
   ```cron
   # weekly: sector/industry/asset-type + numeric fundamentals (incremental via --max-age 30)
   10 3 * * 0  fundamentals-update --json >> ~/cron-logs/fundamentals-update.log
   # monthly: exchange/asset_type backfill from LISTING_STATUS (idempotent, 1 API call)
   40 3 1 * *  data listing-meta --json >> ~/cron-logs/listing-meta.log
   ```
   (both wrapped in `bash -lc "cd ~/src/gefion && set -a && . ./.env && set +a && …"`).
   Logs in `~/cron-logs/`; a full fundamentals pass is ~6.2k OVERVIEW calls ≈ 90 min
   at the key's rate limit. Cron can silently break — that's why the db-health
   coverage check exists independently of it.
3. **Cron observability (#120 Phase 0, 2026-07-13)**: cron lines run with
   OTEL enabled (they source `.env`, which sets `OTEL_ENABLED=true`; the
   old per-line `OTEL_ENABLED=false` overrides are removed). The exporter
   is a batched OTLP processor — async and bounded, it drops spans rather
   than blocking a job if Tempo is down. Tempo retention is 14 days
   (`block_retention: 336h`) so the performance audit can rank slow spans
   over real recurring usage; at this span volume that is a few MB/day.
4. **Nightly data pipeline** (installed 2026-07-11, operations phase): daily-7 —
   an early-morning job ingests the *previous* session, and running all seven
   days self-heals after holidays/outages with a near-instant no-op cost.
   ```cron
   # nightly pipeline — ONE dependency-ordered chain (2026-07-14/15):
   # prices -> features -> ML prediction top-up (spec 012) -> derive all series
   # data-update runs --skip-features (#120 item 1a): its local-mode feature
   # phase duplicated the chained dispatcher-mode feat-compute at ~3.5x the
   # cost — ONE feature pass, on the fast path.
   30 2 * * *  data-update --timeframe auto --skip-features --json && feat-compute --all-features --incremental --json && universe refresh --json && { ml predict-backfill --model-name prod_model --model-version v2022 --json || true; } && { macro ingest --all --json || true; } && macro derive --json
   # universe refresh (spec 015) runs AFTER feat-compute (fresh attributes)
   # and BEFORE macro derive (derived series must see fresh membership); it
   # is blocking by design — a guard refusal should stop the derive rather
   # than silently compute series over a gutted population.
   # macro ingest --all (017) refreshes every EXTERNAL series (VIX, spreads,
   # dollar, rates) before derive so composites read today's values; it is
   # non-blocking (|| true) — a dead provider must never stop the derive.
   # History: VIX went stale for two weeks (2026-07) because refresh was
   # per-series and in no cron at all.
   # predict-backfill resumes from the last stored prediction (a no-op
   # costs seconds), re-materializes the pred_* feature rows (the signal
   # surface derive reads), and runs BEFORE derive so the model series pick
   # up the new day; it is non-blocking (|| true) so a prediction failure
   # never stops the sector/market series refresh. The derived series are the
   # meta-hunt's signal universe, so a silent stall shows up as a coverage
   # refusal at the next hunt — the honest failure mode.
   # History: the top-up was a separate 02:50 cron; once the chain outgrew
   # 20 minutes it fired mid-chain ("No trading days with features") and
   # predictions lagged trading days. Folded into the chain 2026-07-14.
   # Note: plain `macro derive` covers ALL derived series since spec 013
   # ('all' = repo seeds + every DB market function), so sector and model
   # series refresh nightly with no further cron edits.
   # weekly: grade any due forward folds (trust accrual; vintage-span
   # folds are reported, never auto-graded — see USER_GUIDE)
   20 4 * * 0  regime discover accrue-folds --json
   # weekly: provider-garbage sweep over stored data
   40 3 * * 0  quality backfill --json
   # weekly: ONE whole-database pg_dump (drift-proof: includes tables no
   # curated list knows about; compressed custom format; restore = pg_restore)
   10 4 * * 0  backup -o ~/backups/gefion --timestamped --whole-db --json
   ```
4. **Backup design** (owner decisions, 2026-07-11): the safety backbone is a
   **whole-database `pg_dump`** (`--whole-db`) — curated table lists cannot rot
   because nothing is listed; a new spec's tables are included the day they
   exist. Retention is sparse and tiered (`--timestamped` root mode): everything
   kept 14 days, newest-per-month for 3 months, newest-per-year forever; the
   newest always immune; unreadable directories never pruned; a failed backup
   never deletes anything. Sparse is safe because the bulk is reproducible
   (prices re-ingest, features recompute). The parquet data types (incl.
   `irreplaceable` and `regimes`) remain for **selective** export/restore, and
   `tests/test_backup_retention.py` fails if any real table is missing from
   both the type lists and the documented exemptions — the curated lists are
   drift-checked even though the backbone no longer depends on them.

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
- **DDL on compressed hypertables** (learned rolling out 007): TimescaleDB refuses
  constraint changes on a hypertable with compression — and it's two separate walls:
  compressed *chunks* AND the compression *setting* itself. Prod-only (dev/test DBs
  aren't compressed), so a migration that passed every gate can still fail on sloth.
  The sequence that works (007's FK drop, ~35 min for 41 GB raw):
  1. Pause the compression policy: `SELECT alter_job(<job_id>, scheduled => false)`
  2. Decompress: `SELECT decompress_chunk(format('%I.%I', chunk_schema, chunk_name)::regclass, true) FROM timescaledb_information.chunks WHERE hypertable_name = '<t>' AND is_compressed` (check `df -h` first: ~7-8× the compressed size)
  3. **Record the settings** (`timescaledb_information.compression_settings`), then
     `SELECT remove_compression_policy('<t>')` and `ALTER TABLE <t> SET (timescaledb.compress = false)`
  4. Run `gefion db-migrate`
  5. Restore compression with the recorded segmentby/orderby, re-add the policy —
     then **pause the new job before any bulk write** (a freshly added policy fires
     immediately and will deadlock a concurrent backfill; 007's VIX materialization
     hit exactly this)
  6. Bulk writes, then recompress:
     `SELECT compress_chunk(..., true) ... WHERE NOT is_compressed AND range_end < now() - interval '30 days'`, re-enable the job, verify `df -h` is back.
