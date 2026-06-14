"""
Local SQLite index used to avoid reading `cards`/`card_prices` from D1 just to
decide what changed. D1 bills/limits on rows read, so this index lets the
Smart Sync candidate selection and price-delta detection run entirely locally.

The index is seeded once from D1 (preserving the existing `last_checked_at`
"temperature" history) and then maintained locally: every successful D1 write
updates the corresponding row here.
"""

import hashlib
import os
import sqlite3

from . import d1_client

DEFAULT_DB_PATH = "data/local_index.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_state (
    card_id TEXT PRIMARY KEY,
    last_checked_at TEXT,
    needs_backfill INTEGER NOT NULL DEFAULT 0,
    max_market REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS price_checksum (
    card_id TEXT NOT NULL,
    price_type TEXT NOT NULL,
    checksum TEXT NOT NULL,
    PRIMARY KEY (card_id, price_type)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Fields compared by update_card_price() - the checksum must cover exactly these
# so a local-only comparison matches the D1-backed comparison it replaces.
PRICE_CHECKSUM_FIELDS = ["market", "low", "mid", "high", "direct", "avg", "trend", "trend_1d", "trend_7d", "trend_30d"]

# Seed query mirrors the old CANDIDATE_QUERY plus per-variant prices for checksums.
# "current" variants mirror CANDIDATE_QUERY's exclusion of holo/reverse/firstEdition
# when computing max_market for the PREMIUM/STANDARD/NO_PRICE tiering.
_SEED_CARDS_QUERY = """
SELECT c.id, c.updated_at,
       (c.rarity IS NULL AND c.category IS NULL) AS needs_backfill,
       COALESCE(MAX(cp.market), 0.0) AS max_market
FROM cards c
LEFT JOIN card_prices cp
  ON cp.card_id = c.id AND cp.price_type NOT IN ('holo', 'reverse', 'firstEdition')
GROUP BY c.id, c.updated_at
"""

_SEED_PRICES_QUERY = f"SELECT card_id, price_type, {', '.join(PRICE_CHECKSUM_FIELDS)} FROM card_prices"

# price_types excluded from the max_market "current variant" tiering, mirroring
# the old CANDIDATE_QUERY's JOIN condition.
NON_CURRENT_PRICE_TYPES = {"holo", "reverse", "firstEdition"}


def _connect(db_path: str | None) -> sqlite3.Connection:
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def price_checksum(vals: dict) -> str:
    """Computes a checksum over the price fields compared by update_card_price()."""
    parts = [f"{float(vals.get(f) or 0.0):.4f}" for f in PRICE_CHECKSUM_FIELDS]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def is_seeded(db_path: str | None = None) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
        return row is not None and row[0] == "1"
    finally:
        conn.close()


async def seed_from_d1(db_path: str | None = None) -> None:
    """One-time bootstrap: pulls card_state and price checksums from D1.

    Safe to call repeatedly; no-ops if already seeded.
    """
    if is_seeded(db_path):
        return

    cards_rows = await d1_client.d1_query(_SEED_CARDS_QUERY)
    prices_rows = await d1_client.d1_query(_SEED_PRICES_QUERY)

    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO card_state (card_id, last_checked_at, needs_backfill, max_market) "
            "VALUES (?, ?, ?, ?)",
            [
                (row["id"], row["updated_at"], int(row["needs_backfill"]), float(row["max_market"] or 0.0))
                for row in cards_rows
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO price_checksum (card_id, price_type, checksum) VALUES (?, ?, ?)",
            [(row["card_id"], row["price_type"], price_checksum(row)) for row in prices_rows],
        )
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded', '1')")
        conn.commit()
    finally:
        conn.close()


def register_new_cards(card_ids: list[str], db_path: str | None = None) -> None:
    """Registers newly-inserted cards as NEW (unchecked, needing backfill)."""
    if not card_ids:
        return

    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO card_state (card_id, last_checked_at, needs_backfill, max_market) "
            "VALUES (?, NULL, 1, 0.0)",
            [(cid,) for cid in card_ids],
        )
        conn.commit()
    finally:
        conn.close()


def get_candidates(db_path: str | None = None) -> list[dict]:
    """Returns one row per known card: {id, updated_at, needs_backfill, max_market}."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT card_id, last_checked_at, needs_backfill, max_market FROM card_state").fetchall()
        return [{"id": r[0], "updated_at": r[1], "needs_backfill": bool(r[2]), "max_market": r[3]} for r in rows]
    finally:
        conn.close()


def needs_backfill(card_id: str, db_path: str | None = None) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT needs_backfill FROM card_state WHERE card_id = ?", (card_id,)).fetchone()
        return bool(row[0]) if row else True
    finally:
        conn.close()


def get_price_checksums(card_ids: list[str], db_path: str | None = None) -> dict[tuple[str, str], str]:
    """Returns {(card_id, price_type): checksum} for the given card IDs."""
    if not card_ids:
        return {}

    conn = _connect(db_path)
    try:
        placeholders = ", ".join("?" * len(card_ids))
        rows = conn.execute(
            f"SELECT card_id, price_type, checksum FROM price_checksum WHERE card_id IN ({placeholders})",
            card_ids,
        ).fetchall()
        return {(r[0], r[1]): r[2] for r in rows}
    finally:
        conn.close()


def record_check_results(
    checked_at_by_card: dict[str, str],
    backfilled_card_ids: list[str],
    price_checksums_by_key: dict[tuple[str, str], str],
    max_market_by_card: dict[str, float],
    db_path: str | None = None,
) -> None:
    """Persists the outcome of a price-sync batch after a successful D1 write.

    - checked_at_by_card: {card_id: new updated_at} for every card checked.
    - backfilled_card_ids: cards whose CARD_BACKFILL_COLUMNS were filled this round.
    - price_checksums_by_key: {(card_id, price_type): new checksum} for all
      "current"-variant prices observed this round (changed or not).
    - max_market_by_card: {card_id: max market price across "current" variants
      observed this round}, used to keep the PREMIUM/STANDARD/NO_PRICE tiering
      accurate without re-reading card_prices from D1.
    """
    conn = _connect(db_path)
    try:
        backfilled = set(backfilled_card_ids)
        for card_id, checked_at in checked_at_by_card.items():
            max_market = max_market_by_card.get(card_id)
            conn.execute(
                "INSERT INTO card_state (card_id, last_checked_at, needs_backfill, max_market) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(card_id) DO UPDATE SET last_checked_at = excluded.last_checked_at, "
                "needs_backfill = CASE WHEN ? THEN 0 ELSE needs_backfill END, "
                "max_market = CASE WHEN ? THEN excluded.max_market ELSE max_market END",
                (
                    card_id,
                    checked_at,
                    int(card_id in backfilled),
                    max_market if max_market is not None else 0.0,
                    card_id in backfilled,
                    max_market is not None,
                ),
            )

        conn.executemany(
            "INSERT OR REPLACE INTO price_checksum (card_id, price_type, checksum) VALUES (?, ?, ?)",
            [(cid, ptype, checksum) for (cid, ptype), checksum in price_checksums_by_key.items()],
        )
        conn.commit()
    finally:
        conn.close()
