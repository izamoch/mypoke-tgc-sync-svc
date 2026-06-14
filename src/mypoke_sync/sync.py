import asyncio
import datetime
import hashlib
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

from . import d1_client, local_index
from .pokeapi_client import fetch_pokeapi_data
from .utils.phash import calculate_phash
from .validator import validate_card_data, validate_price_data, validate_set_data

TCGDEX_API = "https://api.tcgdex.net/v2/en"

logger = logging.getLogger("sync")

# Global control flag
SHOULD_STOP = False

# Number of newly-created cards collected before upserting to D1, so a
# mid-run crash only loses at most one batch of already-fetched cards.
CARD_PUSH_BATCH_SIZE = 20

# Batch size for the price-sync loop: how many cards are fetched/processed
# from TCGDex and written to D1 per iteration.
PRICE_BATCH_SIZE = 50

SET_COLUMNS = ["id", "name", "series", "card_count", "image_url", "release_date", "updated_at"]

CARD_COLUMNS = [
    "id",
    "name",
    "set_id",
    "image_url",
    "phash",
    "dex_id",
    "rarity",
    "category",
    "illustrator",
    "hp",
    "types",
    "stage",
    "suffix",
    "attacks",
    "weaknesses",
    "retreat",
    "regulation_mark",
    "legal",
    "flavor_text",
    "evolutions",
    "updated_at",
]

# Metadata fields that get backfilled/refreshed during price sync.
CARD_BACKFILL_COLUMNS = [
    "dex_id",
    "rarity",
    "category",
    "illustrator",
    "hp",
    "types",
    "stage",
    "suffix",
    "attacks",
    "weaknesses",
    "retreat",
    "regulation_mark",
    "legal",
    "flavor_text",
    "evolutions",
]

PRICE_COLUMNS = [
    "card_id",
    "price_type",
    "market",
    "low",
    "mid",
    "high",
    "direct",
    "avg",
    "trend",
    "trend_1d",
    "trend_7d",
    "trend_30d",
    "updated_at",
]


@dataclass
class Card:
    """Minimal stand-in for a `cards` row, used by `determine_check_strategy`."""

    id: str
    updated_at: datetime.datetime | None = None


