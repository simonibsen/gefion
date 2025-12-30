-- AI Experimentation Framework Tables
-- Migration: 2024-12-29

-- Experiments table - tracks experiment definitions and status
CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    experiment_type VARCHAR(50) NOT NULL,  -- strategy_params, feature_selection, etc.

    -- Configuration (JSONB for flexibility)
    config JSONB NOT NULL,  -- Type-specific config
    search_space JSONB,     -- Parameters to explore

    -- Objective & Goal (optional)
    objective_metric VARCHAR(50) DEFAULT 'sharpe_ratio',  -- What to optimize
    objective_direction VARCHAR(10) DEFAULT 'maximize',   -- maximize or minimize
    goal_target NUMERIC(12,6),           -- Optional: target value (e.g., 2.0 for Sharpe > 2.0)
    goal_type VARCHAR(20),               -- 'achieve' (absolute), 'improve' (relative), 'minimize'
    baseline_value NUMERIC(12,6),        -- For 'improve': current performance to beat
    early_stop_on_goal BOOLEAN DEFAULT FALSE,  -- Stop when goal achieved?

    -- Execution
    status VARCHAR(20) DEFAULT 'proposed',  -- proposed, approved, running, completed, failed, rejected
    priority INTEGER DEFAULT 0,

    -- Chaining
    parent_experiment_id INTEGER REFERENCES experiments(id),
    depends_on_output VARCHAR(100),  -- Which output from parent to use

    -- Results
    results JSONB,           -- Best params, metrics, etc.
    artifacts_path VARCHAR(500),  -- Path to saved models/files
    goal_achieved BOOLEAN,   -- Did we meet the goal?

    -- Metadata
    proposed_by VARCHAR(50) DEFAULT 'ai',  -- ai or user
    approved_by VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- Tracking
    total_trials INTEGER,
    completed_trials INTEGER DEFAULT 0,
    best_score NUMERIC(12,6),

    CONSTRAINT valid_status CHECK (status IN ('proposed', 'approved', 'running', 'completed', 'failed', 'rejected')),
    CONSTRAINT valid_goal_type CHECK (goal_type IS NULL OR goal_type IN ('achieve', 'improve', 'minimize')),
    CONSTRAINT valid_direction CHECK (objective_direction IN ('maximize', 'minimize'))
);

CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_type ON experiments(experiment_type);
CREATE INDEX IF NOT EXISTS idx_experiments_parent ON experiments(parent_experiment_id);

-- Experiment trials table - individual trial results
CREATE TABLE IF NOT EXISTS experiment_trials (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    trial_number INTEGER NOT NULL,

    -- Parameters tested
    params JSONB NOT NULL,

    -- Results
    metrics JSONB NOT NULL,  -- sharpe_ratio, total_return, max_drawdown, etc.
    score NUMERIC(12,6),     -- Primary optimization metric

    -- Metadata
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_seconds NUMERIC(10,2),

    UNIQUE(experiment_id, trial_number)
);

CREATE INDEX IF NOT EXISTS idx_trials_experiment ON experiment_trials(experiment_id);
CREATE INDEX IF NOT EXISTS idx_trials_score ON experiment_trials(score DESC);
