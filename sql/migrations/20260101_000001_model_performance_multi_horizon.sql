-- Migration: model_performance_multi_horizon
-- Date: 2026-01-01
-- Description: Change model_performance primary key to support multiple horizons per model
--
-- Previously: PRIMARY KEY (model_id) - only one row per model
-- After: PRIMARY KEY (model_id, horizon_days) - one row per model+horizon combination

-- Drop existing primary key
ALTER TABLE model_performance DROP CONSTRAINT model_performance_pkey;

-- Add new composite primary key
ALTER TABLE model_performance ADD PRIMARY KEY (model_id, horizon_days);

-- Verify the change
DO $$
BEGIN
    RAISE NOTICE 'Migration complete: model_performance now supports multiple horizons per model';
END $$;
