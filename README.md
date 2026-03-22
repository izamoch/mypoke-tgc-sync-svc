# Pokémon TCG Data Sync (v2 - Premium Data)

Incremental synchronization engine for Pokémon TCG data, aligned with TCGDex API and enriched with PokéAPI lore and evolution chains.

## 🚀 Key Features
- **Granular Pricing**: Full support for TCGPlayer (`market`, `low`, `mid`, `high`, `direct`) and Cardmarket (`avg`, `trend`, and 1d/7d/30d temporal averages).
- **Lore Enrichment**: Automatic backfill of Pokédex flavor text and evolution chains using PokéAPI.
- **Smart Sync Strategy**: Hybrid value-tier + hash rotation — Premium cards (≥$20) checked daily, Standard ($0-$20) via `hash % 5` (~every 5 days), and cards without price data via `hash % 15` (~every 15 days). Safety nets ensure no card goes unchecked indefinitely.
- **Supabase Integration**: Native support for PostgreSQL/Supabase with optimized indexes for mobile search.
- **Auto-Reporting**: Local and webhook-based markdown reports after every sync run.

## 📊 Database Schema (v2)

### Table: `cards`
Stores core metadata and enriched lore.
- `flavor_text`: Description from Pokédex.
- `evolutions`: Family tree (JSON).
- `dex_id`: National Dex ID for species grouping.

### Table: `card_prices` (1:N Relationship)
Stores multiple price variants per card.
- `market`, `low`, `mid`, `high`, `direct` (TCGPlayer)
- `avg`, `trend` (Cardmarket)
- `trend_1d`, `trend_7d`, `trend_30d` (Temporal averages)

## 🛠 Quality & Standards
- **Linter**: [Ruff](https://github.com/astral-sh/ruff) for fast, PEP 8 compliant linting.
- **Type Checking**: [Mypy](http://mypy-lang.org/) for static type safety.
- **Testing**: [Pytest](https://pytest.org/) with `pytest-asyncio` and `pytest-cov`.
- **Pre-commit**: Automated quality gates for every commit.

### Test Coverage
Current core logic coverage:
- `models.py`: 100%
- `sync.py` (strategy): Verified 100% for `determine_check_strategy`

## 🏁 Getting Started

1. **Environment**:
   ```bash
   cp .env.example .env
   pip install -r requirements.txt
   ```

2. **Run Sync**:
   ```bash
   python sync_job.py --force-prices  # Run full database enrichment
   ```

3. **Handover**:
   See `backend_handover.md` for the definitive SQL DDL and Backend Agent prompt.
