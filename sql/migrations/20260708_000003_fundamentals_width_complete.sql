-- Migration: finish widening stocks_fundamentals numerics (issue #79)
-- Owner-approved 2026-07-08. The 20260707_000002 fix widened four ratio
-- columns to NUMERIC(14,6); prod (2026-07-08 cron smoke test) hit the same
-- overflow class on the columns it skipped — observed live: Beta -503341.44
-- (MDXH), -165013.73 (ELOX); DividendYield 1000000.0 (CTAA). Store what the
-- provider says; garbage-filtering is a downstream/universe-quality concern.
-- Widening only (precision and scale both grow) — values are preserved.

ALTER TABLE stocks_fundamentals ALTER COLUMN pe_ratio          TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN forward_pe        TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN peg_ratio         TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN book_value        TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN eps               TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN revenue_per_share TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN beta              TYPE NUMERIC(14,6);
ALTER TABLE stocks_fundamentals ALTER COLUMN ev_to_ebitda      TYPE NUMERIC(14,6);
