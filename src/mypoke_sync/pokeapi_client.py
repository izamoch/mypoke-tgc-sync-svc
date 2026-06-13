import logging

import httpx

from mypoke_sync.utils.retry import with_async_retry

logger = logging.getLogger(__name__)


async def fetch_pokeapi_data(dex_id: int):
    """
    Fetches flavor text (EN) and evolution chain from PokéAPI with retry logic.
    """
    if not dex_id or dex_id <= 0:
        return None, None

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # Helper to perform GET and raise for status (needed for with_async_retry)
            async def get_json(url: str):
                res = await client.get(url)
                res.raise_for_status()
                return res.json()

            # 1. Fetch Species Data with retry
            data = await with_async_retry(
                get_json, f"https://pokeapi.co/api/v2/pokemon-species/{dex_id}", max_retries=3, base_delay=2.0
            )

            if not data:
                return None, None

            # Extract English Flavor Text
            flavor_text = ""
            for entry in data.get("flavor_text_entries", []):
                if entry.get("language", {}).get("name") == "en":
                    flavor_text = entry.get("flavor_text", "").replace("\n", " ").replace("\f", " ")
                    break

            # 2. Fetch Evolution Chain
            evo_url = data.get("evolution_chain", {}).get("url")
            evolutions = []
            if evo_url:
                # Fetch evolution chain data with retry
                evo_data = await with_async_retry(get_json, evo_url, max_retries=2, base_delay=2.0)

                if evo_data:
                    chain = evo_data.get("chain", {})

                    def traverse_chain(node):
                        species_name = node.get("species", {}).get("name")
                        if species_name:
                            evolutions.append(species_name.capitalize())
                        for evolves_into in node.get("evolves_to", []):
                            traverse_chain(evolves_into)

                    traverse_chain(chain)

            return flavor_text, evolutions

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"PokéAPI: Species data for dex_id {dex_id} not found (404).")
            else:
                logger.error(f"PokéAPI: HTTP error fetching data for dex_id {dex_id}: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Unexpected error fetching PokéAPI data for dex_id {dex_id}: {e}")
            return None, None
