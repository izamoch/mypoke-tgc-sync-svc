import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

load_dotenv()

# Local SQLite database used as the sync-state store (existing sets/cards,
# last-checked timestamps, price history for the smart-sync strategy).
# Production data lives in Cloudflare D1 and is pushed via the Worker HTTP
# API - see d1_client.py.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/poke_tgc.sqlite")

# Ensure the target directory exists for file-based SQLite URLs.
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.removeprefix("sqlite:///")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Ensure the local schema exists (no-op if tables are already present).
Base.metadata.create_all(bind=engine)
