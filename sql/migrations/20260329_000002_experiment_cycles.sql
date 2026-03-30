-- Migration: Autonomous Experimentation Framework
--
-- Adds experiment_cycles table and extends experiments + feature_definitions
-- for the autonomous AI experimentation framework.
--
-- Changes:
--   1. New table: experiment_cycles (groups experiments for FDR evaluation)
--   2. Extend experiments: cycle linkage, principle reference, statistical evaluation
--   3. Extend feature_definitions: experimental/production tracking

-- =============================================================================
-- 1. EXPERIMENT CYCLES TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS experiment_cycles (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    holdout_start_date DATE NOT NULL,
    holdout_end_date DATE NOT NULL,
    fdr_rate NUMERIC DEFAULT 0.10,
    discovery_snapshot JSONB,
    principles_consulted JSONB,
    status TEXT DEFAULT 'proposed',
    compute_budget_seconds INTEGER DEFAULT 7200,
    max_experiments INTEGER DEFAULT 20,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    summary JSONB
);

CREATE INDEX IF NOT EXISTS idx_experiment_cycles_status ON experiment_cycles(status);

-- =============================================================================
-- 2. EXTEND EXPERIMENTS TABLE
-- =============================================================================

-- Link to cycle
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS cycle_id INTEGER REFERENCES experiment_cycles(id);

-- Principle reference (text slug referencing YAML, not a FK)
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS principle_id TEXT;

-- Hypothesis and statistical evaluation
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS null_hypothesis TEXT;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS holdout_p_value NUMERIC;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS fdr_survived BOOLEAN;

-- Discovery context and risk classification
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS discovery_context JSONB;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT 'medium';

-- Resource tracking
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS resource_usage JSONB;

-- Promotion and probation
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS demoted_at TIMESTAMPTZ;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS probation_until TIMESTAMPTZ;

-- Index for cycle queries
CREATE INDEX IF NOT EXISTS idx_experiments_cycle_id ON experiments(cycle_id);

-- =============================================================================
-- 3. EXTEND FEATURE DEFINITIONS TABLE
-- =============================================================================

-- Track experimental vs production features
ALTER TABLE feature_definitions ADD COLUMN IF NOT EXISTS is_experimental BOOLEAN DEFAULT false;
ALTER TABLE feature_definitions ADD COLUMN IF NOT EXISTS source_experiment_id INTEGER REFERENCES experiments(id);
ALTER TABLE feature_definitions ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ;

\echo ''
\echo '============================================='
\echo 'Migration: experiment_cycles Complete'
\echo '============================================='
\echo ''
\echo 'Changes:'
\echo '  - Created experiment_cycles table'
\echo '  - Extended experiments: cycle_id, principle_id, null_hypothesis,'
\echo '    holdout_p_value, fdr_survived, risk_level, resource_usage,'
\echo '    promoted_at, demoted_at, probation_until'
\echo '  - Extended feature_definitions: is_experimental,'
\echo '    source_experiment_id, promoted_at'
\echo ''
