import asyncio
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

async def with_async_retry(
    func: Callable[..., Any],
    *args,
    max_retries: int = 3,
    base_delay: float = 2.0,
    **kwargs
):
    """
    Helper to execute an async function with exponential backoff.
    
    Args:
        func: Async function to call.
        args: Arguments to pass to func.
        max_retries: Maximum number of retries before giving up.
        base_delay: Initial delay in seconds.
        kwargs: Keyword arguments to pass to func.
        
    Returns:
        The result of the function if successful.
        
    Raises:
        Exception: The last exception encountered if all retries fail.
    """
    retries = 0
    delay = base_delay
    
    while retries <= max_retries:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            # Check for non-retryable HTTP Status errors (400, 401, 403, 404)
            import httpx
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (400, 401, 403, 404):
                logger.warning(
                    f"Non-retryable HTTP status {e.response.status_code} encountered for {func.__name__}. "
                    "Failing immediately without retry."
                )
                raise e

            if retries == max_retries:
                logger.error(f"Max retries reached ({max_retries}) for {func.__name__}: {e}")
                raise e
            
            # Check for common retryable conditions (optional, but good for stability)
            # If it's a 404 or 400, maybe we shouldn't retry? 
            # We'll let the caller decide or catch specific exceptions if we use httpx.raise_for_status()
            
            logger.warning(
                f"Attempt {retries + 1} failed for {func.__name__}: {e}. "
                f"Retrying in {delay}s..."
            )
            await asyncio.sleep(delay)
            retries += 1
            delay *= 2.0
    
    return None
