-- Incremental sync (/sync/delta) range scans
CREATE INDEX idx_sets_updated_at        ON sets(updated_at);
CREATE INDEX idx_cards_updated_at       ON cards(updated_at);
CREATE INDEX idx_card_prices_updated_at ON card_prices(updated_at);

-- card_prices lookups used by /cards, /cards/search, /card/{id}, and the upsert in /sync/update
CREATE UNIQUE INDEX idx_card_prices_card_type ON card_prices(card_id, price_type);

-- /cards/search exact-match filters
CREATE INDEX idx_cards_set_id ON cards(set_id);
CREATE INDEX idx_cards_dex_id ON cards(dex_id);
CREATE INDEX idx_cards_phash  ON cards(phash);
