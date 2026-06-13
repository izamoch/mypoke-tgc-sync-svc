-- ==========================================
-- D1 FREE-TIER OPTIMIZATION INDEXES
-- Minimizes read/write row operations to stay within Cloudflare D1 free tier limits
-- ==========================================

-- 1. Incremental sync range scans (reduces full-table scans to point-lookups)
CREATE INDEX IF NOT EXISTS idx_sets_updated_at        ON sets(updated_at);
CREATE INDEX IF NOT EXISTS idx_cards_updated_at       ON cards(updated_at);
CREATE INDEX IF NOT EXISTS idx_card_prices_updated_at ON card_prices(updated_at);

-- 2. card_prices unique lookups & upserts (crucial for ON CONFLICT DO UPDATE)
-- Note: Already applied in D1 as 'idx_card_prices_card_variant'. Using IF NOT EXISTS.
CREATE UNIQUE INDEX IF NOT EXISTS idx_card_prices_card_variant ON card_prices(card_id, price_type);

-- 3. Redundant Index Clean-up (saves D1 Write Rows during prices upserts)
-- The single-column index on card_id is redundant because the unique index on (card_id, price_type) 
-- has card_id as the leftmost column, allowing SQLite to use it for card_id lookups.
DROP INDEX IF EXISTS idx_card_prices_card_id;

-- 4. Covering Index for CANDIDATE_QUERY (Smart Sync candidate selection)
-- This query performs a heavy LEFT JOIN and MAX(market). By including card_id, price_type, and market 
-- in a single covering index, SQLite satisfies the JOIN, WHERE, and MAX operations without 
-- fetching any rows from the card_prices table, dramatically reducing D1 Read Rows.
CREATE INDEX IF NOT EXISTS idx_card_prices_covering_sync ON card_prices(card_id, price_type, market);

-- 5. Foreign Key & Search Filters
CREATE INDEX IF NOT EXISTS idx_cards_set_id ON cards(set_id);
CREATE INDEX IF NOT EXISTS idx_cards_dex_id ON cards(dex_id);
CREATE INDEX IF NOT EXISTS idx_cards_phash  ON cards(phash);

