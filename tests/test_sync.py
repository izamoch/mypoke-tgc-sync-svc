import datetime
import hashlib
import pytest
from sync import determine_check_strategy
from models import Card


def test_new_card():
    """Card never checked -> NEW regardless of price tier"""
    card = Card(id="test-new", last_price_check_at=None)
    assert determine_check_strategy(card, max_market_price=50.0) == "NEW"
    assert determine_check_strategy(card, max_market_price=5.0) == "NEW"
    assert determine_check_strategy(card, max_market_price=0.0) == "NEW"


def test_premium_daily_check():
    """Premium card (>= $20) checked > 20h ago should be PREMIUM"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-premium",
        last_price_check_at=now - datetime.timedelta(hours=22),
    )
    assert determine_check_strategy(card, max_market_price=25.0) == "PREMIUM"


def test_premium_skip_recent():
    """Premium card checked < 20h ago should SKIP"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-premium-skip",
        last_price_check_at=now - datetime.timedelta(hours=5),
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
                last_price_check_at=now - datetime.timedelta(days=2),
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
                last_price_check_at=now - datetime.timedelta(days=2),
            )
            assert determine_check_strategy(card, max_market_price=5.0) == "SKIP"
            return
    pytest.fail("Could not find a card ID not matching today's hash slot")


def test_standard_safety():
    """Standard card not checked in > 8 days should trigger STANDARD_SAFETY"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-standard-safety",
        last_price_check_at=now - datetime.timedelta(days=10),
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
                last_price_check_at=now - datetime.timedelta(days=5),
            )
            assert determine_check_strategy(card, max_market_price=0.0) == "NO_PRICE"
            return
    pytest.fail("Could not find a card ID matching today's hash slot for NO_PRICE")


def test_no_price_safety():
    """Card with no price not checked in > 20 days -> NO_PRICE_SAFETY"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-noprice-safety",
        last_price_check_at=now - datetime.timedelta(days=25),
    )
    assert determine_check_strategy(card, max_market_price=0.0) == "NO_PRICE_SAFETY"


def test_premium_boundary():
    """Card at exactly $20 should be PREMIUM, at $19.99 should be STANDARD/SKIP"""
    now = datetime.datetime.utcnow()
    card = Card(
        id="test-boundary",
        last_price_check_at=now - datetime.timedelta(hours=22),
    )
    assert determine_check_strategy(card, max_market_price=20.0) == "PREMIUM"
    # $19.99 -> STANDARD tier, result depends on hash match
    result = determine_check_strategy(card, max_market_price=19.99)
    assert result in ("STANDARD", "SKIP")
