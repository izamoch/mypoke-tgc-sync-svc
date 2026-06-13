import json

import httpx
import pytest

from mypoke_sync import d1_client


async def _no_sleep(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    # Avoid real backoff delays during retry tests
    monkeypatch.setattr(d1_client.asyncio, "sleep", _no_sleep)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("D1_DATABASE_ID", "test-db")


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(d1_client.httpx, "AsyncClient", factory)


def _meta_response(metas):
    return httpx.Response(
        200,
        json={
            "success": True,
            "errors": [],
            "result": [{"results": {"columns": [], "rows": []}, "success": True, "meta": m} for m in metas],
        },
    )


async def test_not_configured_raises(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

    with pytest.raises(d1_client.D1Error):
        await d1_client.d1_query("SELECT 1")

    with pytest.raises(d1_client.D1Error):
        await d1_client.d1_raw_batch([{"sql": "SELECT 1", "params": []}])


async def test_d1_query_sends_auth_header_and_returns_results(monkeypatch):
    def handler(request):
        assert request.url.path.endswith("/query")
        assert request.headers["Authorization"] == "Bearer test-token"
        body = json.loads(request.content)
        assert body == {"sql": "SELECT id FROM sets", "params": ["base1"]}
        return httpx.Response(
            200,
            json={
                "success": True,
                "errors": [],
                "result": [{"results": [{"id": "base1"}, {"id": "base2"}], "success": True}],
            },
        )

    _install_transport(monkeypatch, handler)

    rows = await d1_client.d1_query("SELECT id FROM sets", ["base1"])
    assert rows == [{"id": "base1"}, {"id": "base2"}]


async def test_d1_query_raises_on_errors_array(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={"success": False, "errors": [{"code": 7500, "message": "too many SQL variables"}], "result": []},
        )

    _install_transport(monkeypatch, handler)

    with pytest.raises(d1_client.D1Error):
        await d1_client.d1_query("SELECT 1")


async def test_d1_raw_batch_no_op_with_no_statements():
    assert await d1_client.d1_raw_batch([]) == []


async def test_retries_on_5xx_then_succeeds(monkeypatch):
    statuses = iter([500, 200])

    def handler(request):
        if next(statuses) == 500:
            return httpx.Response(500, json={"success": False, "errors": [{"message": "boom"}]})
        return httpx.Response(200, json={"success": True, "errors": [], "result": [{"results": [], "success": True}]})

    _install_transport(monkeypatch, handler)

    assert await d1_client.d1_query("SELECT 1") == []


async def test_gives_up_after_max_retries(monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"success": False})

    _install_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        await d1_client.d1_query("SELECT 1")


async def test_chunked_upsert_no_rows():
    assert await d1_client.chunked_upsert("cards", ["id", "name"], ["id"], []) == {"rows_written": 0, "errors": []}


async def test_chunked_upsert_builds_sql_for_single_statement(monkeypatch):
    requests_bodies = []

    def handler(request):
        body = json.loads(request.content)
        requests_bodies.append(body)
        return _meta_response([{"rows_written": 1} for _ in body["batch"]])

    _install_transport(monkeypatch, handler)

    columns = ["id", "name"]
    rows = [{"id": f"c{i}", "name": f"Card {i}"} for i in range(3)]

    result = await d1_client.chunked_upsert("cards", columns, ["id"], rows)

    assert result["errors"] == []
    # rows_written is summed straight from D1's per-statement meta, here a single statement
    assert result["rows_written"] == 1

    # rows_per_statement = 100 // 2 = 50, so all 3 rows fit in a single statement
    batch = requests_bodies[0]["batch"]
    assert len(batch) == 1
    assert batch[0]["sql"] == (
        "INSERT INTO cards (id, name) VALUES (?, ?), (?, ?), (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name"
    )
    assert batch[0]["params"] == ["c0", "Card 0", "c1", "Card 1", "c2", "Card 2"]


async def test_chunked_upsert_respects_param_limit(monkeypatch):
    """With 21 columns, D1's 100-param limit caps each statement at 4 rows."""
    requests_bodies = []

    def handler(request):
        body = json.loads(request.content)
        requests_bodies.append(body)
        return _meta_response([{"rows_written": 1} for _ in body["batch"]])

    _install_transport(monkeypatch, handler)

    columns = [f"col{i}" for i in range(21)]
    rows = [dict.fromkeys(columns, i) for i in range(5)]

    await d1_client.chunked_upsert("cards", columns, ["col0"], rows)

    batch = requests_bodies[0]["batch"]
    assert len(batch) == 2
    assert len(batch[0]["params"]) == 4 * 21  # first statement: 4 rows
    assert len(batch[1]["params"]) == 1 * 21  # second statement: remaining row


async def test_chunked_upsert_collects_errors_on_batch_failure(monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"success": False, "errors": [{"message": "fail"}]})

    _install_transport(monkeypatch, handler)

    result = await d1_client.chunked_upsert("sets", ["id", "name"], ["id"], [{"id": "s1", "name": "Set 1"}])

    assert result["rows_written"] == 0
    assert len(result["errors"]) == 1


async def test_chunked_update_no_rows():
    assert await d1_client.chunked_update("cards", ["rarity"], "id", []) == {"rows_written": 0, "errors": []}


async def test_chunked_update_builds_sql_per_row(monkeypatch):
    requests_bodies = []

    def handler(request):
        body = json.loads(request.content)
        requests_bodies.append(body)
        return _meta_response([{"changes": 1} for _ in body["batch"]])

    _install_transport(monkeypatch, handler)

    rows = [
        {"id": "c1", "rarity": "Rare", "hp": 90},
        {"id": "c2", "rarity": "Common", "hp": 60},
    ]
    result = await d1_client.chunked_update("cards", ["rarity", "hp"], "id", rows)

    assert result["rows_written"] == 2
    batch = requests_bodies[0]["batch"]
    assert len(batch) == 2
    assert batch[0]["sql"] == "UPDATE cards SET rarity=?, hp=? WHERE id=?"
    assert batch[0]["params"] == ["Rare", 90, "c1"]
    assert batch[1]["params"] == ["Common", 60, "c2"]