def _parse_dt(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    return datetime.datetime.fromisoformat(value)


def determine_check_strategy(card: Card, max_market_price: float = 0.0) -> str:
    """
    Determines WHY a card is checked (or skipped).
    Uses a hybrid value-tier + hash rotation strategy with minimum cooldowns:
      - PREMIUM (market >= $20): checked daily (cooldown ~20 hours / 0.83 days)
      - STANDARD (market $0-$20): checked via hash % 5 (~every 5 days, cooldown 4 days)
      - NO_PRICE (no price data): checked via hash % 15 (~every 15 days, cooldown 12 days)
    Returns: 'PREMIUM', 'STANDARD', 'NO_PRICE', 'NEW' if checked.
    Returns: 'SKIP' if skipped.
    """
    now = datetime.datetime.utcnow()

    # 1. Never checked? -> IMMEDIATE
    if not card.updated_at:
        return "NEW"

    # Use total_seconds to get fractional days instead of integer flooring
    last_check_days = (now - card.updated_at).total_seconds() / 86400.0
    card_hash = int(hashlib.sha256(card.id.encode("utf-8")).hexdigest(), 16)
    day_of_year = now.timetuple().tm_yday

    # 2. PREMIUM: daily check (allow ~20h window for cron flexibility)
    if max_market_price >= 20.0:
        return "PREMIUM" if last_check_days >= 0.83 else "SKIP"

    # 3. NO_PRICE: rotate hash % 15 (~every 15 days) with 12-day cooldown
    if max_market_price == 0.0:
        if last_check_days > 20:
            return "NO_PRICE_SAFETY"
        return "NO_PRICE" if ((card_hash % 15) == (day_of_year % 15) and last_check_days >= 12.0) else "SKIP"

    # 4. STANDARD: rotate hash % 5 (~every 5 days) with 4-day cooldown
    if last_check_days > 8:
        return "STANDARD_SAFETY"
    return "STANDARD" if ((card_hash % 5) == (day_of_year % 5) and last_check_days >= 4.0) else "SKIP"


def should_check_price(card: Card, max_market_price: float = 0.0) -> bool:
    # Legacy wrapper
    return determine_check_strategy(card, max_market_price) != "SKIP"


def stop_sync():
    global SHOULD_STOP
    SHOULD_STOP = True
    logger.info("Stopping Sync Process...")


def start_sync_flag():
    global SHOULD_STOP
    SHOULD_STOP = False


async def _flush_new_cards(cards_payload: list[dict], metrics: dict) -> None:
    """Upserts a batch of new cards to D1 and, on success, registers them in the local index."""
    result = await d1_client.chunked_upsert("cards", CARD_COLUMNS, ["id"], cards_payload)
    metrics["d1_errors"].extend(result["errors"])
    if not result["errors"]:
        local_index.register_new_cards([row["id"] for row in cards_payload])


async def sync_sets_and_cards(card_limit: int | None = None) -> dict:
    """
    Incremental Sync:
    1. Fetch Sets -> Insert NEW sets only.
    2. Fetch Cards List -> Filter against D1 -> Insert NEW cards only (calculating pHash).
    """
    metrics: dict[str, Any] = {
        "new_sets": 0,
        "new_cards": 0,
        "cards_processed": 0,
        "errors": [],
        "d1_errors": [],
    }
    new_sets_count = 0

    async with httpx.AsyncClient() as client:
        # --- 1. SETS ---
        try:
            response = await client.get(f"{TCGDEX_API}/sets")
            response.raise_for_status()
            sets_data = response.json()

            existing_rows = await d1_client.d1_query("SELECT id FROM sets")
            existing_set_ids = {row["id"] for row in existing_rows}

            sets_payload = []
            for s in sets_data:
                if s["id"] in existing_set_ids:
                    continue

                set_val_data = {"id": s.get("id"), "name": s.get("name")}
                if not validate_set_data(set_val_data):
                    logger.warning(f"Skipping invalid set: {s.get('id')}")
                    continue

                card_count = s.get("cardCount")
                sets_payload.append(
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "series": s.get("series"),
                        "card_count": card_count.get("total") if isinstance(card_count, dict) else card_count,
                        "image_url": f"{s.get('logo')}.png" if s.get("logo") else None,
                        "release_date": s.get("releaseDate"),
                        "updated_at": datetime.datetime.utcnow().isoformat(),
                    }
                )
                new_sets_count += 1

            if sets_payload:
                result = await d1_client.chunked_upsert("sets", SET_COLUMNS, ["id"], sets_payload)
                metrics["d1_errors"].extend(result["errors"])

            logger.info(f"Sets synced. Added {new_sets_count} new sets.")

        except Exception as e:
            logger.exception("Sets sync error")
            metrics["errors"].append(f"Sets sync error: {e!r}")

        # --- 2. CARDS ---
        try:
            response = await client.get(f"{TCGDEX_API}/cards")
            response.raise_for_status()
            all_cards_summary = response.json()

            existing_rows = await d1_client.d1_query("SELECT id FROM cards")
            existing_card_ids = {row["id"] for row in existing_rows}

            new_cards_summary = [c for c in all_cards_summary if c["id"] not in existing_card_ids]
            if card_limit:
                new_cards_summary = new_cards_summary[:card_limit]

            metrics["cards_processed"] = len(new_cards_summary)
            logger.info(f"Found {len(new_cards_summary)} new cards to process.")

            pokeapi_cache: dict[int, tuple[str, str | None]] = {}
            cards_payload: list[dict] = []

            for card_summary in new_cards_summary:
                if SHOULD_STOP:
                    logger.info("Sync stopping explicitly (Cards loop).")
                    break

                try:
                    # Random sleep to masquerade as human/prevent rate-limiting
                    await asyncio.sleep(random.uniform(0.1, 0.6))

                    detail_res = await client.get(f"{TCGDEX_API}/cards/{card_summary['id']}")
                    if detail_res.status_code == 404:
                        logger.warning(f"Card {card_summary['id']} returned 404 on details fetch. Skipping.")
                        continue

                    detail_res.raise_for_status()
                    details = detail_res.json()

                    if "id" not in details:
                        continue

                    image_url_low = f"{details.get('image')}/low.png" if details.get("image") else None
                    image_url_high = f"{details.get('image')}/high.png" if details.get("image") else None

                    # Compute pHash (expensive, only for NEW cards)
                    phash = await calculate_phash(image_url_high) if image_url_high else None

                    dex_ids = details.get("dexId", [])
                    dex_id = dex_ids[0] if dex_ids and isinstance(dex_ids, list) else None

                    flavor_text = None
                    evolutions = None
                    if dex_id:
                        if dex_id not in pokeapi_cache:
                            logger.info(f"Enriching NEW Dex ID {dex_id} from PokéAPI...")
                            flavor, evos = await fetch_pokeapi_data(dex_id)
                            # Use "" to distinguish 'checked but missing' from 'unchecked' (None/NULL)
                            pokeapi_cache[dex_id] = (flavor or "", json.dumps(evos) if evos else None)
                        flavor_text, evolutions = pokeapi_cache[dex_id]

                    set_id = details.get("set", {}).get("id") if isinstance(details.get("set"), dict) else None
                    card_val_data = {
                        "id": details.get("id"),
                        "name": details.get("name"),
                        "set_id": set_id,
                        "dex_id": dex_id,
                    }
                    if not validate_card_data(card_val_data):
                        logger.warning(f"Skipping invalid card: {details.get('id')}")
                        continue

                    cards_payload.append(
                        {
                            "id": details["id"],
                            "name": details["name"],
                            "set_id": set_id,
                            "image_url": image_url_low,
                            "phash": phash,
                            "dex_id": dex_id,
                            "rarity": details.get("rarity"),
                            "category": details.get("category"),
                            "illustrator": details.get("illustrator"),
                            "hp": details.get("hp"),
                            "types": json.dumps(details.get("types")) if details.get("types") else None,
                            "stage": details.get("stage"),
                            "suffix": details.get("suffix"),
                            "attacks": json.dumps(details.get("attacks")) if details.get("attacks") else None,
                            "weaknesses": json.dumps(details.get("weaknesses")) if details.get("weaknesses") else None,
                            "retreat": details.get("retreat"),
                            "regulation_mark": details.get("regulationMark"),
                            "legal": json.dumps(details.get("legal")) if details.get("legal") else None,
                            "flavor_text": flavor_text,
                            "evolutions": evolutions,
                            # Explicitly None to trigger an immediate price check next cycle
                            "updated_at": None,
                        }
                    )
                    metrics["new_cards"] += 1

                    if len(cards_payload) >= CARD_PUSH_BATCH_SIZE:
                        await _flush_new_cards(cards_payload, metrics)
                        cards_payload = []

                except Exception as e:
                    logger.exception(f"Card {card_summary['id']} error")
                    metrics["errors"].append(f"Card {card_summary['id']} error: {e!r}")

            if cards_payload:
                await _flush_new_cards(cards_payload, metrics)

        except Exception as e:
            logger.exception("Cards list sync error")
            metrics["errors"].append(f"Cards list sync error: {e!r}")

    logger.info("Sets/Cards Sync Finished.")
    metrics["new_sets"] = new_sets_count
    return metrics


