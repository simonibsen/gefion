-- Migration: Add stocks_fundamentals hypertable + exchange/asset_type on stocks
--
-- stocks_fundamentals: time-series table for company fundamental data
-- (market_cap, PE, beta, etc.) — used as source for computed features.
--
-- stocks: add exchange and asset_type columns from LISTING_STATUS data.

-- 1. Add exchange and asset_type columns to stocks
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS asset_type TEXT;

CREATE INDEX IF NOT EXISTS stocks_exchange_idx ON stocks(exchange) WHERE exchange IS NOT NULL;

-- 2. Create stocks_fundamentals hypertable
CREATE TABLE IF NOT EXISTS stocks_fundamentals (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    date DATE NOT NULL,
    market_cap BIGINT,
    pe_ratio NUMERIC(10,2),
    forward_pe NUMERIC(10,2),
    peg_ratio NUMERIC(10,4),
    book_value NUMERIC(12,4),
    dividend_yield NUMERIC(8,6),
    eps NUMERIC(10,4),
    revenue_per_share NUMERIC(10,4),
    profit_margin NUMERIC(8,6),
    operating_margin NUMERIC(8,6),
    return_on_equity NUMERIC(8,6),
    beta NUMERIC(8,4),
    ev_to_ebitda NUMERIC(10,2),
    shares_outstanding BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (data_id, date)
);

SELECT create_hypertable('stocks_fundamentals', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('stocks_fundamentals', INTERVAL '90 days');

CREATE INDEX IF NOT EXISTS stocks_fundamentals_data_date_idx
    ON stocks_fundamentals(data_id, date DESC);
