# Implementation Plan: SPA Re-Verdict for Discovery

**Branch**: `010-spa-reverdict` | **Date**: 2026-07-09 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/010-spa-reverdict/spec.md`

## Summary

A post-run **Hansen SPA re-verdict** over a completed discovery run's candidate
ledger — the selection-aware test that gates raising discovery budgets (issue
#87). The command reconstructs each counted candidate's per-observation records
from the ledger's stored expressions plus the run's pre-registration,
**verifies** the reconstruction by reproducing the stored per-test p-values
(refusing honestly on drift), then runs a joint stationary bootstrap
(recomputing each test unit's statistic per resample, studentized, recentered
null) and records the consistent/lower/upper SPA p-values append-only in a new
`spa_reverdicts` table (DDL proposed, owner approval pending). Results surface
in `discover show`/`verdicts` and the grades view; `discover start` enforces
the budget gate in config validation. A seeded CI negative control mirrors
006's noise-run discipline.

## Technical Context

**Language/Version**: Python 3.10+ (existing codebase)
**Primary Dependencies**: numpy (bootstrap + statistics — stationary bootstrap,
Politis–White block length, and Hansen SPA are implemented directly, ~150
lines; no new external dependency), existing `gefion.regimes.discovery`
(runner/edges/signals/segregation/ledger), psycopg, `gefion.observability`
**Storage**: ONE new table — `spa_reverdicts` (append-only per-run results;
plain relational, 006-ledger precedent). DDL is PROPOSED in contracts/sql.md —
**awaiting owner approval**. The re-verdict itself reads ledger + price data
and writes only this table.
**Testing**: pytest; statistical core is pure/seeded (no DB); reconstruction +
recording are DB tests via `schema.test_db_url()`; the negative control is a
seeded CI test like 006's
**Target Platform**: dev + prod (sloth — the two admitted runs are the
acceptance case)
**Project Type**: single project — new `src/gefion/regimes/discovery/spa.py`
plus small hooks in runner config validation, ledger, and CLI/MCP
**Performance Goals**: B=1000 iterations over a v1 family (≤200 units, outer
windows of ~30–90 observations) completes in seconds–minutes (vectorized numpy:
resample indices once per iteration, recompute unit statistics as array ops)
**Constraints**: read-only over ledger and market rows (SC-1002 checksums);
seeded reproducibility (identical inputs+seed → identical p-values); honest
refusals (drift, missing data, empty family, short window); append-only records
**Scale/Scope**: families ≤200 units at v1; retroactive over runs 1–7 on prod

## Constitution Check

*GATE: evaluated against constitution v1.9.0 — PASS (one approval pending, by design).*

| Principle | Status | Notes |
|---|---|---|
| I. Database-First | PASS | Re-verdict results are DB rows; two-file rule for the one new table; reconstruction reuses the same edges/signals code paths the run used (no parallel implementations). |
| II. TDD | PASS | Statistical core is pure and property-testable (size/power under seeds); every phase Red→Green. |
| III. CLI-First | PASS | `regime discover spa <run>` + mirrored MCP tool in the same increment. |
| IV. Observability | PASS | Spans: reconstruction (n units, verification outcome), bootstrap (iterations, block length), verdict (p-values). |
| V. CLI Presentation | PASS | `out.*` helpers; `--json` clean payload. |
| VI. Simplicity | PASS | No new dependency: the three algorithms (stationary bootstrap, automatic block length, SPA recentering) are short, well-specified numpy routines — a heavyweight stats package would be a bigger surface than the code. |
| Schema Governance | **PENDING** | `spa_reverdicts` DDL proposed, not executed (contracts/sql.md). Recording tasks block on owner approval; the statistical core does not. |
| Secrets | PASS | None. |

*Post-Phase-1 re-check: no new violations; the single pending item remains the DDL approval.*

## Project Structure

### Documentation (this feature)

```text
specs/010-spa-reverdict/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R8
├── data-model.md        # Phase 1: spa_reverdicts + verification record
├── quickstart.md        # Phase 1: end-to-end walkthrough
├── contracts/
│   ├── sql.md           # PROPOSED DDL (owner approval required)
│   ├── cli.md           # regime discover spa; show/verdicts/grades surfacing; start gate
│   └── mcp.md           # regime_discover_spa tool
└── tasks.md             # Phase 2 (/speckit.tasks — not here)
```

### Source Code (repository root)

```text
src/gefion/regimes/discovery/
├── spa.py               # NEW: reconstruction, verification, stationary bootstrap,
│                        #      Politis–White block length, Hansen SPA statistic
├── runner.py            # config validation gains the budget gate (V1 caps + SPA check)
├── ledger.py            # record/read spa_reverdicts; latest-per-run helper
└── (edges.py, signals.py, segregation.py reused as-is — reconstruction calls them)

