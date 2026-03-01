import datetime
import pytest
from sync import determine_check_strategy
from models import Card

def test_determine_check_strategy_new_card():
    # Card has never been checked
    card = Card(id="test-1", last_price_check_at=None)
    assert determine_check_strategy(card) == "NEW"

def test_determine_check_strategy_hot_card():
    now = datetime.datetime.utcnow()
    # Updated yesterday (HOT area), checked 2 days ago (needs check)
    card = Card(
        id="test-2", 
        updated_at=now - datetime.timedelta(days=1),
        last_price_check_at=now - datetime.timedelta(days=2)
    )
    assert determine_check_strategy(card) == "HOT"

def test_determine_check_strategy_hot_skip():
    now = datetime.datetime.utcnow()
    # Updated yesterday, checked today (skip)
    card = Card(
        id="test-3", 
        updated_at=now - datetime.timedelta(days=1),
        last_price_check_at=now - datetime.timedelta(hours=5)
    )
    assert determine_check_strategy(card) == "SKIP"

def test_determine_check_strategy_cold_safety():
    now = datetime.datetime.utcnow()
    # Old card, but hasn't been checked in 50 days (triggers safety check)
    card = Card(
        id="test-4", 
        updated_at=now - datetime.timedelta(days=300),
        last_price_check_at=now - datetime.timedelta(days=50)
    )
    assert determine_check_strategy(card) == "COLD_SAFETY"
