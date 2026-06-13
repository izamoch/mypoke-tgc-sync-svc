import httpx

from mypoke_sync import sync


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(sync.httpx, "AsyncClient", factory)


async def _fake_phash(*args, **kwargs):
    return "fakephash"


async def _fake_pokeapi(*args, **kwargs):
    return "Flavor text", ["Evolution"]


async def test_sync_sets_and_cards_inserts_new_set_and_card(monkeypatch):
    monkeypatch.setattr(sync, "calculate_phash", _fake_phash)
    monkeypatch.setattr(sync, "fetch_pokeapi_data", _fake_pokeapi)
    sync.start_sync_flag()

    sets_summary = [
        {
            "id": "base1",
            "name": "Base Set",
            "series": "Base",
            "cardCount": {"total": 102},
            "logo": "https://images/base1/logo",
            "releaseDate": "1999/01/09",
        }
    ]
    cards_summary = [{"id": "base1-1"}]
    card_detail = {
        "id": "base1-1",
        "name": "Alakazam",
        "set": {"id": "base1"},
        "image": "https://images/base1-1",
        "dexId": [65],
        "rarity": "Rare Holo",
        "category": "Pokemon",
        "hp": 80,
        "types": ["Psychic"],
    }

    def handler(request):
        path = request.url.path
        if path.endswith("/sets"):
            return httpx.Response(200, json=sets_summary)
        if path.endswith("/cards/base1-1"):
            return httpx.Response(200, json=card_detail)
        if path.endswith("/cards"):
            return httpx.Response(200, json=cards_summary)
        raise AssertionError(f"unexpected request: {request.url}")

    _install_transport(monkeypatch, handler)

    async def fake_d1_query(sql, params=None):
        if "FROM sets" in sql or "FROM cards" in sql:
            return []
        raise AssertionError(f"unexpected query: {sql}")

    upserts = []

    async def fake_chunked_upsert(table, columns, conflict_cols, rows, **kwargs):
        upserts.append((table, columns, conflict_cols, rows))
        return {"rows_written": len(rows), "errors": []}

    monkeypatch.setattr(sync.d1_client, "d1_query", fake_d1_query)
    monkeypatch.setattr(sync.d1_client, "chunked_upsert", fake_chunked_upsert)

    metrics = await sync.sync_sets_and_cards()

    assert metrics["new_sets"] == 1
    assert metrics["new_cards"] == 1
    assert metrics["errors"] == []
    assert metrics["d1_errors"] == []

    sets_upsert = next(u for u in upserts if u[0] == "sets")
    assert sets_upsert[2] == ["id"]
    assert sets_upsert[3][0]["id"] == "base1"
    assert sets_upsert[3][0]["card_count"] == 102
    assert sets_upsert[3][0]["image_url"] == "https://images/base1/logo.png"

    cards_upsert = next(u for u in upserts if u[0] == "cards")
    assert cards_upsert[2] == ["id"]
    card_row = cards_upsert[3][0]
    assert card_row["id"] == "base1-1"
    assert card_row["set_id"] == "base1"
    assert card_row["updated_at"] is None
    assert card_row["dex_id"] == 65
    assert card_row["flavor_text"] == "Flavor text"
    assert card_row["phash"] == "fakephash"


async def test_sync_prices_updates_card_and_inserts_price(monkeypatch):
    monkeypatch.setattr(sync, "fetch_pokeapi_data", _fake_pokeapi)
    sync.start_sync_flag()

    card_detail = {
        "id": "base1-1",
        "dexId": [65],
        "pricing": {
            "tcgplayer": {
                "normal": {
                    "marketPrice": 5.0,
                    "lowPrice": 4.0,
                    "midPrice": 4.5,
                    "highPrice": 6.0,
                    "directLowPrice": 4.2,
                }
            }
        },
    }

    def handler(request):
        if request.url.path.endswith("/cards/base1-1"):
            return httpx.Response(200, json=card_detail)
        raise AssertionError(f"unexpected request: {request.url}")

    _install_transport(monkeypatch, handler)

    async def fake_d1_query(sql, params=None):
        if "max_market" in sql:
            # Card never checked -> NEW, always scheduled regardless of force_prices
            return [{"id": "base1-1", "updated_at": None, "max_market": 0.0}]
        if "FROM cards WHERE id IN" in sql:
            return [{"id": "base1-1", **dict.fromkeys(sync.CARD_BACKFILL_COLUMNS)}]
        if "FROM card_prices WHERE card_id IN" in sql:
            return []
        raise AssertionError(f"unexpected query: {sql}")

    updates = []
    upserts = []

    async def fake_chunked_update(table, set_columns, where_column, rows, **kwargs):
        updates.append((table, set_columns, where_column, rows))
        return {"rows_written": len(rows), "errors": []}

    async def fake_chunked_upsert(table, columns, conflict_cols, rows, **kwargs):
        upserts.append((table, columns, conflict_cols, rows))
        return {"rows_written": len(rows), "errors": []}

    monkeypatch.setattr(sync.d1_client, "d1_query", fake_d1_query)
    monkeypatch.setattr(sync.d1_client, "chunked_update", fake_chunked_update)
    monkeypatch.setattr(sync.d1_client, "chunked_upsert", fake_chunked_upsert)

    result = await sync.sync_prices()

    assert result["total_cards"] == 1
    assert result["scheduled_for_check"] == 1
    assert result["checked_count"] == 1
    assert result["updated_count"] == 1
    assert result["strategy_breakdown"]["NEW"] == 1
    assert result["error_list"] == []
    assert result["d1_errors"] == []

    cards_update = updates[0]
    assert cards_update[0] == "cards"
    assert cards_update[2] == "id"
    updated_row = cards_update[3][0]
    assert updated_row["id"] == "base1-1"
    assert updated_row["dex_id"] == 65  # backfilled from details
    assert updated_row["flavor_text"] == "Flavor text"

    prices_upsert = upserts[0]
    assert prices_upsert[0] == "card_prices"
    assert prices_upsert[2] == ["card_id", "price_type"]
    price_row = prices_upsert[3][0]
    assert price_row["card_id"] == "base1-1"
    assert price_row["price_type"] == "normal"
    assert price_row["market"] == 5.0
