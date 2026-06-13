import io

import httpx
import imagehash
from PIL import Image


async def calculate_phash(image_url: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            response.raise_for_status()

            img = Image.open(io.BytesIO(response.content))
            # pHash with hash_size=16 produces a 16x16 hash = 256 bits
            hash_val = imagehash.phash(img, hash_size=16)
            return str(hash_val)
    except Exception as e:
        print(f"Error calculating pHash for {image_url}: {e}")
        return None
