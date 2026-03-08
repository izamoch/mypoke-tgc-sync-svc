from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
import datetime

Base = declarative_base()


class Set(Base):
    __tablename__ = "sets"

    id = Column(String, primary_key=True)
    name = Column(String, index=True)
    series = Column(String, index=True)
    card_count = Column(Integer)
    image_url = Column(String)
    release_date = Column(String)  # TCGDex stores it as "YYYY/MM/DD" or "YYYY"
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

    cards = relationship("Card", back_populates="set")


class Card(Base):
    __tablename__ = "cards"

    id = Column(String, primary_key=True)
    name = Column(String, index=True)
    set_id = Column(String, ForeignKey("sets.id"), index=True)
    image_url = Column(String)
    phash = Column(String, index=True)
    
    # Expanded Metadata
    dex_id = Column(Integer, index=True)
    rarity = Column(String, index=True)
    category = Column(String, index=True)
    illustrator = Column(String, index=True)
    hp = Column(Integer)
    types = Column(String)  # JSON-encoded list: ["Grass", "Fire"]
    stage = Column(String)
    suffix = Column(String)
    attacks = Column(String)    # JSON-encoded list of dicts
    weaknesses = Column(String) # JSON-encoded list of dicts
    retreat = Column(Integer)
    regulation_mark = Column(String)
    legal = Column(String)      # JSON-encoded dict: {"standard": true, "expanded": true}

    # Lore (PokéAPI)
    flavor_text = Column(String)
    evolutions = Column(String)  # JSON-encoded list of family members

    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_price_check_at = Column(DateTime, default=None)

    set = relationship("Set", back_populates="cards")
    prices = relationship("CardPrice", back_populates="card")


class CardPrice(Base):
    __tablename__ = "card_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    card_id = Column(String, ForeignKey("cards.id"), index=True)
    price_type = Column(String, index=True)  # Variant: 'normal', 'holofoil', etc.
    
    # Granular Prices (Alinged with TCGDex)
    market = Column(Float)  # TCGPlayer marketPrice
    low = Column(Float)     # TCGPlayer lowPrice / Cardmarket low
    mid = Column(Float)     # TCGPlayer midPrice
    high = Column(Float)    # TCGPlayer highPrice
    direct = Column(Float)  # TCGPlayer directLowPrice
    avg = Column(Float)     # Cardmarket avg
    trend = Column(Float)   # Cardmarket trend
    
    # Cardmarket specific temporal trends
    trend_1d = Column(Float)
    trend_7d = Column(Float)
    trend_30d = Column(Float)

    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    card = relationship("Card", back_populates="prices")




class ChangeLog(Base):
    __tablename__ = "change_log"

    version_id = Column(Integer, primary_key=True, autoincrement=True)
    card_id = Column(String)
    change_type = Column(String)
    old_value = Column(String)
    new_value = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String)  # 'prices' or 'cards'
    status = Column(String)  # 'success' or 'error'
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    finished_at = Column(DateTime)
    cards_processed = Column(Integer, default=0)
    cards_added = Column(Integer, default=0)
    sets_added = Column(Integer, default=0)
    prices_updated = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    error_details = Column(String)
