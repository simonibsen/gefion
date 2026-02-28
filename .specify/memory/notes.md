# Development Notes

**Last Updated**: 2026-02-28

## Model Evaluation Gotcha

When evaluating predictions with `g2 ml eval`, the evaluation period must allow enough time for the horizon to complete. For example, 7-day predictions from 2026-01-30 can't be evaluated until price data exists for 2026-02-06.

**Workaround**: Generate backfill predictions for historical dates:
```bash
g2 ml predict --model-name quantile --model-version 20260202 \
  --prediction-date 2025-10-01 --exchange NASDAQ --limit 50
g2 ml eval --model-name quantile --model-version 20260202 \
  --start-date 2025-10-01 --end-date 2025-10-15
```

## Spec-Kit Setup

Initialized 2026-02-28 with `specify init --here --ai claude --force --no-git`.
Constitution ratified at v1.3.0 with 6 core principles.
Slash commands available: `/speckit.constitution`, `/speckit.specify`, `/speckit.plan`, `/speckit.tasks`, `/speckit.implement`.
