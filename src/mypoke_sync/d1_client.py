import asyncio
import logging
import os

import httpx

logger = logging.getLogger("d1_client")

SYNC_PATH = "/sync/update"

# Cloudflare Workers / D1 impose limits on request body size and execution time,
# so updates are sent in small successive batches rather than one large payload.
DEFAULT_CHUNK_SIZE = 150
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0


async def push_sync_data(
    sets: list[dict] | None = None,
    cards: list[dict] | None = None,
    prices: list[dict] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict:
    """
    Sends sets/cards/prices to the Cloudflare Worker sync endpoint.

    Splits the data into successive chunks of `chunk_size` records per list and
    issues one POST per chunk, so large updates stay within Worker/D1 request
    size and execution time limits.
    """
    sets = sets or []
    cards = cards or []
    prices = prices or []

    if not (sets or cards or prices):
        return {"skipped": False, "chunks_sent": 0, "total_chunks": 0, "errors": []}

    worker_url = os.getenv("WORKER_URL", "").rstrip("/")
    admin_token = os.getenv("ADMIN_TOKEN", "")

    if not worker_url or not admin_token:
        logger.warning(
            "WORKER_URL and/or ADMIN_TOKEN are not configured. Skipping D1 sync push "
            f"({len(sets)} sets, {len(cards)} cards, {len(prices)} prices not sent)."
        )
        return {"skipped": True, "chunks_sent": 0, "total_chunks": 0, "errors": []}

    num_chunks = max(
        (len(sets) + chunk_size - 1) // chunk_size,
        (len(cards) + chunk_size - 1) // chunk_size,
        (len(prices) + chunk_size - 1) // chunk_size,
        1,
    )

    url = f"{worker_url}{SYNC_PATH}"
    headers = {"X-API-Key": admin_token, "Content-Type": "application/json"}

    chunks_sent = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(num_chunks):
            start, end = i * chunk_size, (i + 1) * chunk_size
            payload = {
                "sets": sets[start:end],
                "cards": cards[start:end],
                "prices": prices[start:end],
            }
            if not (payload["sets"] or payload["cards"] or payload["prices"]):
                continue

            try:
                await _post_with_retry(client, url, headers, payload)
                chunks_sent += 1
            except Exception as e:
                msg = f"Chunk {i + 1}/{num_chunks} failed: {e}"
                logger.error(msg)
                errors.append(msg)

    return {"skipped": False, "chunks_sent": chunks_sent, "total_chunks": num_chunks, "errors": errors}


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_RETRY_DELAY,
) -> httpx.Response:
    """
    POSTs the payload, retrying on HTTP error responses (4xx/5xx) and network
    errors with exponential backoff.
    """
    delay = base_delay
    for attempt in range(1, max_retries + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            if attempt == max_retries:
                raise

            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else "network error"
            logger.warning(
                f"POST {url} failed (attempt {attempt}/{max_retries}, status={status}): {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
            delay *= 2

    raise RuntimeError("unreachable")
