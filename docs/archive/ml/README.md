# ML Documentation Index (Source of Truth)

This folder contains Gefion's ML vision, system design, and execution roadmap. To avoid duplicated/contradictory plans, treat the documents as follows:

## Canonical docs

- `docs/archive/ml/HIGHLEVEL.md`: Product vision and end goal (what we are building and why). Keep this narrative-focused.
- `docs/archive/ml/ML_SYSTEM_DESIGN.md`: System design and decisions (schemas, pipelines, storage choices, model shape). **Source of truth for architecture decisions.**
- `docs/archive/ml/ML_ROADMAP.md`: Implementation phases and task checklist (what to do next). Should reference `ML_SYSTEM_DESIGN.md` instead of re-stating competing options.

## Current key decisions (summarized)

- Modeling: start with **one multi-output quantile model** (shared encoder + 7d/30d/90d heads); fall back to separate models only if validation materially underperforms.
- Storage: store predictions in **dedicated prediction tables** (e.g., `quantile_predictions`, `prediction_outcomes`, `model_performance`) rather than encoding them as `computed_features`.
- Config/lineage (MVP): store dataset manifests and run configs in **`ml_datasets`** and **`ml_runs`**, and register artifacts in **`ml_models`**.