async def _fetch_card_details_concurrent(client: httpx.AsyncClient, card_id: str, semaphore: asyncio.Semaphore):
    async with semaphore:
        # Small random delay to stagger concurrent requests
        await asyncio.sleep(random.uniform(0.05, 0.15))
        try:
            response = await client.get(f"{TCGDEX_API}/cards/{card_id}")
            return card_id, response, None
        except Exception as e:
            return card_id, None, e


def _extract_prices_found(pricing: dict) -> dict[str, dict]:
    """Parses a TCGDex `pricing` object into {variant: {market, low, ..., trend_30d}}."""
    prices_found: dict[str, dict] = {}

    # 1. TCGPlayer (Main Prices)
    tcg_pricing = pricing.get("tcgplayer")
    if tcg_pricing and isinstance(tcg_pricing, dict):
        for variant, p_data in tcg_pricing.items():
            if not isinstance(p_data, dict):
                continue

            market_p = p_data.get("marketPrice") or 0.0
            low_p = p_data.get("lowPrice") or 0.0
            mid_p = p_data.get("midPrice") or 0.0
            high_p = p_data.get("highPrice") or 0.0
            direct_p = p_data.get("directLowPrice") or 0.0

            if any([market_p, low_p, mid_p, high_p, direct_p]):
                prices_found[variant] = {
                    "market": market_p,
                    "low": low_p,
                    "mid": mid_p,
                    "high": high_p,
                    "direct": direct_p,
                    "avg": 0.0,
                    "trend": 0.0,
                    "trend_1d": 0.0,
                    "trend_7d": 0.0,
                    "trend_30d": 0.0,
                }

    # 2. Cardmarket (Supplement) - Handle both flat and nested JSON
    cm_pricing = pricing.get("cardmarket")
    if cm_pricing and isinstance(cm_pricing, dict):
        is_nested = any(isinstance(v, dict) for v in cm_pricing.values())
        if is_nested:
            for variant, cm_data in cm_pricing.items():
                if not isinstance(cm_data, dict):
                    continue

                # Create generic variant entry if TCGPlayer missed it or not in found
                if variant not in prices_found:
                    prices_found[variant] = {
                        "market": 0.0,
                        "low": 0.0,
                        "mid": 0.0,
                        "high": 0.0,
                        "direct": 0.0,
                        "avg": 0.0,
                        "trend": 0.0,
                        "trend_1d": 0.0,
                        "trend_7d": 0.0,
                        "trend_30d": 0.0,
                    }

                prices_found[variant].update(
                    {
                        "avg": cm_data.get("avg") or 0.0,
                        "trend": cm_data.get("trend") or 0.0,
                        "trend_1d": cm_data.get("avg1") or 0.0,
                        "trend_7d": cm_data.get("avg7") or 0.0,
                        "trend_30d": cm_data.get("avg30") or 0.0,
                    }
                )
                if prices_found[variant]["low"] == 0.0:
                    prices_found[variant]["low"] = cm_data.get("low") or 0.0
        else:
            # FLAT structure (standard for TCGDex Cardmarket now).
            # If TCGPlayer was null, synthesize a 'normal' variant to hold the data.
            if not prices_found:
                prices_found["normal"] = {
                    "market": 0.0,
                    "low": 0.0,
                    "mid": 0.0,
                    "high": 0.0,
                    "direct": 0.0,
                    "avg": 0.0,
                    "trend": 0.0,
                    "trend_1d": 0.0,
                    "trend_7d": 0.0,
                    "trend_30d": 0.0,
                }

            # Update all variants found (usually 'normal' or 'unlimited') with the flat data
            for v in list(prices_found.keys()):
                s = "-holo" if "holo" in v.lower() else ""
                prices_found[v].update(
                    {
                        "avg": cm_pricing.get(f"avg{s}") or cm_pricing.get("avg") or 0.0,
                        "trend": cm_pricing.get(f"trend{s}") or cm_pricing.get("trend") or 0.0,
                        "trend_1d": cm_pricing.get(f"avg1{s}") or cm_pricing.get("avg1") or 0.0,
                        "trend_7d": cm_pricing.get(f"avg7{s}") or cm_pricing.get("avg7") or 0.0,
                        "trend_30d": cm_pricing.get(f"avg30{s}") or cm_pricing.get("avg30") or 0.0,
                    }
                )
                if prices_found[v]["low"] == 0.0:
                    prices_found[v]["low"] = cm_pricing.get(f"low{s}") or cm_pricing.get("low") or 0.0

    return prices_found


