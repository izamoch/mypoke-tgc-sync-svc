# Pokémon TCG Data Sync (v2 - Premium Data)

Incremental synchronization engine for Pokémon TCG data, aligned with TCGDex API and enriched with PokéAPI lore and evolution chains.

## 🚀 Key Features
- **Granular Pricing**: Full support for TCGPlayer (`market`, `low`, `mid`, `high`, `direct`) and Cardmarket (`avg`, `trend`, and 1d/7d/30d temporal averages).
- **Lore Enrichment**: Automatic backfill of Pokédex flavor text and evolution chains using PokéAPI.
- **Smart Sync Strategy**: Hybrid value-tier + hash rotation — Premium cards (≥$20) checked daily, Standard ($0-$20) via `hash % 5` (~every 5 days), and cards without price data via `hash % 15` (~every 15 days). Safety nets ensure no card goes unchecked indefinitely.
- **Direct Cloudflare D1 Sync**: D1 is the single source of truth. The service reads and writes directly to D1 via its REST API (`d1_client.py`) — no local database, no proxy Worker in the write path. Reads are scoped to "just what's needed" (existing IDs, Smart Sync candidates); writes use chunked `INSERT ... ON CONFLICT DO UPDATE` / `UPDATE` statements respecting D1's 100-bound-parameters-per-statement limit.
- **Auto-Reporting**: Local and webhook-based markdown/HTML reports after every sync run, including any D1 write errors.

## 📊 Database Schema (v3)

All tables include an `updated_at` column which serves as the unified source of truth for all activity (last checked or last modified). Dedicated logging tables have been eliminated to optimize database performance.

### Table: `sets`
Stores information about card expansions.
- `id`: Unique set identifier.
- `name`: Human-readable name.
- `series`: Collection series.
- `updated_at`: Last modification or sync check.

### Table: `cards`
Stores core metadata and enriched lore.
- `flavor_text`: Description from Pokédex.
- `evolutions`: Family tree (JSON).
- `dex_id`: National Dex ID for species grouping.
- `updated_at`: Unified timestamp for metadata changes or last price check.

### Table: `card_prices` (1:N Relationship)
Stores multiple price variants per card.
- `market`, `low`, `mid`, `high`, `direct` (TCGPlayer)
- `avg`, `trend` (Cardmarket)
- `trend_1d`, `trend_7d`, `trend_30d` (Temporal averages)
- `updated_at`: Last price modification.

## 🛠 Quality & Standards
- **Linter**: [Ruff](https://github.com/astral-sh/ruff) for fast, PEP 8 compliant linting.
- **Type Checking**: [Mypy](http://mypy-lang.org/) for static type safety.
- **Testing**: [Pytest](https://pytest.org/) with `pytest-asyncio` and `pytest-cov`.
- **Pre-commit**: Automated quality gates for every commit.

### Test Coverage
Current core logic coverage:
- `sync.py` (strategy): Verified 100% for `determine_check_strategy`
- `d1_client.py`: Unit tested against a mocked Cloudflare D1 REST API (`httpx.MockTransport`).

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Yes | Cloudflare account ID (Dashboard → Workers & Pages → Overview). |
| `CLOUDFLARE_API_TOKEN` | Yes | API token with `D1:Edit` permission, scoped to the database below. |
| `D1_DATABASE_ID` | Yes | Cloudflare D1 database ID (`wrangler d1 list`). |
| `REPORT_WEBHOOK_URL` | No | If set, sends a JSON sync report (markdown + HTML) to this URL after each run. |

If `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`/`D1_DATABASE_ID` are not set, the job aborts immediately — D1 is the only source of state, so there is no degraded "skip" mode.

## 🏁 Getting Started

1. **Environment**:
   ```bash
   cp .env.example .env
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Run Sync**:
   The logic is now organized in the `src/` directory. You can run the sync job as a module:
   ```bash
   export PYTHONPATH=$PYTHONPATH:$(pwd)/src
   python -m mypoke_sync.main --force-prices
   ```
   Or, if you installed the package in editable mode (`pip install -e .`):
   ```bash
   mypoke-sync --force-prices
   ```

3. **Project Structure**:
   - `src/mypoke_sync/`: Core package containing the D1 client, sync logic, and PokéAPI/TCGDex clients.
   - `tests/`: Automated test suite.
   - `reports/`: Markdown/HTML sync reports generated after each run.
