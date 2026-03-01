import httpx
import asyncio
from sqlalchemy.orm import Session
from sqlalchemy import or_
import models, database
from utils.phash import calculate_phash
import json
import datetime
import random
from datetime import timedelta



import hashlib
import logging

TCGDEX_API = "https://api.tcgdex.net/v2/en"

# Global control flag
SHOULD_STOP = False


def determine_check_strategy(card: models.Card) -> str:
    """
    Determines WHY a card is checked (or skipped).
    Returns: 'HOT', 'STABLE', 'COLD' if checked.
    Returns: 'SKIP' if skipped.
    """
    now = datetime.datetime.utcnow()
    
    # 0. Never checked? -> IMMEDIATE
    if not card.last_price_check_at:
        return 'NEW'
    
    last_check_days = (now - card.last_price_check_at).days
    last_update_days = (now - card.updated_at).days
    
    # 1. HOT
    if last_update_days < 7:
        return 'HOT' if last_check_days >= 1 else 'SKIP'
        
    card_hash = int(hashlib.sha256(card.id.encode('utf-8')).hexdigest(), 16)
    day_of_year = now.timetuple().tm_yday
    
    # 2. STABLE
    if last_update_days < 30:
        if last_check_days > 10: return 'STABLE_SAFETY'
        if (card_hash % 7) == (day_of_year % 7): return 'STABLE'
        return 'SKIP'
        
    # 3. COLD
    if last_check_days > 45: return 'COLD_SAFETY'
    if (card_hash % 30) == (day_of_year % 30): return 'COLD'
    return 'SKIP'

def should_check_price(card: models.Card) -> bool:
    # Legacy wrapper if needed, but we will assume sync_prices uses determine_check_strategy now
    return determine_check_strategy(card) != 'SKIP'

def stop_sync():

    global SHOULD_STOP
    SHOULD_STOP = True
    print("Stopping Sync Process...")

def start_sync_flag():
    global SHOULD_STOP
    SHOULD_STOP = False

def maintain_price_history(db: Session, card_id: str, variant: str):
    """
    Retention Policy:
    - Last 7 days: 1 record per day.
    - 7-30 days: 2 records per week (approx every 3.5 days).
    - 30-365 days: 2 records per month (approx every 15 days).
    - Older: 1 record per month.
    """
    now = datetime.datetime.utcnow()
    # Fetch all history for this variant, ordered by time DESC
    history = db.query(models.CardPriceHistory).filter(
        models.CardPriceHistory.card_id == card_id,
        models.CardPriceHistory.price_type == variant
    ).order_by(models.CardPriceHistory.timestamp.desc()).all()

    seen_buckets = set()
    to_delete = []

    for record in history:
        age_days = (now - record.timestamp).days
        
        if age_days <= 7:
            # 1 record per day
            bucket = f"d_{age_days}" # d_0, d_1, ...
        elif age_days <= 30:
            # 2 records per week -> Bucket by 3-day chunks
            # 0-3, 4-7... relative to week? No, let's just bucket by (days // 3)
            # This ensures roughly 2-3 per week.
            bucket = f"w_{age_days // 4}" 
        elif age_days <= 365:
            # 2 records per month -> Bucket by 15 days
            bucket = f"m_{age_days // 15}"
        else:
            # Older than a year: Delete
            to_delete.append(record)
            continue

        if bucket in seen_buckets:
            to_delete.append(record)
        else:
            seen_buckets.add(bucket)

    if to_delete:
        # Batch delete
        for record in to_delete:
            db.delete(record)
        # We don't commit here to allow caller to bulk commit