def _backfill_card_fields(update_row: dict, details: dict) -> None:
    """Fills in dex_id/rarity/category/etc. on `update_row` (in place) if still missing."""
    if not update_row.get("dex_id"):
        dex_ids = details.get("dexId", [])
        if dex_ids and isinstance(dex_ids, list):
            update_row["dex_id"] = dex_ids[0]

    if not update_row.get("rarity"):
        update_row["rarity"] = details.get("rarity")
        update_row["category"] = details.get("category")
        update_row["illustrator"] = details.get("illustrator")
        update_row["hp"] = details.get("hp")
        update_row["types"] = json.dumps(details.get("types")) if details.get("types") else None
        update_row["stage"] = details.get("stage")
        update_row["suffix"] = details.get("suffix")
        update_row["attacks"] = json.dumps(details.get("attacks")) if details.get("attacks") else None
        update_row["weaknesses"] = json.dumps(details.get("weaknesses")) if details.get("weaknesses") else None
        update_row["retreat"] = details.get("retreat")
        update_row["regulation_mark"] = details.get("regulationMark")
        update_row["legal"] = json.dumps(details.get("legal")) if details.get("legal") else None


def _build_price_row(card_id: str, variant: str, vals: dict) -> dict:
    """Builds the full card_prices row dict (ready for chunked_upsert) from freshly fetched `vals`."""
    return {
        "card_id": card_id,
        "price_type": variant,
        "market": vals.get("market", 0.0),
        "low": vals.get("low", 0.0),
        "mid": vals.get("mid", 0.0),
        "high": vals.get("high", 0.0),
        "direct": vals.get("direct", 0.0),
        "avg": vals.get("avg", 0.0),
        "trend": vals.get("trend", 0.0),
        "trend_1d": vals.get("trend_1d", 0.0),
        "trend_7d": vals.get("trend_7d", 0.0),
        "trend_30d": vals.get("trend_30d", 0.0),
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }


