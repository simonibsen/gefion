-- Migration: Unify quantile_predictions and trend_class_predictions into a single
-- predictions table with JSONB values for flexible prediction storage.
--
-- This migration:
-- 1. Creates the new predictions table (hypertable)
-- 2. Migrates data from both old tables
-- 3. Verifies row counts match
-- 4. Recreates signal_strength_view against new table
-- 5. Drops old tables

-- 1. Create unified predictions table
CREATE TABLE IF NOT EXISTS predictions (
    model_id INTEGER NOT NULL REFERENCES ml_models(id),
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    prediction_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,
    prediction_type TEXT NOT NULL,
    prediction_values JSONB NOT NULL,
    metadata JSONB DEFAULT '{}',
    run_id INTEGER REFERENCES ml_runs(id),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (model_id, data_id, prediction_date, horizon_days, prediction_type),
    CONSTRAINT check_horizon_positive CHECK (horizon_days > 0),
    CONSTRAINT check_prediction_type CHECK (prediction_type IN ('quantile', 'trend_class'))
);

SELECT create_hypertable('predictions', 'prediction_date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('predictions', INTERVAL '30 days');

CREATE INDEX IF NOT EXISTS predictions_symbol_date_idx
    ON predictions(data_id, prediction_date, horizon_days);
CREATE INDEX IF NOT EXISTS predictions_type_idx
    ON predictions(prediction_type, prediction_date DESC);
CREATE INDEX IF NOT EXISTS predictions_run_id_idx
    ON predictions(run_id);

-- 2. Migrate quantile predictions
INSERT INTO predictions (model_id, data_id, prediction_date, horizon_days, prediction_type, prediction_values, metadata, run_id, created_at)
SELECT
    model_id, data_id, prediction_date, horizon_days,
    'quantile',
    jsonb_build_object('q10', q10, 'q50', q50, 'q90', q90),
    jsonb_build_object(
        'model_version', COALESCE(model_version, ''),
        'features_snapshot', COALESCE(features_snapshot, '{}'::jsonb)
    ),
    run_id, created_at
FROM quantile_predictions
ON CONFLICT DO NOTHING;

-- 3. Migrate trend class predictions
INSERT INTO predictions (model_id, data_id, prediction_date, horizon_days, prediction_type, prediction_values, metadata, run_id, created_at)
SELECT
    model_id, data_id, prediction_date, horizon_days,
    'trend_class',
    jsonb_build_object(
        'predicted_class', predicted_class,
        'p_strong_up', p_strong_up, 'p_weak_up', p_weak_up,
        'p_neutral', p_neutral, 'p_weak_down', p_weak_down,
        'p_strong_down', p_strong_down,
        'entropy', entropy, 'margin', margin
    ),
    jsonb_build_object(
        'weak_threshold', weak_threshold,
        'strong_threshold', strong_threshold
    ),
    run_id, created_at
FROM trend_class_predictions
ON CONFLICT DO NOTHING;

-- 4. Verify row counts
DO $$
DECLARE
    old_q_count BIGINT;
    old_t_count BIGINT;
    new_q_count BIGINT;
    new_t_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO old_q_count FROM quantile_predictions;
    SELECT COUNT(*) INTO old_t_count FROM trend_class_predictions;
    SELECT COUNT(*) INTO new_q_count FROM predictions WHERE prediction_type = 'quantile';
    SELECT COUNT(*) INTO new_t_count FROM predictions WHERE prediction_type = 'trend_class';
    IF old_q_count != new_q_count OR old_t_count != new_t_count THEN
        RAISE EXCEPTION 'Migration data count mismatch: quantile=% vs %, trend=% vs %',
            old_q_count, new_q_count, old_t_count, new_t_count;
    END IF;
END $$;

-- 5. Recreate signal_strength_view using new table
CREATE OR REPLACE VIEW signal_strength_view AS
WITH params AS (
    SELECT
        0.4::numeric AS quantile_weight,
        0.4::numeric AS classifier_weight,
        0.2::numeric AS persistence_weight
),
quantile_signals AS (
    SELECT
        p.data_id,
        p.prediction_date,
        p.horizon_days,
        (p.prediction_values->>'q10')::NUMERIC(10,4) AS q10,
        (p.prediction_values->>'q50')::NUMERIC(10,4) AS q50,
        (p.prediction_values->>'q90')::NUMERIC(10,4) AS q90,
        (p.prediction_values->>'q90')::NUMERIC(10,4) - (p.prediction_values->>'q10')::NUMERIC(10,4) AS iqr_width,
        vt.strong_threshold,
        vt.weak_threshold,
        vt.historical_volatility,
        GREATEST(-1, LEAST(1,
            (p.prediction_values->>'q50')::NUMERIC / NULLIF(vt.strong_threshold, 0)
        )) AS quantile_component,
        CASE
            WHEN vt.historical_volatility > 0 THEN
                GREATEST(0, LEAST(1,
                    1 - (((p.prediction_values->>'q90')::NUMERIC - (p.prediction_values->>'q10')::NUMERIC) / (vt.historical_volatility * 2))
                ))
            ELSE 0.5
        END AS quantile_confidence
    FROM predictions p
    LEFT JOIN LATERAL (
        SELECT strong_threshold, weak_threshold, historical_volatility
        FROM volatility_thresholds
        WHERE data_id = p.data_id
          AND horizon_days = p.horizon_days
          AND calculation_date <= p.prediction_date
        ORDER BY calculation_date DESC
        LIMIT 1
    ) vt ON TRUE
    WHERE p.prediction_type = 'quantile'
),
classifier_signals AS (
    SELECT
        p.data_id,
        p.prediction_date,
        p.horizon_days,
        p.prediction_values->>'predicted_class' AS predicted_class,
        (p.prediction_values->>'p_strong_down')::NUMERIC(5,4) AS p_strong_down,
        (p.prediction_values->>'p_weak_down')::NUMERIC(5,4) AS p_weak_down,
        (p.prediction_values->>'p_neutral')::NUMERIC(5,4) AS p_neutral,
        (p.prediction_values->>'p_weak_up')::NUMERIC(5,4) AS p_weak_up,
        (p.prediction_values->>'p_strong_up')::NUMERIC(5,4) AS p_strong_up,
        (COALESCE((p.prediction_values->>'p_strong_up')::NUMERIC, 0) * 1.0 +
         COALESCE((p.prediction_values->>'p_weak_up')::NUMERIC, 0) * 0.5 +
         COALESCE((p.prediction_values->>'p_neutral')::NUMERIC, 0) * 0.0 +
         COALESCE((p.prediction_values->>'p_weak_down')::NUMERIC, 0) * -0.5 +
         COALESCE((p.prediction_values->>'p_strong_down')::NUMERIC, 0) * -1.0) AS classifier_component,
        GREATEST(
            COALESCE((p.prediction_values->>'p_strong_up')::NUMERIC, 0),
            COALESCE((p.prediction_values->>'p_weak_up')::NUMERIC, 0),
            COALESCE((p.prediction_values->>'p_neutral')::NUMERIC, 0),
            COALESCE((p.prediction_values->>'p_weak_down')::NUMERIC, 0),
            COALESCE((p.prediction_values->>'p_strong_down')::NUMERIC, 0)
        ) - (
            CASE
                WHEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                ) = (p.prediction_values->>'p_strong_up')::NUMERIC
                THEN GREATEST(
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                )
                WHEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                ) = (p.prediction_values->>'p_weak_up')::NUMERIC
                THEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                )
                WHEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                ) = (p.prediction_values->>'p_neutral')::NUMERIC
                THEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                )
                WHEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                ) = (p.prediction_values->>'p_weak_down')::NUMERIC
                THEN GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_strong_down')::NUMERIC
                )
                ELSE GREATEST(
                    (p.prediction_values->>'p_strong_up')::NUMERIC,
                    (p.prediction_values->>'p_weak_up')::NUMERIC,
                    (p.prediction_values->>'p_neutral')::NUMERIC,
                    (p.prediction_values->>'p_weak_down')::NUMERIC
                )
            END
        ) AS margin,
        COALESCE((p.prediction_values->>'margin')::NUMERIC, 0.5) AS classifier_confidence
    FROM predictions p
    WHERE p.prediction_type = 'trend_class'
)
SELECT
    COALESCE(qs.data_id, cs.data_id) AS data_id,
    s.symbol,
    COALESCE(qs.prediction_date, cs.prediction_date) AS prediction_date,
    COALESCE(qs.horizon_days, cs.horizon_days) AS horizon_days,
    qs.quantile_component,
    cs.classifier_component,
    qs.q50,
    qs.q10,
    qs.q90,
    cs.predicted_class,
    GREATEST(-1, LEAST(1,
        COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
        COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
    )) AS signal_score,
    CASE
        WHEN GREATEST(-1, LEAST(1,
            COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
            COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
        )) > 0.3 THEN 'bullish'
        WHEN GREATEST(-1, LEAST(1,
            COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
            COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
        )) < -0.3 THEN 'bearish'
        ELSE 'neutral'
    END AS signal_direction,
    qs.quantile_confidence,
    cs.classifier_confidence,
    cs.margin,
    (COALESCE(qs.quantile_confidence, 0.5) + COALESCE(cs.classifier_confidence, 0.5)) / 2 AS avg_confidence,
    qs.iqr_width,
    qs.strong_threshold,
    qs.weak_threshold,
    qs.historical_volatility
FROM quantile_signals qs
FULL OUTER JOIN classifier_signals cs
    ON qs.data_id = cs.data_id
    AND qs.prediction_date = cs.prediction_date
    AND qs.horizon_days = cs.horizon_days
LEFT JOIN stocks s ON COALESCE(qs.data_id, cs.data_id) = s.id;

-- 6. Drop old tables
DROP TABLE IF EXISTS quantile_predictions CASCADE;
DROP TABLE IF EXISTS trend_class_predictions CASCADE;
