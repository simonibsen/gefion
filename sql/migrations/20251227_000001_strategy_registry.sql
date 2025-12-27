-- Migration: Add strategy registry tables
-- Date: 2025-12-27
-- Description: Add strategy_registry and strategy_configs tables for DB-driven strategy management

-- =============================================================================
-- STRATEGY REGISTRY
-- =============================================================================

-- Strategy registry - maps strategy names to Python implementations
-- Stores metadata about available strategies (module path, class name, defaults)
CREATE TABLE IF NOT EXISTS strategy_registry (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    class_name TEXT NOT NULL,
    default_params JSONB DEFAULT '{}',
    param_schema JSONB,
    description TEXT,
    tags TEXT[],
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Strategy configurations - parameterized instances of strategies
-- Each config references a strategy and can override default params
CREATE TABLE IF NOT EXISTS strategy_configs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL REFERENCES strategy_registry(name) ON DELETE CASCADE,
    params JSONB NOT NULL DEFAULT '{}',
    description TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for listing enabled strategies
CREATE INDEX IF NOT EXISTS idx_strategy_registry_enabled
    ON strategy_registry(enabled, name)
    WHERE enabled = TRUE;

-- Index for listing active configs
CREATE INDEX IF NOT EXISTS idx_strategy_configs_active
    ON strategy_configs(active, strategy_name)
    WHERE active = TRUE;