def update_card_price(current: dict | None, card_id: str, variant: str, vals: dict) -> tuple[bool, bool, dict]:
    """
    Compares `vals` (freshly fetched prices) against `current` (the existing
    card_prices row, or None if the variant doesn't exist yet).

    Returns (any_changed, is_significant, new_row):
      - any_changed: True if the market price differs by > $0.01, or the row is new.
      - is_significant: True for major price swings (> 5% or > $0.50), used to
        signal that the card's "temperature" should reset.
      - new_row: the full card_prices row dict ready for chunked_upsert.
    """
    market = vals.get("market", 0.0)
    any_changed = False
    is_significant = False

    if current is None:
        any_changed = True
        is_significant = True
    else:
        current_market = current.get("market") or 0.0
        market_diff = abs(current_market - market)

        if market_diff > 0.01:
            any_changed = True

            if current_market > 0:
                percent_change = market_diff / current_market
                if percent_change >= 0.05 or market_diff >= 0.50:
                    is_significant = True
            elif market_diff >= 0.50:
                is_significant = True

    return any_changed, is_significant, _build_price_row(card_id, variant, vals)


async def _enrich_card_lore(update_row: dict, pokeapi_cache: dict[int, tuple[str, str | None]]) -> None:
    """Fills in flavor_text/evolutions from PokéAPI, once per unique Dex ID."""
    if not update_row.get("dex_id") or update_row.get("flavor_text") is not None:
        return

    dex_id = update_row["dex_id"]
    if dex_id not in pokeapi_cache:
        logger.info(f"Enriching Dex ID {dex_id} from PokéAPI...")
        flavor, evos = await fetch_pokeapi_data(dex_id)
        pokeapi_cache[dex_id] = (flavor or "", json.dumps(evos) if evos else None)

    flavor_text, evolutions = pokeapi_cache[dex_id]
    update_row["flavor_text"] = flavor_text
    if evolutions:
        update_row["evolutions"] = evolutions


