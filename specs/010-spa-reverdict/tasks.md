# Tasks: SPA Re-Verdict for Discovery

**Input**: Design documents from `/specs/010-spa-reverdict/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R8), data-model.md,
contracts/ (all present); **DDL owner-approved 2026-07-09**.

**Tests**: INCLUDED — TDD is non-negotiable (Constitution II). Every
implementation task is preceded by a failing test (Red → Green), committed
together. The statistical core is pure and seeded, so its tests are exact.

**Delivery rule** (plan, mandatory): each story lands its CLI/MCP surface and
docs *in the same increment*; the docs-drift tests enforce the mechanical part.

**Ordering note**: the plan runs the **statistical core first** (pure, no DB —
the hardest correctness risk isolated where it is cheapest to test), then
reconstruction/verification, then recording + surfaces, then the gate and the
negative control.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create `src/gefion/regimes/discovery/spa.py` scaffold (module docstring stating the SPA question vs BH; imports from `gefion.observability`) — algorithms arrive with their tests

## Phase 2: Foundational — the statistical core (pure, no DB) 🎯 hardest risk first

- [ ] T002 Write `tests/test_spa_statistics.py` (part 1, RED): stationary bootstrap index paths — expected block length honored (mean geometric block ≈ target under seed), joint application (same path across units), determinism under seed, wrap-around correctness; Politis–White automatic block length on a seeded AR(1) series lands in the theoretically sensible range, floored at 1 and capped at n/3
- [ ] T003 Implement the stationary bootstrap + Politis–White block length in `src/gefion/regimes/discovery/spa.py` (GREEN)
- [ ] T004 Write `tests/test_spa_statistics.py` (part 2, RED): Hansen SPA over a synthetic unit matrix — (a) size: pure-noise units at α=0.05 reject within the exact binomial 99% bound over seeded repetitions; (b) power: one planted strong unit → small p_consistent; (c) ordering: p_lower ≤ p_consistent ≤ p_upper on every seeded case; (d) reproducibility: identical inputs+seed ⇒ byte-identical p-values; (e) family-of-one degrades gracefully to the single-unit test
- [ ] T005 Implement the Hansen SPA statistic (studentized max, HAC scale consistent with the block length, consistent/lower/upper recentering) in `src/gefion/regimes/discovery/spa.py` (GREEN)

**Checkpoint**: the statistics are proven correct in isolation — everything DB-ward consumes a trusted core.

---

## Phase 3: US1+US2 — Reconstruction, verification, and the verdict (P1) 🎯 MVP

**Goal**: rebuild a stored run's counted family via the run's own code paths,
verify against stored p-values, and produce the SPA verdict.
**Independent test**: seeded synthetic run → planted edge rejects, noise
doesn't; perturbing one price row → refusal naming the divergent unit.

- [ ] T006 [US1] Write `tests/test_spa_reconstruction.py` (RED, DB): from a completed synthetic run (built with the 006 `discovery_synth` + runner over the test DB), rebuild `DiscoveryConfig` from `search_space`, the market via `signals.load_market_data` (dataset/universe/max-date), and the outer window from `segregation`; recompute every counted unit's test via the same `edges.*` functions; the recomputed p-values reproduce the stored ones within tolerance (1e-9 abs / 1e-6 rel); the unit set equals the BH family exactly (refused/uncounted excluded)
- [ ] T007 [US1] Implement reconstruction (`spa.reconstruct_family(conn, run)`) in `src/gefion/regimes/discovery/spa.py`, calling the run's own code paths per research R1 (GREEN)
- [ ] T008 [US2] Extend `tests/test_spa_reconstruction.py` (RED): perturb one outer-window price row → verification refuses naming the divergent unit(s) and magnitudes, and no verdict is produced; `family_size` 0 / no counted candidates → honest "nothing to test" refusal; outer window below the 20-observation floor → refusal naming the floor
- [ ] T009 [US2] Implement verification + refusal taxonomy (`spa.verify`, `SpaRefusal`) (GREEN)
- [ ] T010 [US1] Write `tests/test_spa_reverdict.py` (RED, DB): end-to-end `spa.reverdict(conn, run, iterations, seed)` on the synthetic runs — planted-edge run yields small p_consistent, noise run large; same seed ⇒ identical p-values; ledger and price rows byte-identical before/after (checksums, SC-1002)
- [ ] T011 [US1] Implement the end-to-end re-verdict orchestration (reconstruct → verify → bootstrap → verdict) with spans (family size, iterations, block length, verification outcome, p-values) (GREEN)

**Checkpoint**: MVP — a trustworthy selection-aware verdict exists for any stored run, with drift refusal proven.

---

## Phase 4: Recording + the command (US1 completion + surfaces)

- [x] T012 [US1] Write `tests/test_spa_reverdict.py` (schema part, RED): after db-init, `spa_reverdicts` exists with the approved shape (FK CASCADE to runs, index, NOT NULLs); recording is append-only (two executions → two rows; nothing updated); `family_size` on the row equals the run's stored family_size
- [x] T013 [US1] Apply the approved DDL via the two-file rule: `sql/schema.sql` + `sql/migrations/20260710_000001_spa_reverdicts.sql`; add the `TABLE_PURPOSE` entry in `scripts/gen_data_dictionary.py`; regenerate `docs/DATA_DICTIONARY.md` in the same commit (GREEN)
- [x] T014 [US1] Ledger API: `record_spa_reverdict` / `latest_spa_reverdict` / `list_spa_reverdicts` in `src/gefion/regimes/discovery/ledger.py` (tests first in `tests/test_spa_reverdict.py`)
- [x] T015 [US1] CLI `gefion regime discover spa <run>` (`--iterations 1000`, `--seed` default run seed, `--level` default run FDR rate, `--block-length` expert override) in `src/gefion/cli.py`; refusals exit non-zero with the reason verbatim; MCP `regime_discover_spa` in `mcp-server/server.py` (source-inspection tests first); README row + USER_GUIDE + MCP_WORKFLOWS entries; docs-drift green

---

## Phase 5: US3 — The verdict is visible where verdicts live (P2)

- [x] T016 [US3] Write surfacing tests (extend `tests/test_spa_reverdict.py`, RED): `discover show`/`verdicts` display the latest SPA (p_consistent, level, pass/fail, when) or `SPA: not yet run`; `discover grades` flags an admitted edge whose run's latest SPA fails, while its BH verdict and trust grade are unchanged
- [x] T017 [US3] Implement the show/verdicts SPA line and the grades flag in `src/gefion/cli.py` (+ the same fields in the MCP payloads) (GREEN); `/gefion` operator routing ("is this family trustworthy at scale" → `regime_discover_spa`; a flag is not a demotion)

---

## Phase 6: US4 — The budget gate is enforced in code (P2)

- [x] T018 [US4] Write `tests/test_spa_gate.py` (RED, DB): `discover start` with `budget > 200` or `depth > 2` and no passing latest SPA on the 2 most recent completed runs (same dataset version) → refused naming the gate and the satisfying command; with passing re-verdicts → accepted and `{gate: "spa", runs, reverdict_ids}` recorded in the new run's `search_space`; within-cap starts byte-identical to today (no gate interference)
- [x] T019 [US4] Implement `V1_MAX_BUDGET = 200` / `V1_MAX_DEPTH = 2` constants + the gate in the runner's config validation (`src/gefion/regimes/discovery/runner.py`), threaded through the CLI start path (GREEN)

---

## Phase 7: US5 — The negative control (P3)

- [x] T020 [US5] Write `tests/test_spa_negative_control.py` (RED): M=40 seeded pure-noise families through the FULL pipeline (synthetic runs → reconstruct → verify → SPA at B=200) — rejections at α=0.05 within the exact binomial 99% bound; one planted-edge family rejects; wall-clock under ~60s
- [x] T021 [US5] Wire the control into CI expectations (it runs in the normal suite; GREEN by construction of Phases 2–3 — any failure here is a real size/power defect)

---

## Phase 8: Polish & Cross-Cutting

- [x] T022 Learning materials: `.claude/commands/gefion-learn.md` Module 10 aside (BH corrects the p-values you have; SPA models the search that produced them) + checkpoint (why is the drift refusal a feature?); curriculum drift test green
- [x] T023 Observability pass: `OTEL_ENABLED=true` re-verdict on a dev run; `gefion span-check` — spans parented, no orphans; runtime sanity (B=1000, v1 family in seconds–minutes)
- [x] T024 Full-suite pre-flight: drop `gefion_test`, complete suite against a fresh DB (exit code captured — the pipe-masking lesson); docs-drift + dictionary `--check` green
- [ ] T025 Update `.specify/memory/progress.md`; PR the branch, merge on green
- [ ] T026 Post-merge on sloth: `db-migrate`; `regime discover spa` over the completed prod runs (incl. the two admitted regimes — SC-1002 verbatim: verdicts recorded, zero ledger rows modified, checksums identical); report the verdicts to the owner; issue #87 updated (post-run re-verdict increment done; in-run gate + signal_source rungs remain)

---

## Dependencies & Story Completion Order

```
Setup (T001)
  └─> Statistical core (T002–T005: pure, seeded)   🎯 hardest risk first
        └─> US1+US2 reconstruction/verification/verdict (T006–T011)  🎯 MVP
              └─> Recording + command (T012–T015)
                    ├─> US3 surfacing (T016–T017)
                    └─> US4 gate (T018–T019)
                          └─> US5 negative control (T020–T021)
                                └─> Polish (T022–T026)
```

Parallel opportunities: T016–T017 (surfacing) and T018–T019 (gate) are
independent after T014; T022 alongside T020–T021.

## Implementation Strategy

- **The statistical core lands first and alone** — pure, seeded, property-
  tested; every downstream consumer builds on proven size/power/reproducibility.
- **MVP = through Phase 3**: a trustworthy verdict for any stored run, drift
  refusal included, even before durable recording exists.
- **The gate (Phase 6) is the payoff**: #87's "required before raising
  budgets" becomes enforced configuration validation.
- Everything through T025 runs on this machine; T026 is the only sloth step —
  and it is the acceptance case (the two admitted prod regimes).

## Success Criteria Mapping

SC-1001 (planted/noise + reproducibility) → T004–T005, T010–T011 · SC-1002
(prod runs, zero mutation) → T010 (checksums) + T026 · SC-1003 (drift refusal)
→ T008–T009 · SC-1004 (gate) → T018–T019 · SC-1005 (negative control) →
T020–T021 · SC-1006 (surfacing + flag, no demotion) → T016–T017