sql/schema.sql + sql/migrations/2026MMDD_NNNNNN_spa_reverdicts.sql   # after approval
src/gefion/cli.py        # regime discover spa; show/verdicts display; grades flag
mcp-server/server.py     # regime_discover_spa (mutating: writes one record; confirm)

tests/
├── test_spa_statistics.py      # pure: bootstrap properties, block length, SPA size/power (seeded)
├── test_spa_reconstruction.py  # DB: rebuild config/market from a stored run; verification
│                               #     reproduces stored p-values; drift → refusal
├── test_spa_reverdict.py       # DB: end-to-end command; append-only recording; refusals
├── test_spa_gate.py            # discover start refuses above-cap without passing SPA
└── test_spa_negative_control.py# seeded noise families: size ≤ nominal; planted edge: power
```

**Structure Decision**: one new module beside the existing discovery machinery;
reconstruction deliberately *calls the same functions the run used*
(`edges.tier1_interaction_test`, `tier2_bucket_tests`, `causal_labels`,
`signals.load_market_data`, segregation date math) so verification compares
like with like — a parallel reimplementation could never certify drift vs
divergence.

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **Three interfaces**:
  | Operation | CLI | MCP | UI |
  |---|---|---|---|
  | Re-verdict a run | `regime discover spa <run> [--iterations 1000] [--seed N] [--level 0.01]` | `regime_discover_spa` (mutating — one appended record; confirm first) | Regimes/Discovery page shows the SPA line per run |
  | See it | `discover show` / `verdicts` (SPA beside BH; "not yet run" when absent) | same payloads | same |
  | Grades flag | `discover grades` flags admitted edges whose family's latest SPA fails | same | same |
  | The gate | `discover start` refuses above-cap budget/depth without a passing SPA | same | — |
- **Documentation**: README (command row), USER_GUIDE + the discovery section
  (what SPA answers vs BH; why reconstruction must verify first),
  MCP_WORKFLOWS (tool + honesty rules), `/gefion` routing ("is this family
  trustworthy at scale"), DATA_DICTIONARY regen (new table).
- **Learning materials**: gefion-learn **Module 10** aside — BH corrects the
  p-values you have; SPA models the *search* that produced them — plus
  checkpoint: *why does the re-verdict refuse when it cannot reproduce the
  ledger's stored p-values, and why is that refusal a feature?*
- **Delivery rule**: surfaces + docs land per increment (docs-drift enforced).

## Increment plan (safety & value ordering)

1. **Statistical core first (pure, no DB)** — stationary bootstrap +
   Politis–White block length + Hansen SPA statistic, property-tested under
   seeds (size on noise, power on planted effects, reproducibility). The
   hardest correctness risk isolated where it is cheapest to test.
2. **Reconstruction + verification** — rebuild config/market from a stored
   run via the run's own code paths; reproduce stored p-values; drift refusal.
3. **DDL (after approval) + recording** — `spa_reverdicts` via two-file rule;
   append-only ledger API; dictionary regen.
4. **The command** — `regime discover spa` + MCP + refusal surface; spans.
5. **Surfacing** — show/verdicts SPA line; grades flag (no auto-demotion).
6. **The gate** — V1 caps as named constants; `discover start` validation +
   gate-satisfaction recorded in pre-registration.
7. **Negative control** — seeded CI test: size ≤ nominal on noise families,
   power on planted edge.
8. **Polish** — curriculum, span-check, fresh-DB suite, PR; then the
   retroactive re-verdict of the two admitted prod runs (SC-1002) post-merge.

## Complexity Tracking

*No constitutional violations to justify.* The pending item is the standard
Schema Governance approval for `spa_reverdicts` (contracts/sql.md). No new
external dependency — the statistical routines are short, deterministic numpy
implementations with the papers' exact recipes recorded in research.md.
