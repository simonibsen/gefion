---
description: Project developer/PM assistant — check status, suggest next work, or run a full dev loop
---

## Arguments

$ARGUMENTS

## Instructions

Parse the arguments above. Supported modes:

| Command | Meaning |
|---------|---------|
| *(empty)* or `status` | Show project health and suggest prioritized next steps |
| `next` | Recommend the single best thing to work on with rationale |
| `run` | Pick a task and execute a full autonomous dev loop |

---

### Gather Context (all modes)

Before doing anything, read these project state files to understand where things stand:

1. **`.specify/memory/progress.md`** — current capabilities, test counts, data coverage
2. **`.specify/memory/backlog.md`** — open work items with priorities
3. **`.specify/memory/constitution.md`** — core principles and constraints (TDD, DB-first, CLI-first, observability, simplicity)
4. **`.specify/memory/notes.md`** — dev tips and session context

Also gather live system state:

5. **Test suite** — run `OTEL_ENABLED=false .venv/bin/python -m pytest --tb=no -q` to get pass/fail/skip counts
6. **Git status** — check branch, uncommitted changes, distance from main
7. **System health** — use the `system_status` MCP tool (or `.venv/bin/g2 system-status --json`) to check infrastructure, data freshness, and missing features

---

### Mode: `status` (default)

Present a concise project health report:

**Health Dashboard**:
- Tests: X passed, Y failed, Z skipped
- Infrastructure: PostgreSQL, Tempo, Docker status
- Data freshness: latest price date, symbols count
- Git: branch, uncommitted changes, commits ahead of main

**Top 3 Suggested Next Steps** (prioritized):
For each suggestion, include:
- What to do (1 sentence)
- Why it matters (impact/urgency)
- Estimated scope (small/medium/large)
- Which backlog item it addresses (if any)

Prioritization criteria:
1. Broken things first (failing tests, missing infrastructure)
2. High-priority backlog items
3. Technical debt that blocks future work
4. New features

---

### Mode: `next`

Recommend the single most impactful thing to work on right now.

Include:
- **What**: Clear description of the task
- **Why**: Business/technical justification
- **How**: High-level approach (2-3 bullet points)
- **Scope**: Files likely affected, estimated complexity
- **Spec needed?**: Whether this warrants a spec (use `/g2-dev run` to execute, or suggest `specify create` for larger items)

If the task is large enough to warrant a spec, suggest creating one via the spec-kit workflow:
```
specify create --name <spec-name>
```

---

### Mode: `run`

Execute a full autonomous development loop for the highest-priority actionable task.

**Step 1: Select task**
- Pick from backlog (highest priority, smallest scope first)
- Or address a failing test / system health issue

**Step 2: Plan** (enter plan mode for non-trivial tasks)
- Follow the constitution's plan structure: tests listed before implementation
- Reference the spec if one exists (`.specify/specs/<name>.md`)

**Step 3: Implement** (TDD — mandatory per constitution)
1. Write tests FIRST in `tests/`
2. Run pytest — verify FAIL
3. Implement minimum code in `src/`
4. Run pytest — verify PASS

**Step 4: Verify**
- Run full test suite
- If performance-sensitive: check traces via `g2 span-check` (Tempo must be running)
- Verify no regressions

**Step 5: Update project state**
- Update `.specify/memory/progress.md` if capabilities changed
- Update `.specify/memory/backlog.md` (remove completed items, add follow-up work)
- Update docs if major feature (per constitution documentation requirements)

**Step 6: Report**
- Summarize what was done
- Show test results
- List files changed
- Suggest commit message (do NOT commit automatically — wait for user)

---

### Principles (always follow)

- **TDD is non-negotiable** — never write `src/` before `tests/`
- **Constitution is law** — all decisions must comply with `.specify/memory/constitution.md`
- **Schema changes need approval** — propose DDL, don't execute
- **Observe your work** — check traces after performance-sensitive changes
- **Keep it simple** — YAGNI, minimum necessary complexity
- **Spec-kit aware** — for larger features, create specs via `specify create`; reference existing specs in `.specify/specs/`
- **Skill-aware** — if a task would benefit from a reusable slash command, suggest creating a new `g2-` prefixed skill in `.claude/commands/`
