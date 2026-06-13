import datetime
import hashlib

import pytest

from mypoke_sync.sync import (
    Card,
    _extract_prices_found,
    _parse_dt,
    determine_check_strategy,
    update_card_price,
)


def test_new_card():
    """Card never checked -> NEW regardless of price tier"""
    card = Card(id="test-new", updated_at=None)
    assert determine_check_strategy(card, max_market_price=50.0) == "NEW"
    assert determine_check_strategy(card, max_market_price=5.0) == "NEW"
    assert determine_check_strategy(card, max_market_price=0.0) == "NEW"


def test_premium_daily_check():
    """Premium card (>= $20) checked > 20h ago should be PREMIUM"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-premium",
        updated_at=now - datetime.timedelta(hours=22),
    )
    assert determine_check_strategy(card, max_market_price=25.0) == "PREMIUM"


def test_premium_skip_recent():
    """Premium card checked < 20h ago should SKIP"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-premium-skip",
        updated_at=now - datetime.timedelta(hours=5),
    )
    assert determine_check_strategy(card, max_market_price=100.0) == "SKIP"


def test_standard_hash_hit():
    """Standard card ($0-$20) on its hash day should be STANDARD"""
    now = datetime.datetime.utcnow()
    day_of_year = now.timetuple().tm_yday

    # Find a card ID whose hash % 5 matches today
    for i in range(100):
        cid = f"standard-{i}"
        card_hash = int(hashlib.sha256(cid.encode("utf-8")).hexdigest(), 16)
        if (card_hash % 5) == (day_of_year % 5):
            card = Card(
                id=cid,
                updated_at=now - datetime.timedelta(days=5),
            )
            assert determine_check_strategy(card, max_market_price=5.0) == "STANDARD"
            return
    pytest.fail("Could not find a card ID matching today's hash slot")


def test_standard_hash_miss():
    """Standard card not on its hash day should SKIP"""
    now = datetime.datetime.utcnow()
    day_of_year = now.timetuple().tm_yday

    # Find a card ID whose hash % 5 does NOT match today
    for i in range(100):
        cid = f"standard-miss-{i}"
        card_hash = int(hashlib.sha256(cid.encode("utf-8")).hexdigest(), 16)
        if (card_hash % 5) != (day_of_year % 5):
            card = Card(
                id=cid,
                updated_at=now - datetime.timedelta(days=5),
            )
            assert determine_check_strategy(card, max_market_price=5.0) == "SKIP"
            return
    pytest.fail("Could not find a card ID not matching today's hash slot")


def test_standard_safety():
    """Standard card not checked in > 8 days should trigger STANDARD_SAFETY"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-standard-safety",
        updated_at=now - datetime.timedelta(days=10),
    )
    assert determine_check_strategy(card, max_market_price=8.0) == "STANDARD_SAFETY"


def test_no_price_hash_hit():
    """Card with no price on its hash day should be NO_PRICE"""
    now = datetime.datetime.utcnow()
    day_of_year = now.timetuple().tm_yday

    for i in range(200):
        cid = f"noprice-{i}"
        card_hash = int(hashlib.sha256(cid.encode("utf-8")).hexdigest(), 16)
        if (card_hash % 15) == (day_of_year % 15):
            card = Card(
                id=cid,
                updated_at=now - datetime.timedelta(days=15),
            )
            assert determine_check_strategy(card, max_market_price=0.0) == "NO_PRICE"
            return
    pytest.fail("Could not find a card ID matching today's hash slot for NO_PRICE")


def test_no_price_safety():
    """Card with no price not checked in > 20 days -> NO_PRICE_SAFETY"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-noprice-safety",
        updated_at=now - datetime.timedelta(days=25),
    )
    assert determine_check_strategy(card, max_market_price=0.0) == "NO_PRICE_SAFETY"


