# Research — SPA Re-Verdict for Discovery (010)

Decisions resolving the plan's design questions, grounded in the discovery
code and the SPA/bootstrap literature. The three owner-level choices (post-run
vs in-run; Hansen SPA vs White RC; stationary bootstrap joint across
candidates) were made by the owner on 2026-07-09 and recorded on issue #87 —
they are inputs here, not open questions.

## R1 — Reconstruction reuses the run's own code paths

**Decision**: rebuild `DiscoveryConfig` from the run row's `search_space`
JSONB, rebuild the market view via `signals.load_market_data` with the stored
dataset version / universe filter / max-date, re-derive the outer window from
the stored `segregation` boundaries, and recompute each counted candidate's
outer tests by calling the *same functions the run called*
(`edges.tier1_interaction_test`, `edges.causal_labels` +
`edges.tier2_bucket_tests`).

**Rationale**: verification (R3) compares recomputed p-values to stored ones —
that comparison only certifies "the world is unchanged" if the computation is
identical. A parallel reimplementation would conflate code divergence with
data drift. Grounded: the run row pre-registers seed, search_space,
segregation, dataset_version (verified in schema); candidates store
expression + per-test summaries (signal, conditioning/bucket, n, pvalue, coef).

**Alternatives considered**: storing per-observation series in the ledger at
run time (rejected — retroactivity is the point; the two admitted prod runs
predate this spec); replaying from a serialized MarketData snapshot (rejected —
no such snapshot exists, and prices are immutable history anyway).

## R2 — The bootstrap resamples records; statistics are recomputed per resample

**Decision**: the SPA operates on the **joint per-observation record matrix**:
for each counted test unit (candidate × signal, matching the BH family
exactly), reconstruct its per-date record series over the outer window; align
all units on the union of outer dates (missing dates masked per unit). Each
bootstrap iteration draws ONE stationary-bootstrap index path over the dates
and applies it to every unit (joint resampling), then **recomputes each unit's
test statistic** on the resampled records — the same statistic family the
outer test used. The SPA statistic is the studentized max across units;
Hansen's recentering rule supplies the null mean per unit.

**Rationale**: this is Hansen's construction adapted to discovery's units:
resampling the raw records (not the summary statistics) preserves both serial
dependence (via blocks) and cross-candidate dependence (via joint indices),
and recomputing statistics per resample handles tier-1 (HAC interaction
coefficient) and tier-2 (bucket paired tests) uniformly — no per-tier
special-casing in the bootstrap itself.

**Alternatives considered**: bootstrapping summary statistics with an assumed
covariance (rejected — assumes away exactly the cross-candidate dependence the
joint resampling exists to preserve); per-unit independent resampling
(rejected — destroys cross-candidate dependence and understates the max-null).

## R3 — Verification tolerance and drift semantics

**Decision**: before any bootstrap, recompute every counted unit's p-value and
compare to the stored one. Tolerance: absolute 1e-9 OR relative 1e-6 —
floating-point/library noise only. Any unit beyond tolerance ⇒ **refusal**
naming the units and divergence magnitudes; the refusal is recorded as a
diagnostics-style note in the command output (not in the ledger). Verification
outcome (all-match, per-unit max divergence) is part of the recorded result on
success.

**Rationale**: the stored p-values are the fingerprint of the world the run
saw. Reproducing them proves the reconstruction is byte-faithful; anything
else means price backfills or environment drift, and a verdict computed on a
drifted world would be about a different search than the one being judged.

**Alternatives considered**: loose tolerance with a warning (rejected —
"slightly different world" is not a category with a defensible cutoff; refuse
and let the operator investigate); skipping verification with a flag (rejected
for v1 — an escape hatch would get used).

## R4 — Politis–White automatic expected block length

**Decision**: expected block length chosen by the Politis–White (2004, with
Patton's 2009 correction) automatic rule computed on the **cross-unit mean
record series** (one length for the joint resampling), floored at 1 and capped
at n/3. Recorded with the result; overridable via an expert flag.

**Rationale**: one joint index path needs one block length; the cross-unit
mean series is the standard practical choice for a family-level parameter.
The cap guards degenerate short windows (which below ~20 observations refuse
outright per FR-1006 — floor set at 20).

**Alternatives considered**: per-unit lengths (incompatible with joint
resampling); fixed length √n (simpler but strictly dominated by the automatic
rule and harder to justify in review).

## R5 — Hansen SPA specifics

**Decision**: studentized statistics with HAC-consistent scale per unit
(kernel consistent with the block length); Hansen's recentering
μ̂_k = d̄_k · 1{ d̄_k ≥ −√(ω̂_k²/n · 2 log log n) } (the "consistent" null);
report p_consistent as the verdict and p_lower (all-zero null, RC-like) /
p_upper (all-centered) as bracketing diagnostics. B defaults to 1000; RNG is
numpy's PCG64 seeded from `--seed` (default: the run's own stored seed).

