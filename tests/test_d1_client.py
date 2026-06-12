import httpx
import pytest
from mypoke_sync import d1_client


async def _no_sleep(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    # Avoid real backoff delays during retry tests
    monkeypatch.setattr(d1_client.asyncio, "sleep", _no_sleep)


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(d1_client.httpx, "AsyncClient", factory)


async def test_skipped_when_not_configured(monkeypatch):
    monkeypatch.delenv("WORKER_URL", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    result = await d1_client.push_sync_data(cards=[{"id": "card-1"}])

    assert result["skipped"] is True
    assert result["chunks_sent"] == 0


async def test_no_op_when_no_data(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "https://worker.example")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    def handler(request):
        raise AssertionError("HTTP request should not be made when there is no data")

    _install_transport(monkeypatch, handler)

    result = await d1_client.push_sync_data()

    assert result == {"skipped": False, "chunks_sent": 0, "total_chunks": 0, "errors": []}


async def test_chunks_and_sends_required_headers(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "https://worker.example/")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, handler)

    cards = [{"id": f"card-{i}"} for i in range(5)]
    result = await d1_client.push_sync_data(cards=cards, chunk_size=2)

    assert result["chunks_sent"] == 3
    assert result["total_chunks"] == 3
    assert result["errors"] == []

    assert len(requests) == 3
    for req in requests:
        assert str(req.url) == "https://worker.example/sync/update"
        assert req.headers["X-API-Key"] == "secret-token"
        assert req.headers["Content-Type"] == "application/json"

    # Each chunk carries at most `chunk_size` cards
    assert [len(r.read()) > 0 for r in requests] == [True, True, True]


async def test_retries_on_5xx_then_succeeds(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "https://worker.example")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    statuses = iter([500, 200])

    def handler(request):
        return httpx.Response(next(statuses), json={"ok": True})

    _install_transport(monkeypatch, handler)

    result = await d1_client.push_sync_data(cards=[{"id": "card-1"}])

    assert result["chunks_sent"] == 1
    assert result["errors"] == []


async def test_retries_on_4xx_then_succeeds(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "https://worker.example")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    statuses = iter([429, 200])

    def handler(request):
        return httpx.Response(next(statuses), json={"ok": True})

    _install_transport(monkeypatch, handler)

    result = await d1_client.push_sync_data(prices=[{"card_id": "card-1", "price_type": "normal"}])

    assert result["chunks_sent"] == 1
    assert result["errors"] == []


async def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "https://worker.example")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    def handler(request):
        return httpx.Response(500, json={"ok": False})

    _install_transport(monkeypatch, handler)

    result = await d1_client.push_sync_data(sets=[{"id": "set-1"}])

    assert result["chunks_sent"] == 0
    assert result["total_chunks"] == 1
    assert len(result["errors"]) == 1
