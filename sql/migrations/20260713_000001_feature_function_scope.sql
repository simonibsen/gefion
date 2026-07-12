-- Spec 011 (epic #114), owner-approved 2026-07-12: market-level functions
-- become first-class registry citizens. scope discriminates per-stock bodies
-- (dispatcher per symbol) from market bodies (per-date cross-section).
ALTER TABLE feature_functions
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'stock'
    CHECK (scope IN ('stock', 'market'));
