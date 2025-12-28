-- Migration 005: Add called_by column to feature_functions
--
-- Description: Adds the called_by column to support hierarchical plugin architecture.
-- This column allows meta-functions to discover and call their plugin implementations.

-- Add called_by column (nullable to support both meta-functions and plugins)
ALTER TABLE feature_functions
ADD COLUMN IF NOT EXISTS called_by TEXT;

-- Create index for efficient plugin discovery
-- Optimizes: WHERE called_by = 'meta_function' AND enabled = TRUE AND status = 'active'
CREATE INDEX IF NOT EXISTS idx_feature_functions_called_by_enabled_status
    ON feature_functions (called_by, enabled, status)
    WHERE called_by IS NOT NULL;