async def sync_sets_and_cards(db: Session, card_limit: int = None) -> dict:
    """
    Incremental Sync:
    1. Fetch Sets -> Insert NEW sets only.
    2. Fetch Cards List -> Filter against DB -> Insert NEW cards only (calculating pHash).
    """
    start_time = datetime.datetime.utcnow()
    log = models.SyncLog(sync_type='cards', started_at=start_time, status='running')
    db.add(log)
    db.commit()
    
    print(f"[{start_time}] Starting Incremental Sets/Cards Sync...")
    errors = []
    
    async with httpx.AsyncClient() as client:
        # --- 1. SETS ---
        try:
            response = await client.get(f"{TCGDEX_API}/sets")
            response.raise_for_status()
            sets_data = response.json()
            
            # Optimization: Fetch all existing Set IDs
            existing_set_ids = {s.id for s in db.query(models.Set.id).all()}
            
            metrics = {
                "new_sets": 0,
                "new_cards": 0,
                "cards_processed": 0,
                "errors": []
            }
            new_sets_count = 0
            for s in sets_data:
                if s['id'] not in existing_set_ids:
                    new_set = models.Set(
                        id=s['id'],
                        name=s['name'],
                        series=s.get('series'),
                        card_count=s.get('cardCount', {}).get('total') if isinstance(s.get('cardCount'), dict) else s.get('cardCount'),
                        image_url=f"{s.get('logo')}.png" if s.get('logo') else None
                    )
                    db.add(new_set)
                    new_sets_count += 1
            
            db.commit()
            log.sets_added = new_sets_count
            print(f"Sets synced. Added {new_sets_count} new sets.")

        except Exception as e:
            errors.append(f"Sets sync error: {str(e)}")
            metrics["errors"].append(f"Sets sync error: {str(e)}")
            db.rollback()

        # --- 2. CARDS ---
        try:
            response = await client.get(f"{TCGDEX_API}/cards")
            response.raise_for_status()
            all_cards_summary = response.json()
            
            # Optimization: Fetch all existing Card IDs
            # If DB is huge, fetching check set is safer than 20k individual queries
            existing_card_ids = {c.id for c in db.query(models.Card.id).all()}
            
            # Identify NEW cards
            new_cards_summary = [c for c in all_cards_summary if c['id'] not in existing_card_ids]
            
            if card_limit:
                new_cards_summary = new_cards_summary[:card_limit]
            
            log.cards_processed = len(new_cards_summary)
            metrics["cards_processed"] = len(new_cards_summary)
            print(f"Found {len(new_cards_summary)} new cards to process.")
            
            for card_summary in new_cards_summary:
                if SHOULD_STOP:
                    print("Sync stopping explicitly (Cards loop).")
                    break

                try:
                    # Fetch Full Details
                    detail_res = await client.get(f"{TCGDEX_API}/cards/{card_summary['id']}")
                    detail_res.raise_for_status()
                    details = detail_res.json()
                    
                    if 'id' not in details: continue

                    # Images
                    image_url_low = f"{details.get('image')}/low.png" if details.get('image') else None
                    image_url_high = f"{details.get('image')}/high.png" if details.get('image') else None
                    
                    # Compute pHash (Expensive operation, only for NEW cards)
                    phash = await calculate_phash(image_url_high) if image_url_high else None
                    
                    new_card = models.Card(
                        id=details['id'],
                        name=details['name'],
                        set_id=details['set']['id'],
                        image_url=image_url_low,
                        phash=phash,
                        # updated_at defaults to now
                    )
                    db.add(new_card)
                    log.cards_added += 1
                    metrics["new_cards"] += 1
                    
                    # Initial Price (Optional: can be handled by sync_prices, but nice to have initialized)
                    # We skip it here to strictly separate logic: sync_prices will pick it up because it has no price.
                    
                    db.add(models.ChangeLog(
                        card_id=details['id'],
                        change_type='new_card',
                        new_value=json.dumps({"name": details['name']})
                    ))
                    db.commit()
                    
                except Exception as e:
                    errors.append(f"Card {card_summary['id']} error: {str(e)}")
                    metrics["errors"].append(f"Card {card_summary['id']} error: {str(e)}")
                    db.rollback()
                    
        except Exception as e:
            errors.append(f"Cards list sync error: {str(e)}")
            metrics["errors"].append(f"Cards list sync error: {str(e)}")

    log.status = 'success' if not errors else 'error'
    log.error_details = "\n".join(errors[-50:]) if errors else None # Limit error log size
    log.errors_count = len(errors)
    log.finished_at = datetime.datetime.utcnow()
    db.commit()
    print("Sets/Cards Sync Finished.")
    
    metrics["new_sets"] = new_sets_count
    return metrics

