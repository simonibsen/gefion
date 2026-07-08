-- Migration: widen stocks_fundamentals ratio columns (owner-approved 2026-07-07)
--
-- The first full-universe fundamentals run overflowed NUMERIC(8,6)
-- (|v| < 100): distressed/shell stocks legitimately report margins and
-- return-on-equity in the +/-thousands. Widen to NUMERIC(14,6) — same
-- scale, wider container, no data change.
--
-- Mirrors the canonical DDL in sql/schema.sql (two-file rule).

ALTER TABLE stocks_fundamentals ALTER COLUMN dividend_yield    TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN profit_margin     TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN operating_margin  TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN return_on_equity  TYPE NUMERIC(14,6);
