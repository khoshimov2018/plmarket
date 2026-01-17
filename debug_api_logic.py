
import asyncio
import httpx
import logging
import json
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_api")

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

async def get_esports_markets(game_filter):
    async with httpx.AsyncClient(verify=False) as client:
        markets = []
        seen_ids = set()
        
        tag_slugs = ["esports", "sports", "lol", "league-of-legends", "dota-2"]
        
        for tag_slug in tag_slugs:
            try:
                logger.info(f"Checking tag: {tag_slug}")
                response = await client.get(
                    f"{GAMMA_BASE_URL}/events/pagination",
                    params={
                        "limit": 100,
                        "active": "true",
                        "archived": "false",
                        "tag_slug": tag_slug,
                        "closed": "false",
                        "order": "volume",
                        "ascending": "false",
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    events = data if isinstance(data, list) else data.get("data", [])
                    logger.info(f"  Found {len(events)} events")
                    
                    for event in events:
                        event_markets = event.get("markets", [])
                        if not event_markets:
                            event_markets = [event]
                        
                        for market_data in event_markets:
                            market_id = market_data.get("id", market_data.get("condition_id", ""))
                            if market_id in seen_ids:
                                continue
                                
                            question = market_data.get("question", "").lower()
                            title = event.get("title", "").lower()
                            combined = f"{title} {question}"
                            
                            # Check esports terms
                            is_esports = any(t in combined for t in [
                                "lol:", "league", "dota", "valorant", "cs2", "counter-strike",
                                "esport", "lck", "lpl", "lec", "worlds", "ti ", "blast"
                            ])
                            
                            if not is_esports:
                                continue
                            
                            if game_filter == "lol":
                                if not any(t in combined for t in ["lol", "league", "lck", "lec", "lpl", "worlds"]):
                                    continue
                            elif game_filter == "dota2":
                                if not any(t in combined for t in ["dota", "ti ", "the international", "dpc"]):
                                    continue
                                    
                            logger.info(f"  MATCH: {combined[:50]}... (ID: {market_id})")
                            seen_ids.add(market_id)
                            markets.append(market_data)
                            
            except Exception as e:
                logger.error(f"Error checking tag {tag_slug}: {e}")

        logger.info(f"Total markets found for {game_filter}: {len(markets)}")

async def main():
    logger.info("--- Checking LoL Markets ---")
    await get_esports_markets("lol")
    
    logger.info("\n--- Checking Dota 2 Markets ---")
    await get_esports_markets("dota2")

if __name__ == "__main__":
    asyncio.run(main())
