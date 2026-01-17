"""
Binance WebSocket provider for real-time crypto price data.

This module provides ultra-low-latency price feeds from Binance
to detect when crypto prices are about to cross Polymarket thresholds.
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field

import aiohttp
import websockets

from src.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PriceData:
    """Real-time price data for a crypto pair."""
    symbol: str
    price: float
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    volume_24h: float
    price_change_24h: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def spread(self) -> float:
        """Calculate bid-ask spread percentage."""
        if self.bid == 0:
            return 0
        return (self.ask - self.bid) / self.bid * 100
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price."""
        return (self.bid + self.ask) / 2


@dataclass
class OrderBookLevel:
    """Single level in order book."""
    price: float
    quantity: float


@dataclass
class OrderBook:
    """Order book snapshot."""
    symbol: str
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0
    
    @property
    def bid_volume(self) -> float:
        """Total bid volume in top 10 levels."""
        return sum(level.quantity for level in self.bids[:10])
    
    @property
    def ask_volume(self) -> float:
        """Total ask volume in top 10 levels."""
        return sum(level.quantity for level in self.asks[:10])
    
    @property
    def imbalance(self) -> float:
        """Order book imbalance (-1 to 1, positive = more bids)."""
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return 0
        return (self.bid_volume - self.ask_volume) / total