**Rationale**: exactly Hansen (2005); the lower/upper bracket makes the
consistent p-value's position interpretable and costs nothing extra (same
bootstrap draws).

## R6 — `spa_reverdicts` table (DDL proposed)

**Decision**: a small append-only table keyed to the run
(`run_id → regime_discovery_runs ON DELETE CASCADE`, matching the ledger's
own cascade design): p-values, level, iterations, seed, block length, family
size, verification summary JSONB, created_at. No UNIQUE on run_id — re-runs
append; "latest" is by created_at. Full DDL in contracts/sql.md, **owner
approval pending**.

**Rationale**: append-only history is a spec requirement (FR-1007); a JSONB
column on the run row would invite in-place overwrites and bloat the
pre-registration row with derived analysis. Cascade with the run is correct:
the re-verdict is derived analysis of the run, not independent audit (unlike
the candidate ledger, which must survive the artifacts it audits — different
role, different rule, consistent with the deletion-story checklist).

## R7 — The budget gate

**Decision**: name the v1 caps as constants (`V1_MAX_BUDGET = 200`,
`V1_MAX_DEPTH = 2` — today's de-facto ceiling made explicit). `discover start`
config validation: if `budget > V1_MAX_BUDGET or depth > V1_MAX_DEPTH`,
require that the most recent N=2 completed runs on the same dataset version
each carry a latest SPA re-verdict that passes at the run's FDR level;
otherwise refuse naming the gate and the `regime discover spa` command. On
satisfaction, record `{gate: "spa", runs: [...], reverdict_ids: [...]}` inside
the new run's `search_space` pre-registration (no schema change — JSONB).

**Rationale**: FR-1009/1010 verbatim; recording satisfaction in the
pre-registration makes the gate auditable exactly where the run's other
declarations live.

**Alternatives considered**: a global config flag to disable the gate
(rejected — the gate exists to bind); gating on ANY historical passing SPA
(rejected — stale passes on old configs shouldn't license new scale).

## R8 — Negative control design

**Decision**: a seeded CI test builds M=40 synthetic noise families (the 006
`discovery_synth` generator), runs the full SPA (B=200 for CI speed) on each,
and asserts the rejection count at α=0.05 is within the exact binomial 99%
bound for size ≤ α; one planted-edge family must reject. Runtime target <60s.

**Rationale**: mirrors 006's noise-run discipline; the binomial bound makes
the assertion seed-stable rather than flaky. B=200 in CI is enough for size
calibration at α=0.05 (p-value granularity 0.005); the shipped default stays
B=1000.

## R2a — Amendment (implementation): unit series for the mean-form core

**Decision**: the statistical core implements the canonical **mean-form** SPA
(studentized means of per-observation series), so each unit's series is defined
to make its mean the test's alternative:

- **tier-2 / bucket units**: the within-bucket per-date differential records
  (`experimental_score − baseline_score` on dates the candidate's causal label
  equals the bucket) — exactly the paired substrate the outer test averaged.
- **tier-1 / interaction units**: the demeaned interaction moment
  `z_t = (sig_t − s̄)(cond_t − c̄) · fwd_t` over the outer window, sign-aligned
  with the stored coefficient (the unit's alternative is "the effect persists
  in its discovered direction", mirroring how discovery follows a signal's
  causal direction). E[z] > 0 corresponds to the discovered-direction
  interaction moment.

**Rationale**: R2's "recompute the statistic per resample" is preserved in
spirit — the studentized mean of these series is the moment form of each
tier's statistic — while keeping the proven, exactly-tested mean-form core
(SPA over moment conditions is standard practice). Verification (R3) is
unaffected: it recomputes the ORIGINAL tests via the run's own code paths.

**Alternatives considered**: a per-unit statistic callback recomputing the full
HAC regression per bootstrap resample (deferred — heavier machinery for v1
with no change to the null logic; revisit if interaction-tier families
dominate).

**v1 scope note**: expressive-tier candidates (freeform/detector) are refused
with a named limitation — their fitted detector state makes byte-faithful
reconstruction a separate increment.
