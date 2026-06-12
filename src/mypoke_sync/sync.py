import asyncio
import datetime
import hashlib
import json
import logging
import random
from typing import Any

import httpx
from sqlalchemy.orm import Session

from . import d1_client, models
from .utils.phash import calculate_phash
from .pokeapi_client import fetch_pokeapi_data
from .validator import validate_card_data, validate_price_data, validate_set_data

TCGDEX_API = "https://api.tcgdex.net/v2/en"

# Global control flag
SHOULD_STOP = False


def _set_payload(s: models.Set) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "series": s.series,
        "card_count": s.card_count,
        "image_url": s.image_url,
        "release_date": s.release_date,
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }


def _card_payload(c: models.Card) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "set_id": c.set_id,
        "image_url": c.image_url,
        "phash": c.phash,
        "dex_id": c.dex_id,
        "rarity": c.rarity,
        "category": c.category,
        "illustrator": c.illustrator,
        "hp": c.hp,
        "types": c.types,
        "stage": c.stage,
        "suffix": c.suffix,
        "attacks": c.attacks,
        "weaknesses": c.weaknesses,
        "retreat": c.retreat,
        "regulation_mark": c.regulation_mark,
        "legal": c.legal,
        "flavor_text": c.flavor_text,
        "evolutions": c.evolutions,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _price_payload(card_id: str, price_type: str, vals: dict) -> dict:
    return {
        "card_id": card_id,
        "price_type": price_type,
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


def _new_d1_stats() -> dict[str, Any]:
    return {"chunks_sent": 0, "total_chunks": 0, "errors": [], "skipped": False}


def _merge_d1_result(d1_stats: dict[str, Any], result: dict[str, Any]) -> None:
    d1_stats["chunks_sent"] += result.get("chunks_sent", 0)
    d1_stats["total_chunks"] += result.get("total_chunks", 0)
    d1_stats["errors"].extend(result.get("errors", []))
    d1_stats["skipped"] = d1_stats["skipped"] or result.get("skipped", False)


async def _push_metrics_to_d1(metrics: dict[str, Any], **kwargs: Any) -> None:
    if not any(kwargs.values()):
        return
    d1_result = await d1_client.push_sync_data(**kwargs)
    _merge_d1_result(metrics["d1_sync"], d1_result)
    metrics["errors"].extend(d1_result["errors"])


async def _push_price_batch_to_d1(
    d1_stats: dict[str, Any],
    errors: list[str],
    touched_cards: list[models.Card],
    prices_payload: list[dict],
) -> None:
    if not (touched_cards or prices_payload):
        return
    d1_result = await d1_client.push_sync_data(
        cards=[_card_payload(c) for c in touched_cards],
        prices=prices_payload,
    )
    _merge_d1_result(d1_stats, d1_result)
    if len(errors) < 50:
        errors.extend(d1_result["errors"][: 50 - len(errors)])


def determine_check_strategy(card: models.Card, max_market_price: float = 0.0) -> str:
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


def should_check_price(card: models.Card, max_market_price: float = 0.0) -> bool:
    # Legacy wrapper
    return determine_check_strategy(card, max_market_price) != "SKIP"


def stop_sync():

    global SHOULD_STOP
    SHOULD_STOP = True
    print("Stopping Sync Process...")


def start_sync_flag():
    global SHOULD_STOP
    SHOULD_STOP = False


async def sync_sets_and_cards(db: Session, card_limit: int = None) -> dict:
    """
    Incremental Sync:
    1. Fetch Sets -> Insert NEW sets only.
    2. Fetch Cards List -> Filter against DB -> Insert NEW cards only (calculating pHash).
    """
    metrics: dict[str, Any] = {"new_sets": 0, "new_cards": 0, "cards_processed": 0, "errors": [], "d1_sync": _new_d1_stats()}
    new_sets_count = 0
    errors = []
    
    async with httpx.AsyncClient() as client:
        # --- 1. SETS ---
        try:
            response = await client.get(f"{TCGDEX_API}/sets")
            response.raise_for_status()
            sets_data = response.json()

            # Optimization: Fetch all existing Set IDs in batches
            existing_set_ids = set()
            for s in db.query(models.Set.id).yield_per(1000):
                existing_set_ids.add(s[0])

            sets_payload = []
            for s in sets_data:
                if s["id"] not in existing_set_ids:
                    # Validate first
                    set_val_data = {
                        "id": s.get("id"),
                        "name": s.get("name")
                    }
                    if not validate_set_data(set_val_data):
                        print(f"Skipping invalid set: {s.get('id')}")
                        continue

                    new_set = models.Set(
                        id=s["id"],
                        name=s["name"],
                        series=s.get("series"),
                        card_count=s.get("cardCount", {}).get("total")
                        if isinstance(s.get("cardCount"), dict)
                        else s.get("cardCount"),
                        image_url=f"{s.get('logo')}.png" if s.get("logo") else None,
                        release_date=s.get("releaseDate"),
                    )
                    db.add(new_set)
                    sets_payload.append(_set_payload(new_set))
                    new_sets_count += 1

            db.commit()
            print(f"Sets synced. Added {new_sets_count} new sets.")

            await _push_metrics_to_d1(metrics, sets=sets_payload)

        except Exception as e:
            errors.append(f"Sets sync error: {str(e)}")
            metrics["errors"].append(f"Sets sync error: {str(e)}")
            db.rollback()

        # --- 2. CARDS ---
        try:
            response = await client.get(f"{TCGDEX_API}/cards")
            response.raise_for_status()
            all_cards_summary = response.json()

            # Optimization: Fetch all existing Card IDs in batches to avoid RAM saturation
            # If DB is huge, fetching check set is safer than 20k individual queries
            existing_card_ids = set()
            for c in db.query(models.Card.id).yield_per(2000):
                existing_card_ids.add(c[0])

            # Identify NEW cards
            new_cards_summary = [c for c in all_cards_summary if c["id"] not in existing_card_ids]

            if card_limit:
                new_cards_summary = new_cards_summary[:card_limit]

            metrics["cards_processed"] = len(new_cards_summary)
            print(f"Found {len(new_cards_summary)} new cards to process.")

            cards_payload = []
            for card_summary in new_cards_summary:
                if SHOULD_STOP:
                    print("Sync stopping explicitly (Cards loop).")
                    break

                try:
                    # Random sleep to masquerade as human/prevent rate-limiting
                    await asyncio.sleep(random.uniform(0.1, 0.6))

                    # Fetch Full Details
                    detail_res = await client.get(f"{TCGDEX_API}/cards/{card_summary['id']}")
                    if detail_res.status_code == 404:
                        print(f"Warning: Card {card_summary['id']} returned 404 on details fetch. Skipping.")
                        continue
                        
                    detail_res.raise_for_status()
                    details = detail_res.json()

                    if "id" not in details:
                        continue

                    # Images
                    image_url_low = f"{details.get('image')}/low.png" if details.get("image") else None
                    image_url_high = f"{details.get('image')}/high.png" if details.get("image") else None

                    # Compute pHash (Expensive operation, only for NEW cards)
                    phash = await calculate_phash(image_url_high) if image_url_high else None

                    # Extract Dex ID & Fetch Lore
                    dex_ids = details.get("dexId", [])
                    dex_id = dex_ids[0] if dex_ids and isinstance(dex_ids, list) else None
                    
                    flavor_text = None
                    evolutions = None
                    if dex_id:
                        if "pokeapi_cache" not in metrics:
                            metrics["pokeapi_cache"] = {}
                        if dex_id not in metrics["pokeapi_cache"]:
                            print(f"Enriching NEW Dex ID {dex_id} from PokéAPI...")
                            flavor, evos = await fetch_pokeapi_data(dex_id)
                            # Use "" to distinguish 'checked but missing' from 'unchecked' (None/NULL)
                            metrics["pokeapi_cache"][dex_id] = (flavor if flavor else "", json.dumps(evos) if evos else None)
                        flavor_text, evolutions = metrics["pokeapi_cache"][dex_id]

                    # Validate first
                    card_val_data = {
                        "id": details.get("id"),
                        "name": details.get("name"),
                        "set_id": details.get("set", {}).get("id") if isinstance(details.get("set"), dict) else None,
                        "dex_id": dex_id
                    }
                    if not validate_card_data(card_val_data):
                        print(f"Skipping invalid card: {details.get('id')}")
                        continue

                    new_card = models.Card(
                        id=details["id"],
                        name=details["name"],
                        set_id=details["set"]["id"],
                        image_url=image_url_low,
                        phash=phash,
                        # Expanded Metadata
                        dex_id=dex_id,
                        flavor_text=flavor_text,
                        evolutions=evolutions,
                        rarity=details.get("rarity"),
                        category=details.get("category"),
                        illustrator=details.get("illustrator"),
                        hp=details.get("hp"),
                        types=json.dumps(details.get("types")) if details.get("types") else None,
                        stage=details.get("stage"),
                        suffix=details.get("suffix"),
                        attacks=json.dumps(details.get("attacks")) if details.get("attacks") else None,
                        weaknesses=json.dumps(details.get("weaknesses")) if details.get("weaknesses") else None,
                        retreat=details.get("retreat"),
                        regulation_mark=details.get("regulationMark"),
                        legal=json.dumps(details.get("legal")) if details.get("legal") else None,
                        updated_at=None  # Explicitly None to trigger immediate price check
                    )
                    db.add(new_card)
                    metrics["new_cards"] += 1
                    db.commit()
                    cards_payload.append(_card_payload(new_card))

                except Exception as e:
                    errors.append(f"Card {card_summary['id']} error: {str(e)}")
                    metrics["errors"].append(f"Card {card_summary['id']} error: {str(e)}")
                    db.rollback()

            await _push_metrics_to_d1(metrics, cards=cards_payload)

        except Exception as e:
            errors.append(f"Cards list sync error: {str(e)}")
            metrics["errors"].append(f"Cards list sync error: {str(e)}")

    print("Sets/Cards Sync Finished.")

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


async def sync_prices(db: Session, force_prices: bool = False) -> dict:
    """
    Updates prices for cards based on Temperature/Hashing strategy.
    """
    start_time = datetime.datetime.utcnow()
    print(f"[{start_time}] Starting Price Sync (Smart Strategy)...")
    errors = []

    # Avoid fetching all cards at once! We will use yield_per(1000) for batched query fetching.
    # We must count the total cards first to know how many to process.
    total_cards = db.query(models.Card.id).count()

    # Pre-flight to get strategy stats and the subset of IDs that need updating
    # We fetch card columns + max market price via subquery to classify tiers.
    card_ids_to_check = []
    strat_stats = {"NEW": 0, "PREMIUM": 0, "STANDARD": 0, "STANDARD_SAFETY": 0, "NO_PRICE": 0, "NO_PRICE_SAFETY": 0}

    # Subquery: max market price per card (excluding legacy stale variants)
    from sqlalchemy import func
    max_price_subq = (
        db.query(
            models.CardPrice.card_id,
            func.max(models.CardPrice.market).label("max_market")
        )
        .filter(models.CardPrice.price_type.notin_(["holo", "reverse", "firstEdition"]))
        .group_by(models.CardPrice.card_id)
        .subquery()
    )

    query = (
        db.query(
            models.Card.id,
            models.Card.updated_at,
            func.coalesce(max_price_subq.c.max_market, 0.0).label("max_market")
        )
        .outerjoin(max_price_subq, models.Card.id == max_price_subq.c.card_id)
    )

    for cid, last_check, max_market in query.all():
        temp_card = models.Card(
            id=cid,
            updated_at=last_check,
        )
        strat = determine_check_strategy(temp_card, float(max_market or 0.0))
        if force_prices or strat != "SKIP":
            card_ids_to_check.append(cid)
            strat_stats[strat] = strat_stats.get(strat, 0) + 1

    total_to_check = len(card_ids_to_check)

    print(f"Total Cards: {total_cards}. Scheduled for check: {total_to_check}")
    print(
        f"Breakdown: PREMIUM={strat_stats['PREMIUM']}, STANDARD={strat_stats['STANDARD']}, NO_PRICE={strat_stats['NO_PRICE']}, NEW={strat_stats['NEW']}"
    )
    # Batch size for processing and committing
    BATCH_SIZE = 50
    updated_count = 0
    checked_count = 0
    d1_stats = _new_d1_stats()

    # Detailed Stats
    stats = {
        "variant_updates": {},  # e.g. "normal": 5, "reverseHolofoil": 2
        "errors_by_type": {},
    }

    # Silence HTTPX logs to avoid spam
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    async with httpx.AsyncClient() as client:
        # Process in chunks of BATCH_SIZE
        for i in range(0, total_to_check, BATCH_SIZE):
            if SHOULD_STOP:
                print("Sync stopping explicitly (Prices loop).")
                break
                
            chunk_ids = card_ids_to_check[i : i + BATCH_SIZE]
            
            # Fetch details concurrently for the chunk
            semaphore = asyncio.Semaphore(10)
            tasks = [_fetch_card_details_concurrent(client, cid, semaphore) for cid in chunk_ids]
            results = await asyncio.gather(*tasks)
            
            # Fetch all card objects for this chunk in ONE query
            cards_chunk = db.query(models.Card).filter(models.Card.id.in_(chunk_ids)).all()
            # Map them by ID for easy access
            cards_by_id = {c.id: c for c in cards_chunk}

            batch_touched_cards = []
            batch_prices_payload = []

            for cid, response, err in results:
                card = cards_by_id.get(cid)
                if not card:
                    continue

                checked_count += 1
                try:
                    if err:
                        raise err

                    if response.status_code == 404:
                        card.updated_at = datetime.datetime.utcnow()
                        batch_touched_cards.append(card)
                        continue

                    response.raise_for_status()
                    details = response.json()

                    card.updated_at = datetime.datetime.utcnow()
                    batch_touched_cards.append(card)

                    pricing = details.get("pricing")
                    if not pricing or not isinstance(pricing, dict):
                        continue

                    prices_found = {}
                    
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
                                    "trend_30d": 0.0
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
                                        "market": 0.0, "low": 0.0, "mid": 0.0, "high": 0.0, "direct": 0.0,
                                        "avg": 0.0, "trend": 0.0, "trend_1d": 0.0, "trend_7d": 0.0, "trend_30d": 0.0
                                    }
                                
                                prices_found[variant].update({
                                    "avg": cm_data.get("avg") or 0.0,
                                    "trend": cm_data.get("trend") or 0.0,
                                    "trend_1d": cm_data.get("avg1") or 0.0,
                                    "trend_7d": cm_data.get("avg7") or 0.0,
                                    "trend_30d": cm_data.get("avg30") or 0.0
                                })
                                if prices_found[variant]["low"] == 0.0:
                                    prices_found[variant]["low"] = cm_data.get("low") or 0.0
                        else:
                            # FLAT structure (standard for TCGDex Cardmarket now)
                            # If TCGPlayer was null, we synthesize a 'normal' variant to hold the data
                            if not prices_found:
                                prices_found["normal"] = {
                                    "market": 0.0, "low": 0.0, "mid": 0.0, "high": 0.0, "direct": 0.0,
                                    "avg": 0.0, "trend": 0.0, "trend_1d": 0.0, "trend_7d": 0.0, "trend_30d": 0.0
                                }
                            
                            # Update all variants found (usually 'normal' or 'unlimited') with the flat data
                            for v in list(prices_found.keys()):
                                s = "-holo" if "holo" in v.lower() else ""
                                prices_found[v].update({
                                    "avg": cm_pricing.get(f"avg{s}") or cm_pricing.get("avg") or 0.0,
                                    "trend": cm_pricing.get(f"trend{s}") or cm_pricing.get("trend") or 0.0,
                                    "trend_1d": cm_pricing.get(f"avg1{s}") or cm_pricing.get("avg1") or 0.0,
                                    "trend_7d": cm_pricing.get(f"avg7{s}") or cm_pricing.get("avg7") or 0.0,
                                    "trend_30d": cm_pricing.get(f"avg30{s}") or cm_pricing.get("avg30") or 0.0
                                })
                                if prices_found[v]["low"] == 0.0:
                                    prices_found[v]["low"] = cm_pricing.get(f"low{s}") or cm_pricing.get("low") or 0.0

                    # 0. Backfill dex_id from details if missing
                    if not card.dex_id:
                        dex_ids = details.get("dexId", [])
                        if dex_ids and isinstance(dex_ids, list):
                            card.dex_id = dex_ids[0]
                    
                    # 1. Backfill Rarity, Category, etc. if missing
                    if not card.rarity:
                        card.rarity = details.get("rarity")
                        card.category = details.get("category")
                        card.illustrator = details.get("illustrator")
                        card.hp = details.get("hp")
                        card.types = json.dumps(details.get("types")) if details.get("types") else None
                        card.stage = details.get("stage")
                        card.suffix = details.get("suffix")
                        card.attacks = json.dumps(details.get("attacks")) if details.get("attacks") else None
                        card.weaknesses = json.dumps(details.get("weaknesses")) if details.get("weaknesses") else None
                        card.retreat = details.get("retreat")
                        card.regulation_mark = details.get("regulationMark")
                        card.legal = json.dumps(details.get("legal")) if details.get("legal") else None
                    
                    # 3. Lore Enrichment (PokéAPI) - Once per unique Dex ID
                    if card.dex_id and card.flavor_text is None:
                        if "pokeapi_cache" not in stats:
                            stats["pokeapi_cache"] = {}
                        
                        if card.dex_id not in stats["pokeapi_cache"]:
                            print(f"Enriching Dex ID {card.dex_id} from PokéAPI...")
                            flavor, evos = await fetch_pokeapi_data(card.dex_id)
                            stats["pokeapi_cache"][card.dex_id] = (flavor if flavor else "", json.dumps(evos) if evos else None)
                        
                        c_flav, c_evos = stats["pokeapi_cache"][card.dex_id]
                        card.flavor_text = c_flav
                        if c_evos:
                            card.evolutions = c_evos

                    for variant, p_vals in prices_found.items():
                        # Validate price data before update
                        price_val_data = {
                            "card_id": card.id,
                            "market": p_vals.get("market"),
                            "low": p_vals.get("low"),
                            "mid": p_vals.get("mid"),
                            "high": p_vals.get("high"),
                            "direct": p_vals.get("direct"),
                            "avg": p_vals.get("avg"),
                            "trend": p_vals.get("trend")
                        }
                        if not validate_price_data(price_val_data):
                            print(f"Skipping price record for card {card.id} variant {variant} due to validation failure.")
                            continue

                        changed, _ = await update_card_price(db, card.id, variant, p_vals)
                        if changed:
                            updated_count += 1
                            stats["variant_updates"][variant] = stats["variant_updates"].get(variant, 0) + 1
                            batch_prices_payload.append(_price_payload(card.id, variant, p_vals))

                except Exception as e:
                    err_type = type(e).__name__
                    stats["errors_by_type"][err_type] = stats["errors_by_type"].get(err_type, 0) + 1
                    if len(errors) < 50:
                        errors.append(f"{card.id}: {str(e)}")

            # Batch Commit
            db.commit()

            # Push this batch's changes to Cloudflare D1 via the Worker (chunked internally)
            await _push_price_batch_to_d1(d1_stats, errors, batch_touched_cards, batch_prices_payload)

            # Progress Log
            percent = (checked_count / total_to_check) * 100
            print(
                f"[{datetime.datetime.utcnow().strftime('%H:%M:%S')}] Progress: {checked_count}/{total_to_check} ({percent:.1f}%) - Updates: {updated_count}"
            )

    # --- FINAL REPORT ---
    print("\n" + "=" * 40)
    print("✅ PRICE SYNC COMPLETED REPORT")
    print("=" * 40)
    print(f"Total Checked:      {checked_count}/{total_to_check}")
    print("-" * 20)
    print("Strategy Breakdown:")
    for k, v in strat_stats.items():
        if v > 0:
            print(f"  - {k:<15}: {v}")
    print("-" * 20)
    print(f"Cards Updated:      {updated_count} (Triggered Delta)")
    if stats["variant_updates"]:
        print("Updates by Variant Type:")
        for v, count in stats["variant_updates"].items():
            print(f"  - {v:<15}: {count}")
    print("-" * 20)
    if stats["errors_by_type"]:
        print("Errors Encountered:")
        for err, count in stats["errors_by_type"].items():
            print(f"  - {err:<15}: {count}")
    else:
        print("No errors encountered.")
    print("-" * 20)
    if d1_stats["skipped"]:
        print("D1 Sync: SKIPPED (WORKER_URL/ADMIN_TOKEN not configured)")
    else:
        print(f"D1 Sync: {d1_stats['chunks_sent']}/{d1_stats['total_chunks']} chunks sent successfully")
    print("=" * 40 + "\n")

    return {
        "total_cards": total_cards,
        "checked_count": checked_count,
        "scheduled_for_check": total_to_check,
        "updated_count": updated_count,
        "strategy_breakdown": strat_stats,
        "variant_updates": stats["variant_updates"],
        "errors_by_type": stats["errors_by_type"],
        "error_list": errors,
        "d1_sync": d1_stats,
    }


