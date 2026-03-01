import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)

async def fetch_pokeapi_data(dex_id: int):
    """
    Fetches flavor text (EN) and evolution chain from PokéAPI.
    """
    if not dex_id or dex_id <= 0:
        return None, None

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # 1. Fetch Species Data
            res = await client.get(f"https://pokeapi.co/api/v2/pokemon-species/{dex_id}")
            if res.status_code != 200:
                return None, None
            
            data = res.json()
            
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
                evo_res = await client.get(evo_url)
                if evo_res.status_code == 200:
                    evo_data = evo_res.json()
                    chain = evo_data.get("chain", {})
                    
                    def traverse_chain(node):
                        species_name = node.get("species", {}).get("name")
                        if species_name:
                            evolutions.append(species_name.capitalize())
                        for evolves_into in node.get("evolves_to", []):
                            traverse_chain(evolves_into)
                    
                    traverse_chain(chain)
            
            return flavor_text, evolutions

        except Exception as e:
            logger.error(f"Error fetching PokéAPI data for dex_id {dex_id}: {e}")
            return None, None
