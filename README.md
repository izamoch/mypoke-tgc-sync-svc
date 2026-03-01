# PokeTCG Sync Backend

This repository contains the backend synchronization service for fetching and updating Pokémon TCG (Trading Card Game) data (Sets, Cards, and Prices) from the [TCGDex API](https://tcgdex.net/).

## 📌 Architecture

The system is built using modern Python with an asynchronous architecture:
- **Language**: Python 3.11
- **Database**: PostgreSQL (for production/Cloud Run) or SQLite (for local development).
- **ORM**: SQLAlchemy.
- **HTTP Client**: `httpx` (Asynchronous HTTP requests).
- **Image Hashing**: `imagehash` and `Pillow` (to compute perceptual hashes of card images).

### Core Components
- `models.py`: Defines the database schema (`Set`, `Card`, `CardPrice`, `CardPriceHistory`, `ChangeLog`, `SyncLog`).
- `database.py`: Handles the database connection and session management.
- `sync.py`: Contains the core asynchronous logic for fetching sets, fetching cards, and updating market prices.
- `sync_job.py`: The main entry point script that orchestrates the synchronization phases sequentially.
- `utils/phash.py`: Utility to generate pHash from card images for visual comparisons.

## 🔄 Synchronization Process

The synchronization job runs in two main phases:

1. **Sets and Cards Sync (Incremental)**
   - Fetches all Sets and inserts only the **New** ones.
   - Fetches all Cards, compares against the database, and processes only the **New** cards.
   - Computes a perceptual hash (`pHash`) for each new card's high-resolution image.

2. **Price Sync (Smart Temperature Strategy)**
   - To avoid unnecessary API calls and optimize performance, the system uses a smart checking strategy:
     - **NEW**: Cards that have never been checked are checked immediately.
     - **HOT**: Cards updated within the last 7 days are checked daily.
     - **STABLE**: Cards updated within the last 30 days are checked once a week.
     - **COLD**: Older cards are checked once a month.
   - Only significant price changes (> 0.01) are recorded, keeping the `CardPriceHistory` database table optimized.

### 3. Automated Sync Reports
At the end of every synchronization run, the system automatically generates a detailed Markdown report inside the `reports/` directory. These reports include:
- Total execution duration and timestamps.
- Counts of new Sets and Cards correctly added to the database.
- Granular breakdown of the Temperature Strategy triggers (HOT, STABLE, COLD).
- A concise summary of API errors (if any) to aid in debugging without needing to sift through console logs.

## 🛠 Code Quality & Testing

This project strictly enforces modern Python code quality standards for maintainability and reliability:
- **Ruff**: Ultra-fast linter and formatter used to enforce consistent code style and catch anti-patterns.
- **Mypy**: Statically guarantees type safety across the project to catch type-mismatch bugs.
- **Pytest**: Used alongside `pytest-asyncio` to execute localized business logic unit tests (e.g., algorithmic verification of the Temperature Strategy).
- **Pre-commit**: Local git hooks are configured via `.pre-commit-config.yaml` to seamlessly automate Ruff and Mypy checks on every commit.

To set up the local development environment:
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

## 🐳 Deployment with Docker

The service is fully containerized and can be easily deployed using Docker.

### 1. Build the Docker Image
Navigate to the repository root and build the image:
```bash
docker build -t mypoke_tgc_sync .
```

### 2. Configure Environment Variables
Create a `.env` file or pass the `DATABASE_URL` directly.
For local SQLite:
```env
DATABASE_URL=sqlite:///./data/poke_tgc.sqlite
```
For PostgreSQL:
```env
DATABASE_URL=postgresql://user:password@host:port/dbname
```

### 3. Run the Container
Run the container, making sure to mount a volume if you are using SQLite locally to persist data:
```bash
docker run -d \
  --name poke_sync_job \
  -e DATABASE_URL="sqlite:///./data/poke_tgc.sqlite" \
  -v $(pwd)/data:/app/data \
  mypoke_tgc_sync
```

*(Note: The container is configured to run `python sync_job.py` by default. It executes the sync job and then exits. For periodic execution, you should schedule this container using Cron, Docker Compose with a scheduler, or a cloud scheduler like Google Cloud Run Jobs.)*
