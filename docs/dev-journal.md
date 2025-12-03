# Dev Journal

Chronological log to make it easy to pause/resume work. Keep entries short and focused on decisions, tests, and next steps.

## 2024-11-29
- Reviewed `../folly`: core tables are `stock`, `stock_history`, `calc_store` + `calc_store_descriptor` (stores metadata like fx_name/fx_arg/source_t/source_c). Industries/sectors and `market_dates` present; schema captured in `momo.sql`.
- Reviewed `../gefjon`: modern Python/Postgres stack with ingestion service, AlphaVantage client, and schema in `src/gefjon/db/schema.py`. Dynamic feature system mirrors folly’s calc_store pattern via `feature_definitions` + `computed_features`.
- Captured the provided data model sketch (sources vs computed, data_store + descriptor, sector/industry history) in docs/data-model.md.
- Bootstrapped new repo: `pyproject.toml`, `README.md`, `src/g2` package, pytest config, and a settings loader with tests to pull ALPHAVANTAGE_API_KEY/DATABASE_URL from env or a .env file without leaking secrets.
- Added Timescale-enabled Postgres via `docker-compose.yml` (timescale/timescaledb:2.12.0-pg14) with init script `docker/initdb.d/timescaledb.sql` and `.env.example` for credentials.
- Added AlphaVantage endpoint catalog and parsers (demo-only) in `src/g2/alphavantage/catalog.py` with fixtures/tests for TIME_SERIES_DAILY_ADJUSTED and CPI demo payloads. Tests require pytest install (`pip install -e .[dev]`)—not run here.
- Added Makefile convenience targets: `make venv`, `make test`, `make db-up/db-down`, `make db-health`.
- Added Postgres schema helpers for `stocks` and `stock_prices` hypertable in `src/g2/db/schema.py` with gating tests in `tests/test_schema_stock.py` (skipped unless `ENABLE_DB_TESTS=1`). Uses Timescale `create_hypertable` and unique (data_id, date).
- Improved DB test gating to read `ENABLE_DB_TESTS` at runtime and added `make test-db` target to run with DB tests enabled.
- Added Typer-based CLI entrypoint `g2` with `ingest-prices` command; ingestion helpers in `src/g2/db/ingest.py`. CLI test is DB-gated via `ENABLE_DB_TESTS`.
- Added AlphaVantage `LISTING_STATUS` parser and fixture; introduced `AlphaVantageClient` with simple rate limiter and universe ingest helpers (`src/g2/ingest/universe.py`). New CLI subcommand `ingest-universe` supports filtering by exchange/status, optional limit, max-workers, and configurable rate limit.
- (Deprecated) Wide `stock_indicators` table removed in favor of tall `computed_features`; schema/test references dropped.
- Added local indicator engine (`src/g2/indicators/local.py`); `ingest-indicators` defaults to local compute from `stock_prices`, resuming from last indicator date, with optional API mode. Writer/fetch pools are configurable, and progress shows mode/queue/fetched. README updated with indicator usage.
- Added feature tables: `feature_definitions` now includes `store_table` (default computed_features), `store_column`, and `store_type` to drive where outputs are written. Tall `computed_features` hypertable added for flexible feature storage alongside wide fundamentals table.

## 2024-12-XX
- Aligned ingestion paths with schema-wide `data_id` naming (was `stock_id`), fixing price + indicator ingest conflicts and hypertable uniqueness.
- Fixed price batch writer indentation bug that skipped inserts; `_batch_insert_prices` now batches correctly against `data_id`.
- `ingest-indicators` now labels progress mode based on `--local/--api` and imports `time` for API retry backoff.
- Updated docs (`docs/data-model.md`, `docs/dev-journal.md`) to reflect the `computed_features` + `feature_definitions` pattern and `data_id` terminology.
- Added feature metadata usage: ingest indicators now ensures `feature_definitions`/`computed_features` tables exist, seeds indicator descriptors, and writes indicator values into the tall `computed_features` store. Added retry-on-lock/out-of-shared-memory for writes.
- Added local SMA20 and PSAR computation so indicators can be fully local for speed; SMA20/PSAR also available via API definitions for comparison.

### Next up
- Decide on package layout for data layer (e.g., `g2/db/schema.py` mirroring folly calc_store pattern).
- Add migration/DDL generation with tests (likely SQLAlchemy metadata or SQL strings) for base tables: stocks, stock_history, market_context_history, data_store/descriptor, computed tables.
- Plan ingestion adapter reuse from `gefjon` (AlphaVantage client/service) and add tests around config + rate limits.
- Wire CI-ish test target (just `pytest` for now) and keep journal updated with each increment.
