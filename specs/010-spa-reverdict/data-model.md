# Data Model — SPA Re-Verdict for Discovery (010)

## New table: `spa_reverdicts` (append-only per-run results)

Full DDL in [contracts/sql.md](contracts/sql.md) — **proposed, awaiting owner
approval**. One row per re-verdict execution; re-runs append, "latest" is by
`created_at`. Cascades with its run (derived analysis of the run, unlike the
candidate ledger which must survive its artifacts — deliberate, declared
deletion story).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `run_id` | INTEGER NOT NULL → `regime_discovery_runs(id)` ON DELETE CASCADE | |
| `p_consistent` | DOUBLE PRECISION NOT NULL | the verdict (Hansen consistent null) |
| `p_lower` / `p_upper` | DOUBLE PRECISION NOT NULL | bracketing diagnostics |
| `level` | DOUBLE PRECISION NOT NULL | pass/fail level (default: the run's FDR rate) |
| `passed` | BOOLEAN NOT NULL | **supported**: `p_consistent ≤ level` (R9 amendment — SPA supports the family's best against its own search); precomputed for gate/display |
| `iterations` | INTEGER NOT NULL | bootstrap B |
| `seed` | BIGINT NOT NULL | RNG seed used |
| `block_length` | DOUBLE PRECISION NOT NULL | expected block length used (auto or override) |
| `family_size` | INTEGER NOT NULL | units tested — must equal the run's BH family |
| `verification` | JSONB NOT NULL | {units, max_abs_divergence, all_match: true} — always all_match on a recorded row (mismatch ⇒ refusal, no row) |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

Index: `(run_id, created_at DESC)` — latest-per-run lookups.

## Runtime structures (in-memory)

- **Test unit**: (candidate_hash, tier, signal[, bucket]) — one BH-counted
  outer test; carries its reconstructed per-date record series and its stored
  p-value for verification.
- **Record matrix**: units × outer-window dates, missing dates masked per
  unit; the joint stationary-bootstrap index path is drawn over the date axis.
- **Verification report**: per-unit (stored_p, recomputed_p, divergence);
  refusal payload names units beyond tolerance.
- **Gate check**: (dataset_version, requested budget/depth) → the N most
  recent completed runs → their latest `spa_reverdicts` rows → pass/fail; the
  satisfaction record `{gate, runs, reverdict_ids}` is embedded in the new
  run's `search_space` JSONB (no schema change).

## Invariants

- The SPA family == the realized BH family (same counted units; refused /
  uncounted excluded identically) — `family_size` recorded on the row must
  equal the run's stored `family_size`.
- A recorded row implies verification passed; drift never produces a row.
- Nothing in the run row, candidate ledger, or price data is written by the
  re-verdict (SC-1002: checksums identical before/after).
- Identical (run, iterations, seed) ⇒ identical p-values (PCG64 determinism).