def _process_card_prices(
    cid: str,
    pricing: dict,
    price_checksums: dict[tuple[str, str], str],
    prices_payload: list[dict],
    new_checksums_by_key: dict[tuple[str, str], str],
    stats: dict[str, dict],
) -> tuple[int, float]:
    """Compares each price variant's checksum against the local index.

    Returns (updated_count, max_market across "current" variants).
    """
    updated = 0
    card_max_market = 0.0
    for variant, p_vals in _extract_prices_found(pricing).items():
        price_val_data = {
            "card_id": cid,
            "market": p_vals.get("market"),
            "low": p_vals.get("low"),
            "mid": p_vals.get("mid"),
            "high": p_vals.get("high"),
            "direct": p_vals.get("direct"),
            "avg": p_vals.get("avg"),
            "trend": p_vals.get("trend"),
        }
        if not validate_price_data(price_val_data):
            logger.warning(f"Skipping price record for card {cid} variant {variant} due to validation failure.")
            continue

        if variant not in local_index.NON_CURRENT_PRICE_TYPES:
            card_max_market = max(card_max_market, float(p_vals.get("market") or 0.0))

        new_checksum = local_index.price_checksum(p_vals)
        new_checksums_by_key[(cid, variant)] = new_checksum

        if price_checksums.get((cid, variant)) != new_checksum:
            prices_payload.append(_build_price_row(cid, variant, p_vals))
            updated += 1
            stats["variant_updates"][variant] = stats["variant_updates"].get(variant, 0) + 1

    return updated, card_max_market


async def _process_price_card_result(
    cid: str,
    response: httpx.Response,
    *,
    cards_by_id: dict[str, dict],
    price_checksums: dict[tuple[str, str], str],
    pokeapi_cache: dict[int, tuple[str, str | None]],
    cards_full_payload: list[dict],
    cards_touch_payload: list[dict],
    prices_payload: list[dict],
    checked_at_by_card: dict[str, str],
    backfilled_card_ids: list[str],
    new_checksums_by_key: dict[tuple[str, str], str],
    max_market_by_card: dict[str, float],
    stats: dict[str, dict],
) -> int:
    """Processes one card's TCGDex response, queuing D1 writes and local-index updates.

    Mutates the payload/lookup dicts in place. Returns the number of price
    variants that changed (added to `updated_count`).
    """
    now_iso = datetime.datetime.utcnow().isoformat()
    checked_at_by_card[cid] = now_iso

    if response.status_code == 404:
        if cid in cards_by_id:
            cards_full_payload.append({**cards_by_id[cid], "updated_at": now_iso})
        else:
            cards_touch_payload.append({"id": cid, "updated_at": now_iso})
        return 0

    response.raise_for_status()
    details = response.json()

    update_row = dict(cards_by_id.get(cid, {"id": cid}))
    update_row["updated_at"] = now_iso

    pricing = details.get("pricing")
    if cid in cards_by_id:
        _backfill_card_fields(update_row, details)
        await _enrich_card_lore(update_row, pokeapi_cache)

        cards_full_payload.append(update_row)
        if update_row.get("rarity"):
            backfilled_card_ids.append(cid)
    else:
        cards_touch_payload.append({"id": cid, "updated_at": now_iso})

    if not pricing or not isinstance(pricing, dict):
        return 0

    updated, card_max_market = _process_card_prices(
        cid, pricing, price_checksums, prices_payload, new_checksums_by_key, stats
    )
    max_market_by_card[cid] = card_max_market
    return updated


