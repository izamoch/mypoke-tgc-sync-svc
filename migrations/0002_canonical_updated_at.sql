-- ==========================================
-- CANONICAL updated_at FORMAT + DELTA-CURSOR INDEX
-- ==========================================
-- Requirement 2 (Flutter delta cursor): updated_at must use a single canonical
-- UTC format `YYYY-MM-DDTHH:MM:SSZ` (trailing `Z`, no microseconds) so that
-- SQLite's lexicographic string comparison matches chronological order.
--
-- Two legacy formats coexist and must be re-normalised in place:
--   - historical seed:  2026-01-17T17:24:40.731191   (microseconds, no `Z`)
--   - client `since`:    2026-06-16T00:00:00Z         (already canonical)
--
-- strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) parses either form (SQLite ignores
-- a trailing `Z` and the fractional seconds when reading) and re-emits the
-- canonical form. Rows already canonical are matched by the guard and left
-- untouched, so this is idempotent and safe to re-run.

-- 1. Backfill: re-normalise any non-canonical updated_at across all tables.
UPDATE card_prices
   SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)
 WHERE updated_at IS NOT NULL
   AND updated_at <> strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

UPDATE cards
   SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)
 WHERE updated_at IS NOT NULL
   AND updated_at <> strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

UPDATE sets
   SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)
 WHERE updated_at IS NOT NULL
   AND updated_at <> strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

-- 2. Delta-cursor index: compound (updated_at, id) so the API's `?since=` range
-- scan is index-only and ties within the same second break deterministically by
-- id. Replaces the single-column idx_card_prices_updated_at from 0001.
DROP INDEX IF EXISTS idx_card_prices_updated_at;
CREATE INDEX IF NOT EXISTS idx_card_prices_updated_at ON card_prices(updated_at, id);
