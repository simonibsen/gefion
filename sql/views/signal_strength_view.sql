-- Signal Strength View
-- Computes signal strength dynamically from quantile and classifier predictions.
-- Weights are configurable via the params CTE - change these to experiment.
--
-- Usage: SELECT * FROM signal_strength_view WHERE prediction_date = '2024-12-01';

CREATE OR REPLACE VIEW signal_strength_view AS
WITH params AS (
    -- Configurable weights (change these to experiment)
    SELECT
        0.4::numeric AS quantile_weight,
        0.4::numeric AS classifier_weight,
        0.2::numeric AS persistence_weight
),
quantile_signals AS (
    SELECT
        qp.data_id,
        qp.prediction_date,
        qp.horizon_days,
        qp.q10,
        qp.q50,
        qp.q90,
        qp.q90 - qp.q10 AS iqr_width,
        vt.strong_threshold,
        vt.weak_threshold,
        vt.historical_volatility,
        -- Normalized magnitude: q50 / threshold, clipped to [-1, 1]
        GREATEST(-1, LEAST(1,
            qp.q50 / NULLIF(vt.strong_threshold, 0)
        )) AS quantile_component,
        -- Quantile confidence: narrower IQR relative to volatility = higher confidence
        CASE
            WHEN vt.historical_volatility > 0 THEN
                GREATEST(0, LEAST(1,
                    1 - ((qp.q90 - qp.q10) / (vt.historical_volatility * 2))
                ))
            ELSE 0.5
        END AS quantile_confidence
    FROM quantile_predictions qp
    LEFT JOIN LATERAL (
        SELECT strong_threshold, weak_threshold, historical_volatility
        FROM volatility_thresholds
        WHERE data_id = qp.data_id
          AND horizon_days = qp.horizon_days
          AND calculation_date <= qp.prediction_date
        ORDER BY calculation_date DESC
        LIMIT 1
    ) vt ON TRUE
),
classifier_signals AS (
    SELECT
        tcp.data_id,
        tcp.prediction_date,
        tcp.horizon_days,
        tcp.predicted_class,
        tcp.p_strong_down,
        tcp.p_weak_down,
        tcp.p_neutral,
        tcp.p_weak_up,
        tcp.p_strong_up,
        -- Probability-weighted class score [-1 to +1]
        (COALESCE(tcp.p_strong_up, 0) * 1.0 +
         COALESCE(tcp.p_weak_up, 0) * 0.5 +
         COALESCE(tcp.p_neutral, 0) * 0.0 +
         COALESCE(tcp.p_weak_down, 0) * -0.5 +
         COALESCE(tcp.p_strong_down, 0) * -1.0) AS classifier_component,
        -- Margin between top-2 classes
        GREATEST(
            COALESCE(tcp.p_strong_up, 0),
            COALESCE(tcp.p_weak_up, 0),
            COALESCE(tcp.p_neutral, 0),
            COALESCE(tcp.p_weak_down, 0),
            COALESCE(tcp.p_strong_down, 0)
        ) - (
            -- Second highest probability (approximate using sorted logic)
            CASE
                WHEN GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral, tcp.p_weak_down, tcp.p_strong_down) = tcp.p_strong_up
                THEN GREATEST(tcp.p_weak_up, tcp.p_neutral, tcp.p_weak_down, tcp.p_strong_down)
                WHEN GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral, tcp.p_weak_down, tcp.p_strong_down) = tcp.p_weak_up
                THEN GREATEST(tcp.p_strong_up, tcp.p_neutral, tcp.p_weak_down, tcp.p_strong_down)
                WHEN GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral, tcp.p_weak_down, tcp.p_strong_down) = tcp.p_neutral
                THEN GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_weak_down, tcp.p_strong_down)
                WHEN GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral, tcp.p_weak_down, tcp.p_strong_down) = tcp.p_weak_down
                THEN GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral, tcp.p_strong_down)
                ELSE GREATEST(tcp.p_strong_up, tcp.p_weak_up, tcp.p_neutral, tcp.p_weak_down)
            END
        ) AS margin,
        -- Confidence from existing column or computed
        COALESCE(tcp.margin, 0.5) AS classifier_confidence
    FROM trend_class_predictions tcp
)
SELECT
    COALESCE(qs.data_id, cs.data_id) AS data_id,
    s.symbol,
    COALESCE(qs.prediction_date, cs.prediction_date) AS prediction_date,
    COALESCE(qs.horizon_days, cs.horizon_days) AS horizon_days,

    -- Components (for analysis)
    qs.quantile_component,
    cs.classifier_component,
    qs.q50,
    qs.q10,
    qs.q90,
    cs.predicted_class,

    -- Signal score (weighted combination)
    GREATEST(-1, LEAST(1,
        COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
        COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
    )) AS signal_score,

    -- Signal direction (discretized)
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

    -- Confidence metrics
    qs.quantile_confidence,
    cs.classifier_confidence,
    cs.margin,
    (COALESCE(qs.quantile_confidence, 0.5) + COALESCE(cs.classifier_confidence, 0.5)) / 2 AS avg_confidence,

    -- Raw data for flexibility
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

-- Index hint comment for performance
COMMENT ON VIEW signal_strength_view IS
    'Dynamic signal strength computation. Query with prediction_date filter for best performance.';
