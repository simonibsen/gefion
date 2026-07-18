-- Migration: system-observations ledger (#144)
-- Approved: owner, 2026-07-18.
--
-- The operating plane's notebook: machine-noticed improvements, anomalies,
-- tuning opportunities, and hypotheses — recorded cheaply at the moment of
-- observation by runtime observers (cycle runner, quality scans, discovery)
-- and by agent sessions operating the system. The ledger holds OBSERVATIONS,
-- never actions: nothing reads it programmatically to change behavior;
-- adoption is always a human act. Supersede-never-erase: terminal review
-- states are immutable; rows are never deleted by any artifact door.
CREATE TABLE IF NOT EXISTS system_observations (
    id BIGSERIAL PRIMARY KEY,
    observer TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('improvement', 'anomaly', 'tuning', 'hypothesis')),
    observation TEXT NOT NULL,
    evidence JSONB,
    suggested_action TEXT,
    review_state TEXT NOT NULL DEFAULT 'open'
        CHECK (review_state IN ('open', 'acknowledged', 'adopted', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
