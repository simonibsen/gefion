-- #120 trace review, 2026-07-14: third function scope 'materialized' for
-- registry rows whose values are written by their own pipeline (macro
-- ingest/derive, ml predict) and must never be dispatched. The per-stock
-- sweep previously attempted every active definition regardless of scope:
-- all market functions plus the ingest-side markers failed once per symbol
-- per pass (~124k futile attempts nightly, each fetching source data first).
ALTER TABLE feature_functions
    DROP CONSTRAINT IF EXISTS feature_functions_scope_check;
ALTER TABLE feature_functions
    ADD CONSTRAINT feature_functions_scope_check
    CHECK (scope IN ('stock', 'market', 'materialized'));

-- Retag the ingest-side marker rows that were registered scope='stock'
-- (their bodies say "not dispatched", but the sweep dispatched them anyway).
UPDATE feature_functions
SET scope = 'materialized'
WHERE name IN ('macro_value', 'macro_derived')
  AND function_body LIKE '#%not dispatched%';

-- Register missing markers. pred_* feature_definitions reference
-- function_name 'model_prediction' (spec 012) but no registry row existed,
-- so the sweep attempted them and errored per symbol; older flows could
-- likewise leave macro_* definitions without their macro_value marker.
INSERT INTO feature_functions
    (name, version, status, enabled, description, language, function_body,
     scope)
VALUES
    ('model_prediction', 'v1', 'active', TRUE,
     'vintage-model prediction quantiles (spec 012)', 'python',
     '# materialized by gefion.ml — not dispatched', 'materialized'),
    ('macro_value', 'v1', 'active', TRUE,
     'Ingested macro series values — see gefion.macro.ingest', 'python',
     '# materialized by gefion.macro — not dispatched', 'materialized')
ON CONFLICT DO NOTHING;
