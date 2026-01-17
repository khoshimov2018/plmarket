
import asyncio
import logging
from src.trading.polymarket_client import PolymarketClient
from src.models import Game

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_markets")

async def debug_markets():
    client = PolymarketClient()
    await client.connect()
    
    logger.info("Fetching LoL markets...")
    lol_markets = await client.get_esports_markets(Game.LOL)
    logger.info(f"Found {len(lol_markets)} LoL markets")
    for m in lol_markets:
        logger.info(f"  - {m.question} (ID: {m.market_id})")
        
    logger.info("Fetching Dota 2 markets...")
    dota_markets = await client.get_esports_markets(Game.DOTA2)
    logger.info(f"Found {len(dota_markets)} Dota 2 markets")
    for m in dota_markets:
        logger.info(f"  - {m.question} (ID: {m.market_id})")
        
    # Debug raw response for a tag if empty
    if not lol_markets and not dota_markets:
        logger.info("Checking raw API response for 'esports' tag...")
        try:
            response = await client._gamma_client.get(
                "/events/pagination",
                params={
                    "limit": 10,
                    "active": "true",
                    "archived": "false",
                    "tag_slug": "esports",
                    "closed": "false"
                }
            )
            logger.info(f"Status: {response.status_code}")
            data = response.json()
            logger.info(f"Raw data keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
            if isinstance(data, list) and data:
                logger.info(f"First event: {data[0].get('title')}")
        except Exception as e:
            logger.error(f"Error fetching raw data: {e}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(debug_markets())
