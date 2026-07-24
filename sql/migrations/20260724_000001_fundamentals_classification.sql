-- Classification history (018): sector/industry accrue WITH the
-- fundamentals vintage rows. stocks.sector/industry are current-state only
-- (overwritten on reclassification), so every sector series, industry
-- census, and shell exclusion was applying today's taxonomy to history.
-- Vintage rows now record the classification as of their fetch date.
-- Mirrors sql/schema.sql.

ALTER TABLE stocks_fundamentals ADD COLUMN IF NOT EXISTS sector TEXT;
ALTER TABLE stocks_fundamentals ADD COLUMN IF NOT EXISTS industry TEXT;

-- Backfill existing vintages from current stocks classification: those rows
-- came from the same 2026-07 OVERVIEW pulls that set today's stocks values,
-- so current classification IS the as-of-then classification. Fills NULLs
-- only — never overwrites a recorded vintage.
UPDATE stocks_fundamentals f
SET sector = s.sector, industry = s.industry
FROM stocks s
WHERE s.id = f.data_id
  AND f.sector IS NULL AND f.industry IS NULL
  AND (s.sector IS NOT NULL OR s.industry IS NOT NULL);
