# Backlog / Roadmap

**This file is no longer the worklist.** As of 2026-07-09, actionable work is
tracked in **GitHub Issues**, and designs live in **`specs/NNN-*/`**. See the
"Where work lives" convention in [docs/DEVELOPMENT.md](../../docs/DEVELOPMENT.md).

Why the move: a checked-in worklist carries per-branch state (an item marked done
on one branch reads open on another) and can't be closed by a PR. Issues have one
global state and close from commits (`Closes #N`). Design still belongs in the
repo — that's what `specs/` is for.

## Open work (GitHub Issues)

Live list: `gh issue list` · https://github.com/simonibsen/gefion/issues

At migration (2026-07-09):

- **First-class deletion** — [#75](https://github.com/simonibsen/gefion/issues/75) (regime delete), [#76](https://github.com/simonibsen/gefion/issues/76) (audit: deletion for every created artifact)
- **Data quality** — [#85](https://github.com/simonibsen/gefion/issues/85) (backfill reconcile of findings that no longer reproduce)
- **Discovery** — [#87](https://github.com/simonibsen/gefion/issues/87) (Reality-Check / SPA bootstrap — *gate before raising discovery budgets*)
- **Regimes** — [#86](https://github.com/simonibsen/gefion/issues/86) (005 remaining surface: per-entity labels, reference-leaf resolution, holdout wiring)
- **UI / tech-debt** — [#88](https://github.com/simonibsen/gefion/issues/88) (unified CLI output component)
- **Features** — [#89](https://github.com/simonibsen/gefion/issues/89) (feat enable/disable, validate/fix)
- **Bug** — [#90](https://github.com/simonibsen/gefion/issues/90) (backup disk-space check)

## In-flight specs

- `specs/009-short-side-execution/` — make shorts a first-class backtest position
  so negative-directionality edges become actable (long-only stays the default).

## Deliberately out of scope

- **Live / paper trading, broker integration.** gefion's job is to decide what's
  worth trading (validated, direction-symmetric signals), not to execute. Execution
  is a different risk class (real-time, money-at-risk, regulatory) and belongs in a
  separate system that consumes gefion's outputs. Recorded so it isn't re-litigated.

## History

Completed items and the full backlog history are in git — see this file's log
(`git log --follow .specify/memory/backlog.md`). Shipped highlights: specs 001,
004, 005, 006 (regime discovery), 007 (entity model), 008 (data quality); VIX
ingestion (→007); universe-quality filter (→008); Postgres named-volume migration;
unified predictions table; cascading data cull.