async def update_card_price(db: Session, card_id: str, variant: str, vals: dict) -> tuple[bool, bool]:
    """
    Returns (any_changed, is_significant).
    True if ANY price changed significantly (> 0.01), and a second boolean for major price swings (> 5% or > $0.50).
    """
    market = vals.get("market", 0.0)
    low = vals.get("low", 0.0)
    mid = vals.get("mid", 0.0)
    high = vals.get("high", 0.0)
    direct = vals.get("direct", 0.0)
    avg = vals.get("avg", 0.0)
    trend = vals.get("trend", 0.0)
    
    # Trend extraction (defaults to 0.0 if not present)
    t1 = vals.get("trend_1d", 0.0)
    t7 = vals.get("trend_7d", 0.0)
    t30 = vals.get("trend_30d", 0.0)

    # 2. Update Price
    current = (
        db.query(models.CardPrice)
        .filter(models.CardPrice.card_id == card_id, models.CardPrice.price_type == variant)
        .first()
    )

    any_changed = False
    is_significant = False

    if not current:
        # New Price Record
        new_p = models.CardPrice(
            card_id=card_id, 
            price_type=variant, 
            market=market, 
            low=low, 
            mid=mid,
            high=high,
            direct=direct,
            avg=avg,
            trend=trend,
            trend_1d=t1,
            trend_7d=t7,
            trend_30d=t30
        )
        db.add(new_p)
        any_changed = True
        is_significant = True
    else:
        # Detect Changes (Primary on market)
        market_diff = abs(current.market - market)
        
        if market_diff > 0.01:
            any_changed = True
            
            # Is the change significant enough to restart the temperature? (> 5% or flat > $0.50)
            if current.market and current.market > 0:
                percent_change = market_diff / current.market
                if percent_change >= 0.05 or market_diff >= 0.50:
                    is_significant = True
            elif market_diff >= 0.50:
                is_significant = True
        
        # Update all fields
        current.market = market
        current.low = low
        current.mid = mid
        current.high = high
        current.direct = direct
        current.avg = avg
        current.trend = trend
        current.trend_1d = t1
        current.trend_7d = t7
        current.trend_30d = t30


    return any_changed, is_significant