class BinanceProvider:
    """
    Real-time Binance data provider using WebSocket streams.
    
    Provides:
    - Real-time price updates (bookTicker stream)
    - Order book depth (depth stream)
    - Trade stream for volume analysis
    - Price threshold crossing detection
    """
    
    # Primary endpoints (global)
    WS_BASE_URL = "wss://stream.binance.com:9443/ws"
    REST_BASE_URL = "https://api.binance.com/api/v3"
    
    # Fallback endpoints (for regions where binance.com is blocked)
    WS_FALLBACK_URL = "wss://data-stream.binance.vision/ws"
    REST_FALLBACK_URL = "https://data-api.binance.vision/api/v3"
    
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        symbols: Optional[List[str]] = None
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        
        # State
        self._prices: Dict[str, PriceData] = {}
        self._order_books: Dict[str, OrderBook] = {}
        self._ws_connection: Optional[websockets.WebSocketClientProtocol] = None
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._running = False
        
        # Callbacks for price updates
        self._price_callbacks: List[Callable[[str, PriceData], Any]] = []
        self._threshold_callbacks: List[Callable[[str, float, str], Any]] = []
        
        # Price thresholds to monitor (from Polymarket markets)
        self._thresholds: Dict[str, List[float]] = {}
        
        # Performance tracking
        self._last_update_times: Dict[str, float] = {}
        self._update_latencies: List[float] = []
        
        logger.info(f"BinanceProvider initialized for symbols: {self._symbols}")
    
    async def connect(self) -> None:
        """Initialize HTTP session and WebSocket connection."""
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()
        
        # Fetch initial prices via REST
        await self._fetch_initial_prices()
        
        logger.info("✅ BinanceProvider connected")
    
    async def disconnect(self) -> None:
        """Close all connections."""
        self._running = False
        
        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None
        
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        
        logger.info("BinanceProvider disconnected")
    
    async def _fetch_initial_prices(self) -> None:
        """Fetch initial prices via REST API."""
        if not self._http_session:
            return
        
        # Try primary endpoint first, then fallback
        endpoints = [self.REST_BASE_URL, self.REST_FALLBACK_URL]
        
        for base_url in endpoints:
            try:
                # Fetch 24hr ticker for all symbols
                url = f"{base_url}/ticker/24hr"
                async with self._http_session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        for ticker in data:
                            symbol = ticker.get("symbol", "")
                            if symbol in self._symbols:
                                self._prices[symbol] = PriceData(
                                    symbol=symbol,
                                    price=float(ticker.get("lastPrice", 0)),
                                    bid=float(ticker.get("bidPrice", 0)),
                                    ask=float(ticker.get("askPrice", 0)),
                                    bid_qty=float(ticker.get("bidQty", 0)),
                                    ask_qty=float(ticker.get("askQty", 0)),
                                    volume_24h=float(ticker.get("volume", 0)),
                                    price_change_24h=float(ticker.get("priceChangePercent", 0))
                                )
                                logger.info(f"📊 {symbol}: ${self._prices[symbol].price:,.2f}")
                        return  # Success, exit
                    elif response.status == 451:
                        logger.warning(f"Binance REST blocked (451) at {base_url}, trying fallback...")
                        continue
            except Exception as e:
                logger.warning(f"Error fetching from {base_url}: {e}")
                continue
        
        logger.error("Failed to fetch prices from all Binance endpoints")
    
    async def start_websocket_stream(self) -> None:
        """Start WebSocket stream for real-time price updates."""
        self._running = True
        
        # Build combined stream URL
        streams = []
        for symbol in self._symbols:
            symbol_lower = symbol.lower()
            streams.append(f"{symbol_lower}@bookTicker")  # Best bid/ask
            streams.append(f"{symbol_lower}@depth20@100ms")  # Order book depth
        
        # Try multiple WebSocket endpoints (primary and fallback)
        ws_endpoints = [
            f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}",
            f"wss://data-stream.binance.vision/stream?streams={'/'.join(streams)}",
        ]
        
        logger.info(f"🔌 Connecting to Binance WebSocket...")
        
        while self._running:
            connected = False
            for ws_url in ws_endpoints:
                if not self._running:
                    break
                try:
                    async with websockets.connect(ws_url) as ws:
                        self._ws_connection = ws
                        logger.info("✅ Binance WebSocket connected")
                        connected = True
                        
                        async for message in ws:
                            if not self._running:
                                break
                            
                            await self._handle_ws_message(message)
                        break  # Exit endpoint loop if we were connected
                        
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("WebSocket connection closed, trying next endpoint...")
                    continue
                except Exception as e:
                    if "451" in str(e):
                        logger.warning(f"Binance WebSocket blocked (451), trying fallback...")
                        continue
                    logger.error(f"WebSocket error: {e}")
                    continue
            
            if not connected:
                logger.warning("All Binance WebSocket endpoints failed, retrying in 5s...")
                await asyncio.sleep(5)
    
    async def _handle_ws_message(self, message: str) -> None:
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            
            # Combined stream format: {"stream": "btcusdt@bookTicker", "data": {...}}
            stream = data.get("stream", "")
            payload = data.get("data", data)
            
            if "@bookTicker" in stream:
                await self._handle_book_ticker(payload)
            elif "@depth" in stream:
                await self._handle_depth_update(payload)
            elif "@trade" in stream:
                await self._handle_trade(payload)
                
        except Exception as e:
            logger.debug(f"Error handling WS message: {e}")
    
    async def _handle_book_ticker(self, data: dict) -> None:
        """Handle bookTicker update (best bid/ask)."""
        symbol = data.get("s", "")
        if symbol not in self._symbols:
            return
        
        receive_time = time.time()
        
        # Update price data
        old_price = self._prices.get(symbol)
        new_price = float(data.get("a", 0))  # Best ask as price
        
        if symbol in self._prices:
            self._prices[symbol].bid = float(data.get("b", 0))
            self._prices[symbol].ask = float(data.get("a", 0))
            self._prices[symbol].bid_qty = float(data.get("B", 0))
            self._prices[symbol].ask_qty = float(data.get("A", 0))
            self._prices[symbol].price = new_price
            self._prices[symbol].timestamp = datetime.utcnow()
        else:
            self._prices[symbol] = PriceData(
                symbol=symbol,
                price=new_price,
                bid=float(data.get("b", 0)),
                ask=float(data.get("a", 0)),
                bid_qty=float(data.get("B", 0)),
                ask_qty=float(data.get("A", 0)),
                volume_24h=0,
                price_change_24h=0
            )
        
        # Track latency
        if symbol in self._last_update_times:
            latency = (receive_time - self._last_update_times[symbol]) * 1000
            self._update_latencies.append(latency)
            if len(self._update_latencies) > 1000:
                self._update_latencies = self._update_latencies[-1000:]
        self._last_update_times[symbol] = receive_time
        
        # Check threshold crossings
        await self._check_threshold_crossings(symbol, old_price, self._prices[symbol])
        
        # Notify callbacks
        for callback in self._price_callbacks:
            try:
                await callback(symbol, self._prices[symbol])
            except Exception as e:
                logger.debug(f"Price callback error: {e}")
    
    async def _handle_depth_update(self, data: dict) -> None:
        """Handle order book depth update."""
        symbol = data.get("s", "")
        if symbol not in self._symbols:
            return
        
        bids = [OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])]
        
        self._order_books[symbol] = OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks
        )
    
    async def _handle_trade(self, data: dict) -> None:
        """Handle individual trade event."""
        # Can be used for volume spike detection
        pass
    
    async def _check_threshold_crossings(
        self,
        symbol: str,
        old_price: Optional[PriceData],
        new_price: PriceData
    ) -> None:
        """Check if price crossed any monitored thresholds."""
        if symbol not in self._thresholds or not old_price:
            return
        
        old_val = old_price.price
        new_val = new_price.price
        
        for threshold in self._thresholds[symbol]:
            # Check if price crossed threshold
            crossed_up = old_val < threshold <= new_val
            crossed_down = old_val > threshold >= new_val
            
            if crossed_up:
                direction = "UP"
                logger.info(f"🚀 {symbol} CROSSED ${threshold:,.0f} {direction}! Price: ${new_val:,.2f}")
                for callback in self._threshold_callbacks:
                    try:
                        await callback(symbol, threshold, direction)
                    except Exception as e:
                        logger.debug(f"Threshold callback error: {e}")
            
            elif crossed_down:
                direction = "DOWN"
                logger.info(f"📉 {symbol} CROSSED ${threshold:,.0f} {direction}! Price: ${new_val:,.2f}")
                for callback in self._threshold_callbacks:
                    try:
                        await callback(symbol, threshold, direction)
                    except Exception as e:
                        logger.debug(f"Threshold callback error: {e}")
    
    def add_threshold(self, symbol: str, threshold: float) -> None:
        """Add a price threshold to monitor."""
        if symbol not in self._thresholds:
            self._thresholds[symbol] = []
        if threshold not in self._thresholds[symbol]:
            self._thresholds[symbol].append(threshold)
            logger.info(f"📍 Monitoring {symbol} threshold: ${threshold:,.0f}")
    
    def remove_threshold(self, symbol: str, threshold: float) -> None:
        """Remove a price threshold."""
        if symbol in self._thresholds and threshold in self._thresholds[symbol]:
            self._thresholds[symbol].remove(threshold)
    
    def on_price_update(self, callback: Callable[[str, PriceData], Any]) -> None:
        """Register callback for price updates."""
        self._price_callbacks.append(callback)
    
    def on_threshold_crossing(self, callback: Callable[[str, float, str], Any]) -> None:
        """Register callback for threshold crossings."""
        self._threshold_callbacks.append(callback)
    
    def get_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price data for a symbol."""
        return self._prices.get(symbol)
    
    def get_order_book(self, symbol: str) -> Optional[OrderBook]:
        """Get current order book for a symbol."""
        return self._order_books.get(symbol)
    
    def get_all_prices(self) -> Dict[str, PriceData]:
        """Get all current prices."""
        return self._prices.copy()
    
    @property
    def avg_latency_ms(self) -> float:
        """Average update latency in milliseconds."""
        if not self._update_latencies:
            return 0
        return sum(self._update_latencies) / len(self._update_latencies)
    
    def get_distance_to_threshold(self, symbol: str, threshold: float) -> Optional[float]:
        """
        Get percentage distance from current price to threshold.
        
        Positive = price below threshold
        Negative = price above threshold
        """
        price_data = self._prices.get(symbol)
        if not price_data:
            return None
        
        return (threshold - price_data.price) / price_data.price * 100
    
    def is_approaching_threshold(
        self,
        symbol: str,
        threshold: float,
        within_pct: float = 1.0
    ) -> bool:
        """Check if price is within X% of a threshold."""
        distance = self.get_distance_to_threshold(symbol, threshold)
        if distance is None:
            return False
        return abs(distance) <= within_pct
