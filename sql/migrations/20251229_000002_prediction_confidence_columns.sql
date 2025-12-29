-- Migration: Add confidence columns to prediction tables
-- Date: 2024-12-29
-- Description: Extend existing prediction tables with confidence metrics

-- Add confidence columns to quantile_predictions
ALTER TABLE quantile_predictions ADD COLUMN IF NOT EXISTS
    iqr_width NUMERIC(10,6);              -- q90 - q10 (narrower = more confident)

ALTER TABLE quantile_predictions ADD COLUMN IF NOT EXISTS
    quantile_confidence NUMERIC(5,4);     -- Computed confidence score (0-1)

-- Add confidence columns to trend_class_predictions
ALTER TABLE trend_class_predictions ADD COLUMN IF NOT EXISTS
    entropy NUMERIC(6,4);                 -- Shannon entropy of class probabilities

ALTER TABLE trend_class_predictions ADD COLUMN IF NOT EXISTS
    margin NUMERIC(5,4);                  -- Difference between top-2 class probs

\echo 'Added confidence columns to prediction tables'
