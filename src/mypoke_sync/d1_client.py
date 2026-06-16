import asyncio
import logging
import os

import httpx

logger = logging.getLogger("d1_client")

CF_API_BASE = "https://api.cloudflare.com/client/v4"

# D1 rejects statements with more than 100 bound parameters
# (SQLITE_ERROR "too many SQL variables"), so upserts are chunked accordingly.
D1_MAX_PARAMS_PER_STATEMENT = 100

# Number of independent statements bundled into a single /raw batch request.
DEFAULT_STATEMENTS_PER_REQUEST = 50

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0


class D1Error(Exception):
    """Raised when the D1 REST API reports a request or query error."""


def is_configured() -> bool:
    return bool(
        os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("D1_DATABASE_ID")
    )


def _base_url() -> str:
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    database_id = os.getenv("D1_DATABASE_ID", "")
    return f"{CF_API_BASE}/accounts/{account_id}/d1/database/{database_id}"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('CLOUDFLARE_API_TOKEN', '')}",
        "Content-Type": "application/json",
    }


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_RETRY_DELAY,
) -> httpx.Response:
    """POSTs the body, retrying on HTTP error responses and network errors with exponential backoff."""
    delay = base_delay
    for attempt in range(1, max_retries + 1):
        try:
            response = await client.post(url, json=body, headers=_headers())
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            if attempt == max_retries:
                raise
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else "network error"
            logger.warning(
                f"POST {url} failed (attempt {attempt}/{max_retries}, status={status}): {e}. Retrying in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
            delay *= 2

    raise RuntimeError("unreachable")


async def d1_query(sql: str, params: list | None = None) -> list[dict]:
    """Executes a single SQL statement against D1 and returns its result rows."""
    if not is_configured():
        raise D1Error("CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN and D1_DATABASE_ID must be configured")

    body: dict = {"sql": sql}
    if params is not None:
        body["params"] = params

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _post_with_retry(client, f"{_base_url()}/query", body)

    data = response.json()
    if not data.get("success") or data.get("errors"):
        raise D1Error(f"D1 query failed: {data.get('errors')}")

    result = data.get("result") or []
    if not result:
        return []
    return result[0].get("results") or []


async def d1_raw_batch(statements: list[dict]) -> list[dict]:
    """Executes a batch of independent {sql, params} statements via the /raw endpoint."""
    if not statements:
        return []

    if not is_configured():
        raise D1Error("CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN and D1_DATABASE_ID must be configured")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await _post_with_retry(client, f"{_base_url()}/raw", {"batch": statements})

    data = response.json()
    if not data.get("success") or data.get("errors"):
        raise D1Error(f"D1 batch failed: {data.get('errors')}")

    return data.get("result") or []


async def chunked_upsert(
    table: str,
    columns: list[str],
    conflict_columns: list[str],
    rows: list[dict],
    statements_per_request: int = DEFAULT_STATEMENTS_PER_REQUEST,
    condition_columns: list[str] | None = None,
) -> dict:
    """
    Upserts `rows` into `table` via `INSERT ... ON CONFLICT DO UPDATE SET col=excluded.col`.

    Rows are grouped into multi-row INSERT statements bounded by D1's
    100-bound-parameters-per-statement limit, and multiple statements are
    sent per HTTP request via the /raw batch endpoint.

    If `condition_columns` is given, the DO UPDATE clause is guarded by
    `WHERE <col> IS NOT excluded.<col> OR ...` so that rows whose content
    columns are all unchanged are left untouched (preserving updated_at).
    Uses IS NOT / IS to handle NULL comparisons correctly.
    """
    if not rows:
        return {"rows_written": 0, "errors": []}

    rows_per_statement = max(1, D1_MAX_PARAMS_PER_STATEMENT // len(columns))
    update_columns = [c for c in columns if c not in conflict_columns]
    row_placeholder = "(" + ", ".join(["?"] * len(columns)) + ")"
    update_sql = ", ".join(f"{c}=excluded.{c}" for c in update_columns)
    conflict_sql = ", ".join(conflict_columns)
    columns_sql = ", ".join(columns)

    if condition_columns:
        # Guard: only apply the update when at least one content column differs.
        where_clause = " OR ".join(f"{c} IS NOT excluded.{c}" for c in condition_columns)
        on_conflict_sql = f"ON CONFLICT({conflict_sql}) DO UPDATE SET {update_sql} WHERE {where_clause}"
    else:
        on_conflict_sql = f"ON CONFLICT({conflict_sql}) DO UPDATE SET {update_sql}"

    statements = []
    for i in range(0, len(rows), rows_per_statement):
        chunk = rows[i : i + rows_per_statement]
        values_sql = ", ".join([row_placeholder] * len(chunk))
        sql = f"INSERT INTO {table} ({columns_sql}) VALUES {values_sql} {on_conflict_sql}"
        params = [row.get(c) for row in chunk for c in columns]
        statements.append({"sql": sql, "params": params})

    return await _run_statement_batches(table, statements, statements_per_request)


async def chunked_update(
    table: str,
    set_columns: list[str],
    where_column: str,
    rows: list[dict],
    statements_per_request: int = DEFAULT_STATEMENTS_PER_REQUEST,
    condition_columns: list[str] | None = None,
) -> dict:
    """
    Updates `rows` in `table` via one `UPDATE ... SET ... WHERE where_column = ?` per row.

    Multiple single-row UPDATE statements are sent per HTTP request via the
    /raw batch endpoint.

    If `condition_columns` is given, an extra `AND (<col> IS NOT ? OR ...)` predicate
    is appended so the UPDATE is a no-op when all content columns are unchanged,
    leaving updated_at intact. Uses IS NOT / IS for correct NULL handling.
    Each condition column must also appear in `set_columns` so its value is available.
    """
    if not rows:
        return {"rows_written": 0, "errors": []}

    set_sql = ", ".join(f"{c}=?" for c in set_columns)

    statements = []
    for row in rows:
        if condition_columns:
            cond_sql = " OR ".join(f"{c} IS NOT ?" for c in condition_columns)
            sql = f"UPDATE {table} SET {set_sql} WHERE {where_column}=? AND ({cond_sql})"
            params = (
                [row.get(c) for c in set_columns]
                + [row[where_column]]
                + [row.get(c) for c in condition_columns]
            )
        else:
            sql = f"UPDATE {table} SET {set_sql} WHERE {where_column}=?"
            params = [row.get(c) for c in set_columns] + [row[where_column]]
        statements.append({"sql": sql, "params": params})

    return await _run_statement_batches(table, statements, statements_per_request)


async def _run_statement_batches(table: str, statements: list[dict], statements_per_request: int) -> dict:
    rows_written = 0
    errors: list[str] = []

    for i in range(0, len(statements), statements_per_request):
        batch = statements[i : i + statements_per_request]
        try:
            results = await d1_raw_batch(batch)
            rows_written += sum(
                r.get("meta", {}).get("rows_written", r.get("meta", {}).get("changes", 0)) for r in results
            )
        except Exception as e:
            msg = f"{table} batch {i // statements_per_request + 1} failed: {e}"
            logger.error(msg)
            errors.append(msg)

    return {"rows_written": rows_written, "errors": errors}