def _select_price_check_candidates(rows: list[dict], force_prices: bool) -> tuple[list[str], dict[str, int]]:
    """Applies the Smart Sync temperature strategy to local-index candidates.

    Returns (card_ids_to_check, strategy_breakdown).
    """
    card_ids_to_check: list[str] = []
    strat_stats = {"NEW": 0, "PREMIUM": 0, "STANDARD": 0, "STANDARD_SAFETY": 0, "NO_PRICE": 0, "NO_PRICE_SAFETY": 0}

    for row in rows:
        temp_card = Card(id=row["id"], updated_at=_parse_dt(row["updated_at"]))
        strat = determine_check_strategy(temp_card, row["max_market"])
        if force_prices or strat != "SKIP":
            card_ids_to_check.append(row["id"])
            strat_stats[strat] = strat_stats.get(strat, 0) + 1

    return card_ids_to_check, strat_stats


async def _flush_price_batch_writes(
    cards_full_payload: list[dict],
    cards_touch_payload: list[dict],
    prices_payload: list[dict],
    checked_at_by_card: dict[str, str],
    backfilled_card_ids: list[str],
    new_checksums_by_key: dict[tuple[str, str], str],
    max_market_by_card: dict[str, float],
    d1_errors: list[str],
) -> None:
    """Writes a price-sync batch to D1 and, if all writes succeed, updates the local index."""
    d1_write_ok = True

    if cards_full_payload:
        result = await d1_client.chunked_update(
            "cards", CARD_BACKFILL_COLUMNS + ["updated_at"], "id", cards_full_payload
        )
        d1_errors.extend(result["errors"])
        d1_write_ok = d1_write_ok and not result["errors"]

    if cards_touch_payload:
        result = await d1_client.chunked_update("cards", ["updated_at"], "id", cards_touch_payload)
        d1_errors.extend(result["errors"])
        d1_write_ok = d1_write_ok and not result["errors"]

    if prices_payload:
        result = await d1_client.chunked_upsert("card_prices", PRICE_COLUMNS, ["card_id", "price_type"], prices_payload)
        d1_errors.extend(result["errors"])
        d1_write_ok = d1_write_ok and not result["errors"]

    if d1_write_ok:
        local_index.record_check_results(
            checked_at_by_card, backfilled_card_ids, new_checksums_by_key, max_market_by_card
        )


