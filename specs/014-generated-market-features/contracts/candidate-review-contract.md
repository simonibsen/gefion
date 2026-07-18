# Contract: Candidate Review Gate

## Invariant

A row in `market_function_candidates` with `review_state` in
(`pending`, `rejected`) can NEVER produce a stored value: candidates are not
in `feature_functions`, and every execution path enumerates
`feature_functions` only. There is no code path that executes a candidate
body against stored production data. The only sanctioned execution of a
candidate is the sandbox dry-run over seeded synthetic inputs.

## CLI surface (all support `--json`)

```
gefion macro candidate list [--state pending|approved|rejected]
    # the pending queue (default --state pending), newest first

gefion macro candidate show --id N [--rerun-dry-run]
    # the full review packet: body, kind, declared inputs, provenance,
    # dry-run result; --rerun-dry-run re-executes the seeded dry-run

gefion macro candidate approve --id N [--approver NAME]
    # human act; refuses if dry_run.ok is false or state != pending;
    # atomically promotes: feature_functions upsert (scope=market, active)
    # + paired feature_definitions row; records promoted_function_id

gefion macro candidate reject --id N --reason "..." [--approver NAME]
    # human act; reason required; terminal; row retained for audit

gefion macro register-composite --name X --series a,b,c --body-file f.py
    # OWNER-authored composite: direct registration into feature_functions
    # (no gate — the gate is for GENERATED code); validates series exist +
    # enabled, refuses dependency cycles

gefion macro propose --principle P [--kind cross_section|composite]
    # explicit generation door (same generator the cycle runner uses);
    # writes a candidate, prints its id + dry-run summary
```

Refusal messages name the gate: e.g. attempting to derive a candidate-only
name → "…is a pending candidate — review with `gefion macro candidate show`".

## MCP surface

`macro_candidate_list`, `macro_candidate_show`, `macro_candidate_approve`,
`macro_candidate_reject`, `macro_register_composite`, `macro_propose` — thin
wrappers over the CLI (identical semantics, same refusals). approve/reject
are human-directed acts, trust-equivalent to the existing
`experiment_approve` tool that regime discovery's gate already relies on.
No autonomous caller (cycle runner, cron, scheduler) invokes them.

## UI surface

Candidates queue (pending count + list) and read-only review packet
(body with syntax highlighting, inputs, provenance, dry-run). Decisions are
CLI/MCP acts; the UI links to the exact commands.

## Cycle-runner contract

When a cycle's generation targets market scope, the generated body is
written to `market_function_candidates` (state `pending`) with provenance
and a stored dry-run. The cycle does NOT propose an experiment on it, does
NOT approve it, and reports the candidate id in the cycle summary. Failure
to generate (no backend, compile failure) is reported honestly; no empty
candidate rows.