async def sync_prices(db: Session, force_prices: bool = False) -> dict:
    """
    Updates prices for cards based on Temperature/Hashing strategy.
    """
    start_time = datetime.datetime.utcnow()
    log = models.SyncLog(sync_type='prices', started_at=start_time, status='running')
    db.add(log)
    db.commit()
    
    print(f"[{start_time}] Starting Price Sync (Smart Strategy)...")
    errors = []
    
    # Pre-fetch all cards
    all_cards = db.query(models.Card).all()
    
    # Filter cards to check
    # AND gather strat stats
    cards_to_check = []
    strat_stats = {'NEW': 0, 'HOT': 0, 'STABLE': 0, 'STABLE_SAFETY': 0, 'COLD': 0, 'COLD_SAFETY': 0}
    
    for c in all_cards:
        strat = determine_check_strategy(c)
        if strat != 'SKIP':
            cards_to_check.append(c)
            strat_stats[strat] = strat_stats.get(strat, 0) + 1
    
    total_to_check = len(cards_to_check)
    log.cards_processed = total_to_check
    
    print(f"Total Cards: {len(all_cards)}. Scheduled for check: {total_to_check}")
    print(f"Breakdown: HOT={strat_stats['HOT']}, STABLE={strat_stats['STABLE']}, COLD={strat_stats['COLD']}, NEW={strat_stats['NEW']}")
    
    updated_count = 0
    checked_count = 0
    
    # Detailed Stats
    stats = {
        'delta_triggers': 0,
        'variant_updates': {}, # e.g. "normal": 5, "reverseHolofoil": 2
        'errors_by_type': {}
    }
    
    # Silence HTTPX logs to avoid spam
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    async with httpx.AsyncClient() as client:
        for i, card in enumerate(cards_to_check, 1):
            if SHOULD_STOP:
                 print("Sync stopping explicitly (Prices loop).")
                 log.status = 'stopped'
                 break

            checked_count += 1
            
            # Progress Log (every 100 cards or 5%)
            if i % 100 == 0 or i == total_to_check:
                percent = (i / total_to_check) * 100
                print(f"[{datetime.datetime.utcnow().strftime('%H:%M:%S')}] Progress: {i}/{total_to_check} ({percent:.1f}%) - Updates: {updated_count}")

            try:
                response = await client.get(f"{TCGDEX_API}/cards/{card.id}")
                if response.status_code == 404:
                    card.last_price_check_at = datetime.datetime.utcnow()
                    db.commit()
                    continue
                    
                response.try_check_status = response.raise_for_status() # Just to be safe if raise_for_status is needed, but we catch generic exception
                response.raise_for_status()
                details = response.json()
                
                card.last_price_check_at = datetime.datetime.utcnow()
                
                pricing = details.get('pricing')
                if not pricing: 
                    db.commit()
                    continue

                prices_found = {} 
                if 'tcgplayer' in pricing:
                    for variant, p_data in pricing['tcgplayer'].items():
                        if not isinstance(p_data, dict): continue
                        
                        market_p = p_data.get('marketPrice') or p_data.get('midPrice') or 0.0
                        low_p = p_data.get('lowPrice') or 0.0
                        high_p = p_data.get('highPrice') or 0.0
                        
                        if market_p > 0 or low_p > 0 or high_p > 0:
                            prices_found[variant] = {
                                'market': market_p,
                                'low': low_p,
                                'high': high_p
                            }

                card_updated = False
                for variant, p_vals in prices_found.items():
                    changed = await update_card_price(db, card.id, variant, p_vals, log)
                    if changed:
                        card_updated = True
                        stats['variant_updates'][variant] = stats['variant_updates'].get(variant, 0) + 1
                
                if card_updated:
                    card.updated_at = datetime.datetime.utcnow()
                    updated_count += 1
                    stats['delta_triggers'] += 1
                
                db.commit()

            except Exception as e:
                 err_type = type(e).__name__
                 stats['errors_by_type'][err_type] = stats['errors_by_type'].get(err_type, 0) + 1
                 # Keep errors list small for DB log
                 if len(errors) < 50:
                     errors.append(f"{card.id}: {str(e)}")

    log.prices_updated = updated_count
    log.status = 'success'
    log.finished_at = datetime.datetime.utcnow()
    db.commit()
    
    # --- FINAL REPORT ---
    print("\n" + "="*40)
    print(f"✅ PRICE SYNC COMPLETED REPORT")
    print("="*40)
    print(f"Total Checked:      {checked_count}/{total_to_check}")
    print("-" * 20)
    print(f"Strategy Breakdown:")
    for k, v in strat_stats.items():
        if v > 0: print(f"  - {k:<15}: {v}")
    print("-" * 20)
    print(f"Cards Updated:      {updated_count} (Triggered Delta)")
    if stats['variant_updates']:
        print("Updates by Variant Type:")
        for v, count in stats['variant_updates'].items():
            print(f"  - {v:<15}: {count}")
    print("-" * 20)
    if stats['errors_by_type']:
        print("Errors Encountered:")
        for err, count in stats['errors_by_type'].items():
            print(f"  - {err:<15}: {count}")
    else:
        print("No errors encountered.")
    print("="*40 + "\n")
    
    return {
        "total_cards": len(all_cards),
        "checked_count": checked_count,
        "scheduled_for_check": total_to_check,
        "updated_count": updated_count,
        "strategy_breakdown": strat_stats,
        "variant_updates": stats['variant_updates'],
        "errors_by_type": stats['errors_by_type'],
        "error_list": errors
    }

