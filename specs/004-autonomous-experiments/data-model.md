# Data Model: Autonomous AI Experimentation Framework

**Date**: 2026-03-29 | **Branch**: 004-autonomous-experiments

## Entities

### Experiment Cycle (NEW)

A batch of experiments proposed, executed, and evaluated together. FDR control is applied per cycle.

| Field | Description |
|-------|-------------|
| id | Unique identifier |
| name | Human-readable cycle name |
| holdout_start_date | Start of holdout window (structurally excluded from training) |
| holdout_end_date | End of holdout window |
| fdr_rate | FDR control rate (default 0.10) |
| discovery_snapshot | Structured data inventory at time of cycle start (JSONB) |
| principles_consulted | List of principle IDs the agent considered (JSONB) |
| status | proposed, running, evaluating, completed, failed |
| compute_budget_seconds | Maximum wall time for this cycle |
| max_experiments | Maximum experiments in this cycle |
| created_at | Timestamp |
| completed_at | Timestamp |
| summary | Cycle results summary (JSONB): total experiments, promoted count, rejected count |

**State transitions**: proposed → running → evaluating → completed / failed

### Experiment (EXTEND existing)

New fields on the existing `experiments` table:

| Field | Description |
|-------|-------------|
| cycle_id | Foreign key to experiment_cycles (nullable for backward compat) |
| principle_id | ID of the motivating principle from catalog (text, references YAML) |
| null_hypothesis | Statement of what "no improvement" looks like |
| holdout_p_value | P-value from holdout evaluation (nullable until evaluated) |
| fdr_survived | Whether experiment survived FDR correction (boolean, nullable) |
| discovery_context | Relevant discovery findings that informed this experiment (JSONB) |
| risk_level | low, medium, high |
| resource_usage | Disk, memory, time consumed (JSONB) |
| promoted_at | Timestamp of promotion to production (nullable) |
| demoted_at | Timestamp of demotion during probation (nullable) |
| probation_until | End of probation period (nullable) |

### Experiment Config (EXTEND existing ExperimentConfig dataclass)

Serializable to/from JSONB in the existing `config` column:

| Field | Description |
|-------|-------------|
| (existing fields) | name, experiment_type, search_space, objective_metric, etc. |
| holdout_config | {holdout_weeks, holdout_start_date, holdout_end_date} |
| data_split | {train_start, train_end, validation_start, validation_end} |
| principle_id | Reference to motivating principle |
| null_hypothesis | Text |
| cv_config | {n_splits, embargo_pct, prediction_horizon} (for purged CV) |
| resource_limits | {max_wall_seconds, max_disk_mb, max_memory_mb} |

### Feature Definitions (EXTEND existing)

New fields on the existing `feature_definitions` table:

| Field | Description |
|-------|-------------|
| is_experimental | Boolean (default false); true for agent-created features |
| source_experiment_id | Foreign key to experiments table (nullable) |
| promoted_at | Timestamp of promotion from experimental to production |

### Principle (YAML, not DB)

Stored in `data/principles/{domain}.yaml`:

| Field | Description |
|-------|-------------|
| id | Unique slug (e.g., ldp-fractional-diff) |
| source | {author, title, year, chapter} |
| claim | One-sentence statement of the principle |
| mechanism | How/why it works |
| experiment_types | List of relevant experiment types |
| testable_prediction | What outcome would confirm this principle |
| experiment_design | Suggested experiment configuration |
| known_limitations | When/where this principle may not apply |
| data_requirements | List of required data sources/columns |
| empirical_status | untested, confirmed, partially_confirmed, contradicted |
| experiments | List of experiment IDs that tested this principle, with outcomes |

### Data Inventory (runtime, not persisted)

Produced by discovery step, stored as JSONB in cycle's `discovery_snapshot`:

| Field | Description |
|-------|-------------|
| data_sources | List of {table, columns, row_count, date_range, coverage_pct, freshness_days} |
| features | List of {name, function_name, active, params, coverage_pct} |
| functions | List of {name, inputs, param_schema, description} |
| gaps | List of {principle_id, required_data, available, missing, hypothesis} |
| hypotheses | List of {principle_id, description, experiment_type, feasibility} |

## Relationships

```
experiment_cycles 1──N experiments
experiments N──1 experiment_cycles (via cycle_id)
experiments 1──N experiment_trials (existing)
experiments N──1 principles (via principle_id, cross-ref to YAML)
experiments 1──N feature_definitions (via source_experiment_id)
experiments N──1 experiments (via parent_experiment_id, existing chaining)
```

## Validation Rules

- An experiment in a cycle MUST have a non-null `null_hypothesis`
- An experiment with `cycle_id` MUST be evaluated via holdout before promotion
- `holdout_p_value` is null until holdout evaluation runs; `fdr_survived` is null until cycle evaluation completes
- `is_experimental` features cannot be used in production models until `promoted_at` is set
- `probation_until` is set at promotion time; if model performance degrades before this date, feature is auto-demoted
- A principle's `empirical_status` is updated only after holdout evaluation (not in-sample results)
- Discovery snapshot is immutable per cycle — captured once at cycle start
