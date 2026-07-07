# Phase 1 Data Model: Agentic Regime Discovery (006)

**All DDL is PROPOSED for owner approval (Schema Governance) — see contracts/sql.md.**
Discovered regimes themselves reuse `regime_definitions` (`origin='machine'`); everything
below is the discovery bookkeeping that makes the search honest.

## Entities

### RegimeDiscoveryRun → `regime_discovery_runs`

| Field | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `name` | text | run slug |
| `seed` | bigint | reproducibility (FR-111) |
| `search_space` | JSONB | pre-registration: atom library, depth K, budgets, **signal_source**, **grading_scheme**, **universe_filter chain** (incl. explicit passthrough), tier flags |
| `segregation` | JSONB | outer-holdout boundaries, inner window, fresh-holdout reserve block |
| `reserve_consumed` | boolean | fresh-holdout single-use tracking (R4) |
| `family_size` | integer | realized test count — the FDR denominator actually used |
| `status` | text | `pre_registered` → `enumerated` → `evaluated` → `complete` / `invalid` |
| `dataset_version` | text | provenance (005 FR-023) |
| `created_at` / `completed_at` | timestamptz | |

Lifecycle rule: candidates may only be evaluated when status ≥ `enumerated` (candidate set
frozen — the T4 guard); a run that cannot prove segregation → `invalid`, no verdicts.

### CandidateLedgerEntry → `regime_candidates`

| Field | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `run_id` | FK → regime_discovery_runs | |
| `candidate_hash` | text | canonical-AST SHA (dedup/resume identity) |
| `expression` | JSONB | the RegimeExpression AST |
| `tier` | text | `interaction` \| `grammar` \| `expressive` |
| `provenance` | JSONB | seeding principle / detector method / atoms used |
| `results` | JSONB | per-(signal×bucket) p-values, effective-N, interaction coefs |
| `counted_in_family` | boolean | silent-survivorship guard (FR-104) |
| `verdict` | text | `admitted` \| `rejected` \| `refused_low_power` \| `refused_degenerate` \| `refused_unstable` |

UNIQUE(run_id, candidate_hash).

### DiscoveryDiagnostics → `discovery_diagnostics`

| Field | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `run_id` | FK | |
| `kind` | text | `budget_exhausted` \| `depth_capped` \| `min_sample_refusal` \| `uncomputable_proposal` \| `degenerate` \| `unstable` \| `reserve_refused` |
| `detail` | JSONB | quantitative reason (e.g. `{"effective_n": 3, "floor": 20}`) |
| `sample_dependent` | boolean | re-evaluate on new dataset vs structural/accumulate |
| `dataset_version` | text | |

### TrustGrade → `regime_trust_grades`

| Field | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `candidate_id` | FK → regime_candidates (admitted) | |
| `fold` | integer | 1 = probation window; increments per scheduled re-test |
| `confirmed` | boolean | forward-only outcomes ONLY (interface-enforced, R8) |
| `descriptive` | boolean | true for backward era-slices — display context, never graded |
| `detail` | JSONB | metrics of the fold test |
| `graded_at` | timestamptz | |

Grade = aggregation over `descriptive=false` rows; regime-limited flag derives from early
fold failures; probation tightness maps from the running grade.

## Relationships

```
regime_discovery_runs 1─∞ regime_candidates 1─∞ regime_trust_grades
regime_discovery_runs 1─∞ discovery_diagnostics
regime_candidates (admitted) ──upsert──> regime_definitions (origin='machine')  [005]
regime_definitions 1─∞ regime_labels                                            [005]
```

## Pluggable seams (interfaces, not tables)

- **SignalSource** — `records(feature|model|strategy, context) → per-observation edge records`;
  v1 implementation: `features`
- **GradingScheme** — `register / record_forward_result / grade`; no backward-confirmation API
- **UniverseFilter** — chainable `apply(symbols) → symbols`; built-ins: `test_tickers`,
  `asset_type`, `passthrough` (explicit)
