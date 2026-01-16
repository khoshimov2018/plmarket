"""
Polymarket CLOB API client for trading operations.
Handles order placement, market data, and account management.
"""

import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional
import base64
import json

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from src.models import (
    MarketInfo, OrderBook, Order, Side, OrderStatus, Game
)
from src.config import get_config
from src.logger import get_logger


logger = get_logger("polymarket")


class PolymarketClient:
    """
    Async client for Polymarket's CLOB (Central Limit Order Book) API.
    
    Polymarket uses a hybrid on-chain/off-chain model:
    - Orders are signed off-chain
    - Matching happens off-chain on their CLOB
    - Settlement is on-chain (Polygon)
    """
    
    # API endpoints
    CLOB_BASE_URL = "https://clob.polymarket.com"
    # Main API for market discovery
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
    # Alternative endpoints
    STRAPI_BASE_URL = "https://strapi-matic.poly.market"
    # Sports/Esports specific endpoint (discovered from website)
    SPORTS_BASE_URL = "https://polymarket.com/sports"
    
    # Chain configuration
    POLYGON_CHAIN_ID = 137
    
    def __init__(self):
        config = get_config()
        self._private_key = config.polymarket.private_key
        self._api_key = config.polymarket.api_key
        self._api_secret = config.polymarket.api_secret
        self._api_passphrase = config.polymarket.api_passphrase
        self._chain_id = config.polymarket.chain_id
        
        self._account = Account.from_key(self._private_key)
        self._address = self._account.address
        
        self._clob_client: Optional[httpx.AsyncClient] = None
        self._gamma_client: Optional[httpx.AsyncClient] = None
        
        self._is_connected = False
        self._paper_trading = config.development.paper_trading
        
        # Cache for market data
        self._market_cache: dict[str, MarketInfo] = {}
        self._esports_markets: dict[str, MarketInfo] = {}
    
    @property
    def address(self) -> str:
        return self._address
    
    async def connect(self) -> None:
        """Initialize API clients and authenticate."""
        self._clob_client = httpx.AsyncClient(
            base_url=self.CLOB_BASE_URL,
            timeout=30.0,
        )
        
        self._gamma_client = httpx.AsyncClient(
            base_url=self.GAMMA_BASE_URL,
            timeout=30.0,
            verify=False,  # Workaround for SSL certificate issues
        )
        
        self._is_connected = True
        
        if self._paper_trading:
            logger.info("Connected to Polymarket (PAPER TRADING MODE)", address=self._address)
        else:
            logger.info("Connected to Polymarket (LIVE)", address=self._address)
    
    async def disconnect(self) -> None:
        """Close API clients."""
        if self._clob_client:
            await self._clob_client.aclose()
        if self._gamma_client:
            await self._gamma_client.aclose()
        self._is_connected = False
        logger.info("Disconnected from Polymarket")
    
    def _create_l1_headers(self) -> dict:
        """Create Level 1 authentication headers (read-only)."""
        timestamp = int(time.time())
        nonce = timestamp
        
        message = f"{timestamp}{nonce}"
        message_hash = encode_defunct(text=message)
        signed = self._account.sign_message(message_hash)
        
        return {
            "POLY_ADDRESS": self._address,
            "POLY_SIGNATURE": signed.signature.hex(),
            "POLY_TIMESTAMP": str(timestamp),
            "POLY_NONCE": str(nonce),
        }
    
    def _create_l2_headers(self, method: str, path: str, body: str = "") -> dict:
        """Create Level 2 authentication headers (trading)."""
        timestamp = str(int(time.time() * 1000))
        
        # Create signature
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            base64.b64decode(self._api_secret),
            message.encode(),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.b64encode(signature).decode()
        
        return {
            "POLY_API_KEY": self._api_key,
            "POLY_SIGNATURE": signature_b64,
            "POLY_TIMESTAMP": timestamp,
            "POLY_PASSPHRASE": self._api_passphrase,
            "Content-Type": "application/json",
        }
    
    async def get_esports_markets(self, game: Optional[Game] = None) -> list[MarketInfo]:
        """
        Fetch active esports markets from Polymarket.
        
        Uses the correct API endpoint discovered from Polymarket's frontend:
        /events/pagination?tag_slug=esports
        
        Args:
            game: Optional filter for specific game (LoL or Dota2)
            
        Returns:
            List of active esports markets
        """
        try:
            markets = []
            
            # Use the correct pagination endpoint with esports tag
            try:
                response = await self._gamma_client.get(
                    "/events/pagination",
                    params={
                        "limit": 100,
                        "active": "true",
                        "archived": "false",
                        "tag_slug": "esports",
                        "closed": "false",
                        "order": "volume",
                        "ascending": "false",
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    events = data if isinstance(data, list) else data.get("data", [])
                    
                    for event in events:
                        # Each event may have multiple markets
                        event_markets = event.get("markets", [])
                        if not event_markets:
                            # Single market event
                            event_markets = [event]
                        
                        for market_data in event_markets:
                            # Filter by game if specified
                            question = market_data.get("question", "").lower()
                            title = event.get("title", "").lower()
                            combined = f"{title} {question}"
                            
                            if game == Game.LOL:
                                if not any(t in combined for t in ["lol", "league", "lck", "lec", "lpl", "worlds"]):
                                    continue
                            elif game == Game.DOTA2:
                                if not any(t in combined for t in ["dota", "ti ", "the international", "dpc"]):
                                    continue
                            
                            market = self._parse_market(market_data, game, event)
                            if market and market.market_id not in self._market_cache:
                                markets.append(market)
                                self._market_cache[market.market_id] = market
                                self._esports_markets[market.market_id] = market
                                
            except Exception as e:
                logger.warning(f"Events pagination failed: {e}")
                
                # Fallback: try direct markets endpoint
                try:
                    response = await self._gamma_client.get(
                        "/markets",
                        params={
                            "closed": "false",
                            "limit": 100,
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        for market_data in data:
                            question = market_data.get("question", "").lower()
                            # Check if it's an esports market
                            if any(t in question for t in ["esport", "league", "dota", "lol", "valorant", "cs2", "counter-strike"]):
                                market = self._parse_market(market_data, game)
                                if market and market.market_id not in self._market_cache:
                                    markets.append(market)
                                    self._market_cache[market.market_id] = market
                                    self._esports_markets[market.market_id] = market
                except Exception as e2:
                    logger.debug(f"Fallback market search failed: {e2}")
            
            logger.info(f"Found {len(markets)} esports markets")
            return markets
            
        except Exception as e:
            logger.error("Error fetching esports markets", error=str(e))
            return []
    
    def _parse_market(
        self, 
        data: dict, 
        game: Optional[Game], 
        event: Optional[dict] = None
    ) -> Optional[MarketInfo]:
        """Parse market data into MarketInfo model."""
        try:
            question = data.get("question", "").lower()
            title = (event or data).get("title", "").lower() if event else question
            combined = f"{title} {question}"
            
            # Determine game type from question/title
            if game is None:
                if any(term in combined for term in ["league", "lol:", "lol ", "worlds", "lck", "lec", "lpl"]):
                    game = Game.LOL
                elif any(term in combined for term in ["dota", "ti ", "the international", "dpc"]):
                    game = Game.DOTA2
                else:
                    # Check for other esports we might want to support later
                    if any(term in combined for term in ["valorant", "cs2", "counter-strike"]):
                        game = Game.LOL  # Temporary: treat as LoL for now
                    else:
                        return None  # Not an esports market we care about
            
            # Extract token IDs - handle different API response formats
            tokens = data.get("tokens", [])
            
            # Try to find Yes/No tokens
            yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), None)
            no_token = next((t for t in tokens if t.get("outcome") == "No"), None)
            
            # If not Yes/No, try team names (e.g., "T1", "HLE")
            if not yes_token and tokens:
                # First token is usually team 1 (Yes equivalent)
                yes_token = tokens[0] if len(tokens) > 0 else None
                no_token = tokens[1] if len(tokens) > 1 else None
            
            if not yes_token:
                # Market might be structured differently
                yes_token = {"token_id": data.get("clobTokenIds", [""])[0], "price": 0.5}
                no_token = {"token_id": data.get("clobTokenIds", ["", ""])[1] if len(data.get("clobTokenIds", [])) > 1 else "", "price": 0.5}
            
            # Try to extract team names from title/question
            # Format: "LoL: Team A vs Team B (BO3)"
            team1_name = "Team 1"
            team2_name = "Team 2"
            
            # Parse team names from title like "LoL: HLE vs T1 (BO3)"
            if " vs " in title:
                parts = title.split(" vs ")
                if len(parts) >= 2:
                    # Clean up team 1 name (remove game prefix)
                    team1_part = parts[0]
                    if ":" in team1_part:
                        team1_name = team1_part.split(":")[-1].strip()
                    else:
                        team1_name = team1_part.strip()
                    
                    # Clean up team 2 name (remove suffix like "(BO3)")
                    team2_part = parts[1]
                    if "(" in team2_part:
                        team2_name = team2_part.split("(")[0].strip()
                    else:
                        team2_name = team2_part.strip()
            
            # Get prices from tokens or outcomes
            yes_price = 0.5
            no_price = 0.5
            
            if yes_token:
                yes_price = float(yes_token.get("price", 0.5))
            if no_token:
                no_price = float(no_token.get("price", 0.5))
            
            # Also check for outcomePrices in data
            outcome_prices = data.get("outcomePrices", [])
            if outcome_prices and len(outcome_prices) >= 2:
                try:
                    yes_price = float(outcome_prices[0])
                    no_price = float(outcome_prices[1])
                except (ValueError, TypeError):
                    pass
            
            return MarketInfo(
                market_id=str(data.get("id", data.get("conditionId", ""))),
                condition_id=data.get("conditionId", data.get("condition_id", "")),
                question=data.get("question", (event or {}).get("title", "")),
                token_id_yes=str(yes_token.get("token_id", "") if isinstance(yes_token, dict) else yes_token),
                token_id_no=str(no_token.get("token_id", "") if isinstance(no_token, dict) else no_token),
                match_id=data.get("gameId", data.get("game_id", data.get("id", ""))),
                game=game,
                team1_name=team1_name.title(),
                team2_name=team2_name.title(),
                is_active=not data.get("closed", False),
                yes_price=yes_price,
                no_price=no_price,
            )
        except Exception as e:
            logger.error("Error parsing market data", error=str(e), data_keys=list(data.keys()))
            return None
    
    async def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """
        Get order book for a specific token.
        
        Args:
            token_id: The token ID to get order book for
            
        Returns:
            Current order book snapshot
        """
        try:
            response = await self._clob_client.get(
                "/book",
                params={"token_id": token_id}
            )
            response.raise_for_status()
            data = response.json()
            
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 1.0
            bid_size = float(bids[0]["size"]) if bids else 0.0
            ask_size = float(asks[0]["size"]) if asks else 0.0
            
            return OrderBook(
                market_id=token_id,
                timestamp=datetime.utcnow(),
                best_bid_yes=best_bid,
                best_ask_yes=best_ask,
                bid_size_yes=bid_size,
                ask_size_yes=ask_size,
            )
            
        except httpx.HTTPError as e:
            logger.error(f"Error fetching order book for {token_id}", error=str(e))
            return None
    
    async def get_market_price(self, market_id: str) -> tuple[float, float]:
        """
        Get current market prices for a market.
        
        Returns:
            Tuple of (yes_price, no_price)
        """
        market = self._market_cache.get(market_id)
        if not market:
            return 0.5, 0.5
        
        # Get order books for both tokens
        yes_book = await self.get_order_book(market.token_id_yes)
        no_book = await self.get_order_book(market.token_id_no)
        
        yes_price = yes_book.mid_price_yes if yes_book else 0.5
        no_price = no_book.mid_price_yes if no_book else 0.5
        
        # Normalize prices (should sum to ~1)
        total = yes_price + no_price
        if total > 0:
            yes_price = yes_price / total
            no_price = no_price / total
        
        return yes_price, no_price
    
    async def place_order(
        self,
        token_id: str,
        side: Side,
        size: Decimal,
        price: Decimal,
        order_type: str = "GTC",  # Good Till Cancelled
    ) -> Optional[Order]:
        """
        Place an order on Polymarket.
        
        Args:
            token_id: Token to trade
            side: BUY or SELL
            size: Amount to trade
            price: Limit price
            order_type: Order type (GTC, FOK, IOC)
            
        Returns:
            Order object if successful
        """
        if self._paper_trading:
            return await self._paper_place_order(token_id, side, size, price)
        
        try:
            # Create order payload
            order_payload = {
                "tokenID": token_id,
                "side": "BUY" if side == Side.BUY else "SELL",
                "size": str(size),
                "price": str(price),
                "orderType": order_type,
            }
            
            body = json.dumps(order_payload)
            headers = self._create_l2_headers("POST", "/order", body)
            
            response = await self._clob_client.post(
                "/order",
                content=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            
            order_id = data.get("orderID", str(int(time.time() * 1000)))
            
            order = Order(
                order_id=order_id,
                market_id=token_id,
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                status=OrderStatus.SUBMITTED,
            )
            
            logger.info(
                "Order placed",
                order_id=order_id,
                side=side.value,
                size=str(size),
                price=str(price),
            )
            
            return order
            
        except httpx.HTTPError as e:
            logger.error("Error placing order", error=str(e))
            return None
    
    async def _paper_place_order(
        self,
        token_id: str,
        side: Side,
        size: Decimal,
        price: Decimal,
    ) -> Order:
        """Simulate order placement for paper trading."""
        order_id = f"paper_{int(time.time() * 1000)}"
        
        # Simulate small delay
        await asyncio.sleep(0.05)
        
        order = Order(
            order_id=order_id,
            market_id=token_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            status=OrderStatus.FILLED,  # Assume immediate fill for paper
            filled_size=size,
            average_fill_price=price,
            filled_at=datetime.utcnow(),
        )
        
        logger.info(
            "📝 Paper order filled",
            order_id=order_id,
            side=side.value,
            size=str(size),
            price=str(price),
        )
        
        return order
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if self._paper_trading:
            logger.info(f"📝 Paper order cancelled: {order_id}")
            return True
        
        try:
            headers = self._create_l2_headers("DELETE", f"/order/{order_id}")
            
            response = await self._clob_client.delete(
                f"/order/{order_id}",
                headers=headers,
            )
            response.raise_for_status()
            
            logger.info(f"Order cancelled: {order_id}")
            return True
            
        except httpx.HTTPError as e:
            logger.error(f"Error cancelling order {order_id}", error=str(e))
            return False
    
    async def get_balance(self) -> dict[str, Decimal]:
        """Get account balances."""
        if self._paper_trading:
            config = get_config()
            return {
                "USDC": Decimal(str(config.trading.initial_capital)),
                "available": Decimal(str(config.trading.initial_capital)),
            }
        
        try:
            headers = self._create_l1_headers()
            
            response = await self._clob_client.get(
                "/balances",
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            
            return {
                "USDC": Decimal(str(data.get("usdc", 0))),
                "available": Decimal(str(data.get("available", 0))),
            }
            
        except httpx.HTTPError as e:
            logger.error("Error fetching balance", error=str(e))
            return {"USDC": Decimal("0"), "available": Decimal("0")}
    
    async def get_positions(self) -> list[dict]:
        """Get current open positions."""
        if self._paper_trading:
            return []
        
        try:
            headers = self._create_l1_headers()
            
            response = await self._clob_client.get(
                "/positions",
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPError as e:
            logger.error("Error fetching positions", error=str(e))
            return []
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()