async def sync_prices(force_prices: bool = False) -> dict:
    """
    Updates prices for cards based on the Smart Sync temperature/hashing strategy.

    Candidate selection and price-change detection run against a local SQLite
    index (see `local_index`) instead of reading `cards`/`card_prices` from D1,
    since D1 bills/limits on rows read. The index is seeded once from D1 and
    then kept in sync locally after every successful write.
    """
    start_time = datetime.datetime.utcnow()
    logger.info(f"[{start_time}] Starting Price Sync (Smart Strategy)...")

    await local_index.seed_from_d1()
    rows = local_index.get_candidates()
    total_cards = len(rows)

    card_ids_to_check, strat_stats = _select_price_check_candidates(rows, force_prices)
    total_to_check = len(card_ids_to_check)
    logger.info(f"Total Cards: {total_cards}. Scheduled for check: {total_to_check}")
    logger.info(
        f"Breakdown: PREMIUM={strat_stats['PREMIUM']}, STANDARD={strat_stats['STANDARD']}, "
        f"NO_PRICE={strat_stats['NO_PRICE']}, NEW={strat_stats['NEW']}"
    )

    updated_count = 0
    checked_count = 0
    errors: list[str] = []
    d1_errors: list[str] = []
    pokeapi_cache: dict[int, tuple[str, str | None]] = {}
    stats: dict[str, dict] = {"variant_updates": {}, "errors_by_type": {}}

    # Silence HTTPX logs to avoid spam
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    async with httpx.AsyncClient() as client:
        for i in range(0, total_to_check, PRICE_BATCH_SIZE):
            if SHOULD_STOP:
                logger.info("Sync stopping explicitly (Prices loop).")
                break

            chunk_ids = card_ids_to_check[i : i + PRICE_BATCH_SIZE]

            # Fetch details concurrently for the chunk
            semaphore = asyncio.Semaphore(10)
            tasks = [_fetch_card_details_concurrent(client, cid, semaphore) for cid in chunk_ids]
            results = await asyncio.gather(*tasks)

            # Only cards still missing CARD_BACKFILL_COLUMNS need a D1 read for
            # their current values (used as the base row before merging fresh data).
            backfill_ids = [cid for cid in chunk_ids if local_index.needs_backfill(cid)]
            cards_by_id: dict[str, dict] = {}
            if backfill_ids:
                placeholders = ", ".join("?" * len(backfill_ids))
                cards_rows = await d1_client.d1_query(
                    f"SELECT id, {', '.join(CARD_BACKFILL_COLUMNS)} FROM cards WHERE id IN ({placeholders})",
                    backfill_ids,
                )
                cards_by_id = {row["id"]: row for row in cards_rows}

            price_checksums = local_index.get_price_checksums(chunk_ids)

            cards_full_payload: list[dict] = []
            cards_touch_payload: list[dict] = []
            prices_payload: list[dict] = []
            checked_at_by_card: dict[str, str] = {}
            backfilled_card_ids: list[str] = []
            new_checksums_by_key: dict[tuple[str, str], str] = {}
            max_market_by_card: dict[str, float] = {}

            for cid, response, err in results:
                checked_count += 1
                try:
                    if err:
                        raise err

                    updated_count += await _process_price_card_result(
                        cid,
                        response,
                        cards_by_id=cards_by_id,
                        price_checksums=price_checksums,
                        pokeapi_cache=pokeapi_cache,
                        cards_full_payload=cards_full_payload,
                        cards_touch_payload=cards_touch_payload,
                        prices_payload=prices_payload,
                        checked_at_by_card=checked_at_by_card,
                        backfilled_card_ids=backfilled_card_ids,
                        new_checksums_by_key=new_checksums_by_key,
                        max_market_by_card=max_market_by_card,
                        stats=stats,
                    )

                except Exception as e:
                    err_type = type(e).__name__
                    stats["errors_by_type"][err_type] = stats["errors_by_type"].get(err_type, 0) + 1
                    if len(errors) < 50:
                        errors.append(f"{cid}: {e!r}")

            await _flush_price_batch_writes(
                cards_full_payload,
                cards_touch_payload,
                prices_payload,
                checked_at_by_card,
                backfilled_card_ids,
                new_checksums_by_key,
                max_market_by_card,
                d1_errors,
            )

            percent = (checked_count / total_to_check) * 100 if total_to_check else 100.0
            logger.info(f"Progress: {checked_count}/{total_to_check} ({percent:.1f}%) - Updates: {updated_count}")

    logger.info("Price Sync Completed.")
    logger.info(f"Total Checked: {checked_count}/{total_to_check}")
    logger.info(f"Cards Updated: {updated_count} (Triggered Delta)")
    if stats["errors_by_type"]:
        logger.info(f"Errors by type: {stats['errors_by_type']}")
    if d1_errors:
        logger.error(f"D1 write errors: {d1_errors}")

    return {
        "total_cards": total_cards,
        "checked_count": checked_count,
        "scheduled_for_check": total_to_check,
        "updated_count": updated_count,
        "strategy_breakdown": strat_stats,
        "variant_updates": stats["variant_updates"],
        "errors_by_type": stats["errors_by_type"],
        "error_list": errors,
        "d1_errors": d1_errors,
    }
