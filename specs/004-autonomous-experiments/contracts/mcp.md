# MCP Contract: Experiment Tools

## New MCP Tools

### experiment_discover
Inventories data sources and cross-references with principles. Returns gaps and hypotheses.

**Parameters**: none
**Returns**: data_sources, features, gaps, hypotheses (same as CLI --json)

### experiment_cycle_start
Start a new experiment cycle with holdout and FDR configuration.

**Parameters**: name (optional), fdr_rate (default 0.10), holdout_weeks (default 6), max_experiments (default 20), budget_seconds (default 7200)
**Returns**: cycle_id, holdout window dates

### experiment_cycle_status
Get status of a running or completed cycle.

**Parameters**: cycle_id
**Returns**: status, experiment counts, resource usage

### experiment_cycle_evaluate
Apply FDR control and auto-promote survivors.

**Parameters**: cycle_id
**Returns**: promoted/rejected experiments with p-values and FDR status

### principles_list
Query the principles catalog.

**Parameters**: domain (optional), experiment_type (optional), status (optional)
**Returns**: list of principles with id, claim, experiment_types, empirical_status

### principles_suggest
Suggest experiment hypotheses based on principles + current system state.

**Parameters**: experiment_type (optional)
**Returns**: list of hypotheses with principle references and feasibility

## Extended Existing Tools

### experiment_propose (extended)
New optional parameters: `principle_id`, `null_hypothesis`, `cycle_id`, `type` (now supports all experiment types)

### experiment_status (extended)
Now includes: holdout_p_value, fdr_survived, resource_usage, principle reference
