# CLI Contract: Experiment Commands

## New Commands

### Discovery

```
gefion experiment discover [--json]
```
Inventories available data sources, features, functions, and cross-references against principles catalog. Returns gaps and hypotheses.

### Cycle Management

```
gefion experiment cycle start [--name NAME] [--fdr-rate 0.10] [--holdout-weeks 6] [--max-experiments 20] [--budget-seconds 7200] [--json]
gefion experiment cycle status <cycle_id> [--json]
gefion experiment cycle evaluate <cycle_id> [--json]
gefion experiment cycle list [--status STATUS] [--limit N] [--json]
```

### Extended Experiment Commands (additions to existing)

```
gefion experiment propose <name> --type feature_engineering|feature_selection|hyperparameter|model_comparison|pipeline --principle <principle_id> --cycle <cycle_id> [--search-space JSON] [--null-hypothesis TEXT] [--json]
gefion experiment show-config <experiment_id> [--json]
gefion experiment rerun <experiment_id> [--start-date DATE] [--end-date DATE] [--json]
```

### Principle Commands

```
gefion principles list [--domain DOMAIN] [--experiment-type TYPE] [--status STATUS] [--json]
gefion principles show <principle_id> [--json]
gefion principles suggest [--experiment-type TYPE] [--json]
```

## JSON Output Format

All `--json` commands output:
```json
{
  "_meta": {"timestamp": "...", "command": "...", "params": {}},
  "status": "ok|error",
  "message": "...",
  ...command-specific fields...
}
```

### Discovery Output
```json
{
  "data_sources": [
    {"table": "stock_ohlcv", "columns": [...], "row_count": 12000000, "date_range": ["2025-03-01", "2026-03-27"], "coverage_pct": 100, "freshness_days": 2}
  ],
  "features": [
    {"name": "rsi_14", "function_name": "rsi", "active": true, "coverage_pct": 95}
  ],
  "gaps": [
    {"principle_id": "ldp-fractional-diff", "required_data": "stock_ohlcv.close", "available": true, "missing": null, "hypothesis": "Fractionally differentiated close price as feature"}
  ],
  "hypotheses": [
    {"principle_id": "ldp-fractional-diff", "description": "Test frac-diff features", "experiment_type": "feature_engineering", "feasibility": "ready"}
  ]
}
```

### Cycle Status Output
```json
{
  "cycle_id": 1,
  "name": "cycle-2026-03-29",
  "status": "running",
  "holdout": {"start": "2026-02-15", "end": "2026-03-27"},
  "fdr_rate": 0.10,
  "experiments": {
    "total": 8,
    "completed": 5,
    "running": 2,
    "pending": 1
  },
  "resource_usage": {
    "elapsed_seconds": 1200,
    "budget_seconds": 7200,
    "disk_free_gb": 15.2,
    "memory_used_pct": 45
  }
}
```

### Cycle Evaluate Output
```json
{
  "cycle_id": 1,
  "fdr_rate": 0.10,
  "experiments_evaluated": 8,
  "promoted": 3,
  "rejected": 5,
  "results": [
    {"experiment_id": 12, "name": "frac-diff-close", "p_value": 0.003, "fdr_survived": true, "promoted": true},
    {"experiment_id": 13, "name": "rsi-selection", "p_value": 0.42, "fdr_survived": false, "promoted": false}
  ]
}
```
