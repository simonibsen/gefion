-- Align pre-#79 databases with canonical schema.sql (017): the ratio
-- columns on stocks_fundamentals must be NUMERIC(14,6) so real provider
-- garbage extremes (Beta -503341.44, DividendYield 1000000.0) store
-- verbatim instead of failing with numeric overflow. schema.sql and the
-- db/schema.py creator already declare 14,6; this migration is the
-- two-file rule's missing half for databases created before that change.
-- Idempotent: re-altering to the same type is a no-op rewrite of a tiny
-- (thousands of rows) hypertable.

ALTER TABLE stocks_fundamentals
    ALTER COLUMN pe_ratio          TYPE NUMERIC(14,6),
    ALTER COLUMN forward_pe        TYPE NUMERIC(14,6),
    ALTER COLUMN peg_ratio         TYPE NUMERIC(14,6),
    ALTER COLUMN book_value        TYPE NUMERIC(14,6),
    ALTER COLUMN dividend_yield    TYPE NUMERIC(14,6),
    ALTER COLUMN eps               TYPE NUMERIC(14,6),
    ALTER COLUMN revenue_per_share TYPE NUMERIC(14,6),
    ALTER COLUMN profit_margin     TYPE NUMERIC(14,6),
    ALTER COLUMN operating_margin  TYPE NUMERIC(14,6),
    ALTER COLUMN return_on_equity  TYPE NUMERIC(14,6),
    ALTER COLUMN beta              TYPE NUMERIC(14,6),
    ALTER COLUMN ev_to_ebitda      TYPE NUMERIC(14,6);