def test_premium_boundary():
    """Card at exactly $20 should be PREMIUM, at $19.99 should be STANDARD/SKIP"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-boundary",
        updated_at=now - datetime.timedelta(hours=22),
    )
    assert determine_check_strategy(card, max_market_price=20.0) == "PREMIUM"
    # $19.99 -> STANDARD tier, result depends on hash match
    result = determine_check_strategy(card, max_market_price=19.99)
    assert result in ("STANDARD", "SKIP")


def test_validators():
    """Verify validator helper functions reject bad data and accept good data"""
    from mypoke_sync.validator import validate_card_data, validate_price_data, validate_set_data

    # 1. Set validation
    assert validate_set_data({"id": "swsh1", "name": "Sword & Shield"}) is True
    assert validate_set_data({"id": "swsh1"}) is False
    assert validate_set_data({"name": "Sword & Shield"}) is False

    # 2. Card validation
    assert validate_card_data({"id": "swsh1-1", "name": "Celebi V", "set_id": "swsh1", "dex_id": 251}) is True
    assert validate_card_data({"id": "swsh1-1", "name": "Celebi V"}) is False
    assert validate_card_data({"id": "swsh1-1", "name": "Celebi V", "set_id": "swsh1", "dex_id": "not-an-int"}) is False

    # 3. Price validation
    assert validate_price_data({"card_id": "swsh1-1", "market": 1.5, "low": 1.0}) is True
    assert validate_price_data({"market": 1.5}) is False
    assert validate_price_data({"card_id": "swsh1-1", "market": "not-a-number"}) is False


def test_parse_dt():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("2026-06-08T10:23:45.123456") == datetime.datetime(2026, 6, 8, 10, 23, 45, 123456)


def test_update_card_price_new_record():
    """A variant with no existing row is always a significant change."""
    changed, significant, row = update_card_price(None, "swsh1-1", "normal", {"market": 5.0, "low": 4.0})

    assert changed is True
    assert significant is True
    assert row["card_id"] == "swsh1-1"
    assert row["price_type"] == "normal"
    assert row["market"] == 5.0
    assert row["low"] == 4.0
    assert row["trend_1d"] == 0.0


def test_update_card_price_negligible_diff_is_unchanged():
    current = {"market": 5.00}
    changed, significant, _ = update_card_price(current, "swsh1-1", "normal", {"market": 5.005})

    assert changed is False
    assert significant is False


def test_update_card_price_small_change_not_significant():
    current = {"market": 5.00}
    # ~2% change, well under $0.50 -> changed but not significant
    changed, significant, _ = update_card_price(current, "swsh1-1", "normal", {"market": 5.10})

    assert changed is True
    assert significant is False


def test_update_card_price_large_percent_change_is_significant():
    current = {"market": 5.00}
    # +20% -> significant
    changed, significant, _ = update_card_price(current, "swsh1-1", "normal", {"market": 6.00})

    assert changed is True
    assert significant is True


def test_update_card_price_large_flat_change_is_significant():
    current = {"market": 0.0}
    # From $0 to $0.60 -> flat change >= $0.50 -> significant
    changed, significant, _ = update_card_price(current, "swsh1-1", "normal", {"market": 0.60})

    assert changed is True
    assert significant is True


def test_extract_prices_tcgplayer_only():
    pricing = {
        "tcgplayer": {
            "normal": {"marketPrice": 1.5, "lowPrice": 1.0, "midPrice": 1.2, "highPrice": 2.0, "directLowPrice": 1.1}
        }
    }
    prices = _extract_prices_found(pricing)

    assert set(prices.keys()) == {"normal"}
    assert prices["normal"]["market"] == 1.5
    assert prices["normal"]["avg"] == 0.0


def test_extract_prices_flat_cardmarket_synthesizes_normal_variant():
    """When TCGPlayer is null, a flat Cardmarket object should produce a 'normal' variant."""
    pricing = {
        "tcgplayer": None,
        "cardmarket": {"avg": 2.5, "low": 1.0, "trend": 2.6, "avg1": 2.4, "avg7": 2.3, "avg30": 2.2},
    }
    prices = _extract_prices_found(pricing)

    assert set(prices.keys()) == {"normal"}
    assert prices["normal"]["avg"] == 2.5
    assert prices["normal"]["low"] == 1.0
    assert prices["normal"]["trend_30d"] == 2.2


def test_extract_prices_nested_cardmarket_merges_into_existing_variant():
    pricing = {
        "tcgplayer": {"holofoil": {"marketPrice": 10.0, "lowPrice": 8.0, "midPrice": 9.0, "highPrice": 12.0}},
        "cardmarket": {"holofoil": {"avg": 9.5, "low": 7.5, "trend": 9.6, "avg1": 9.4, "avg7": 9.3, "avg30": 9.2}},
    }
    prices = _extract_prices_found(pricing)

    assert set(prices.keys()) == {"holofoil"}
    assert prices["holofoil"]["market"] == 10.0
    assert prices["holofoil"]["avg"] == 9.5
    # TCGPlayer low (8.0) takes precedence since it's non-zero
    assert prices["holofoil"]["low"] == 8.0