async def update_card_price(db: Session, card_id: str, variant: str, vals: dict, log: models.SyncLog) -> bool:
    """
    Returns True if ANY price (Market, Low, High) changed significantly (> 0.01).
    This ensures the App Delta Sync picks up these changes.
    """
    market = vals['market']
    low = vals['low']
    high = vals['high']

    # Check existing
    current = db.query(models.CardPrice).filter(
        models.CardPrice.card_id == card_id,
        models.CardPrice.price_type == variant
    ).first()
    
    any_changed = False
    
    if not current:
        # New Price Record
        new_p = models.CardPrice(
            card_id=card_id, 
            price_type=variant, 
            value=market,
            low=low,
            high=high
        )
        db.add(new_p)
        any_changed = True
        
        # Insert History for new record
        hist = models.CardPriceHistory(
            card_id=card_id, 
            price_type=variant, 
            value=market,
            timestamp=datetime.datetime.utcnow()
        )
        db.add(hist)
    else:
        # Detect Changes
        market_diff = abs(current.value - market) > 0.01
        low_diff = abs((current.low or 0) - low) > 0.01
        high_diff = abs((current.high or 0) - high) > 0.01
        
        if market_diff or low_diff or high_diff:
            any_changed = True
            
            # Log significant changes if market changed (historically we tracked market)
            if market_diff:
                db.add(models.ChangeLog(card_id=card_id, change_type='price', old_value=str(current.value), new_value=str(market)))
            
            # Update Current Values
            current.value = market
            current.low = low
            current.high = high
            
            # History Logic (Driven by Market Price mainly, but we insert if market changed)
            if market_diff:
                 hist = models.CardPriceHistory(
                    card_id=card_id, 
                    price_type=variant, 
                    value=market,
                    timestamp=datetime.datetime.utcnow()
                )
                 db.add(hist)
                 maintain_price_history(db, card_id, variant)
            else:
                # STABLE MARKET PRICE LOGIC (Flat Line Optimization)
                # Even if Low/High changed, if Market is flat, we optimize the history graph.
                # See previous logic: extend 'tail' record.
                
                last_history = db.query(models.CardPriceHistory).filter(
                    models.CardPriceHistory.card_id == card_id,
                    models.CardPriceHistory.price_type == variant
                ).order_by(models.CardPriceHistory.timestamp.desc()).limit(2).all()
                
                should_insert = False
                should_update_tail = False
                
                if not last_history:
                     should_insert = True
                elif abs(last_history[0].value - market) > 0.01:
                     should_insert = True
                else:
                     if len(last_history) > 1 and abs(last_history[1].value - market) < 0.01:
                         should_update_tail = True
                     else:
                         should_insert = True
        
                if should_insert:
                     # Only insert if we really need to (e.g. gap filling), but here likely checking if we should add a node
                     # because Low changed? No, History tracks Market Value.
                     # If Market is stable, we just extend tail.
                     hist = models.CardPriceHistory(
                        card_id=card_id, 
                        price_type=variant, 
                        value=market,
                        timestamp=datetime.datetime.utcnow()
                    )
                     db.add(hist)
                elif should_update_tail:
                     last_history[0].timestamp = datetime.datetime.utcnow()

    return any_changed
