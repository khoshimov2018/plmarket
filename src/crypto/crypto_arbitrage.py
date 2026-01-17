"""
Crypto Arbitrage Detector for Polymarket.

Detects arbitrage opportunities in crypto price prediction markets
by comparing real-time Binance prices with Polymarket market prices.

Strategy:
- Polymarket has markets like "Will BTC hit $100K by March 2025?"
- When BTC price approaches the threshold, market should price in higher probability
- If Binance shows BTC at $99,500 but Polymarket still prices "Yes" at 40%, that's an edge
- We buy "Yes" before the market catches up
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from src.logger import get_logger
from src.models import MarketInfo, TradingOpportunity
from .binance_provider import BinanceProvider, PriceData

logger = get_logger(__name__)


@dataclass
class CryptoMarket:
    """Represents a Polymarket crypto price prediction market."""
    market_id: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    question: str
    symbol: str  # e.g., "BTCUSDT"
    threshold: float  # e.g., 100000 for "$100K"
    direction: str  # "above" or "below"
    deadline: datetime
    current_yes_price: float = 0.5
    current_no_price: float = 0.5


@dataclass
class CryptoOpportunity:
    """Arbitrage opportunity in crypto market."""
    market: CryptoMarket
    current_price: float  # Current crypto price
    distance_to_threshold_pct: float  # How far from threshold
    model_probability: float  # Our calculated probability
    market_probability: float  # Polymarket's current price
    edge: float  # model_probability - market_probability
    direction: str  # "buy_yes" or "buy_no"
    confidence: float  # 0-1 confidence in the signal
    timestamp: datetime = field(default_factory=datetime.utcnow)


class CryptoArbitrageDetector:
    """
    Detects arbitrage opportunities in Polymarket crypto markets.
    
    Uses real-time Binance data to calculate "true" probability
    and compares with Polymarket prices to find edge.
    """
    
    def __init__(
        self,
        binance: BinanceProvider,
        min_edge: float = 0.05,  # 5% minimum edge
        max_time_to_deadline_hours: int = 24 * 30  # Max 30 days to deadline
    ):
        self.binance = binance
        self.min_edge = min_edge
        self.max_time_to_deadline = timedelta(hours=max_time_to_deadline_hours)
        
        # Active crypto markets from Polymarket
        self._markets: Dict[str, CryptoMarket] = {}
        
        # Recent opportunities (for deduplication)
        self._recent_opportunities: Dict[str, datetime] = {}
        self._opportunity_cooldown = timedelta(seconds=30)
        
        logger.info(f"CryptoArbitrageDetector initialized (min_edge={min_edge*100:.1f}%)")
    
    def add_market(self, market: CryptoMarket) -> None:
        """Add a crypto market to monitor."""
        self._markets[market.market_id] = market
        
        # Register threshold with Binance provider
        self.binance.add_threshold(market.symbol, market.threshold)
        
        logger.info(
            f"📊 Monitoring crypto market: {market.question} "
            f"(threshold=${market.threshold:,.0f})"
        )
    
    def remove_market(self, market_id: str) -> None:
        """Remove a market from monitoring."""
        if market_id in self._markets:
            market = self._markets.pop(market_id)
            self.binance.remove_threshold(market.symbol, market.threshold)
    
    def update_market_price(
        self,
        market_id: str,
        yes_price: float,
        no_price: float
    ) -> None:
        """Update Polymarket prices for a market."""
        if market_id in self._markets:
            self._markets[market_id].current_yes_price = yes_price
            self._markets[market_id].current_no_price = no_price
    
    async def check_opportunities(self) -> List[CryptoOpportunity]:
        """
        Check all markets for arbitrage opportunities.
        
        Returns list of opportunities with edge >= min_edge.
        """
        opportunities = []
        
        for market_id, market in self._markets.items():
            opportunity = await self._analyze_market(market)
            if opportunity and opportunity.edge >= self.min_edge:
                # Check cooldown
                if self._is_on_cooldown(market_id):
                    continue
                
                opportunities.append(opportunity)
                self._recent_opportunities[market_id] = datetime.utcnow()
                
                logger.info(
                    f"🎯 CRYPTO OPPORTUNITY: {market.question}\n"
                    f"   Price: ${opportunity.current_price:,.2f} | "
                    f"Threshold: ${market.threshold:,.0f}\n"
                    f"   Distance: {opportunity.distance_to_threshold_pct:+.2f}%\n"
                    f"   Our prob: {opportunity.model_probability*100:.1f}% | "
                    f"Market: {opportunity.market_probability*100:.1f}%\n"
                    f"   EDGE: {opportunity.edge*100:.1f}% | "
                    f"Action: {opportunity.direction.upper()}"
                )
        
        return opportunities
    
    async def _analyze_market(self, market: CryptoMarket) -> Optional[CryptoOpportunity]:
        """Analyze a single market for opportunity."""
        # Get current crypto price
        price_data = self.binance.get_price(market.symbol)
        if not price_data:
            return None
        
        current_price = price_data.price
        
        # Calculate distance to threshold
        distance_pct = (market.threshold - current_price) / current_price * 100
        
        # Calculate time remaining
        time_remaining = market.deadline - datetime.utcnow()
        if time_remaining.total_seconds() <= 0:
            return None  # Market expired
        
        if time_remaining > self.max_time_to_deadline:
            return None  # Too far in future
        
        # Calculate our probability estimate
        model_prob = self._calculate_probability(
            current_price=current_price,
            threshold=market.threshold,
            direction=market.direction,
            time_remaining=time_remaining,
            order_book=self.binance.get_order_book(market.symbol)
        )
        
        # Get market probability
        if market.direction == "above":
            market_prob = market.current_yes_price
        else:
            market_prob = market.current_no_price
        
        # Calculate edge
        edge = model_prob - market_prob
        
        # Determine direction
        if edge > 0:
            direction = "buy_yes" if market.direction == "above" else "buy_no"
        else:
            direction = "buy_no" if market.direction == "above" else "buy_yes"
            edge = abs(edge)  # Use absolute edge
        
        # Calculate confidence based on various factors
        confidence = self._calculate_confidence(
            distance_pct=distance_pct,
            time_remaining=time_remaining,
            edge=edge,
            order_book_imbalance=self.binance.get_order_book(market.symbol)
        )
        
        return CryptoOpportunity(
            market=market,
            current_price=current_price,
            distance_to_threshold_pct=distance_pct,
            model_probability=model_prob,
            market_probability=market_prob,
            edge=edge,
            direction=direction,
            confidence=confidence
        )
    
    def _calculate_probability(
        self,
        current_price: float,
        threshold: float,
        direction: str,
        time_remaining: timedelta,
        order_book=None
    ) -> float:
        """
        Calculate probability of price crossing threshold.
        
        Uses a simplified model based on:
        - Distance to threshold
        - Time remaining
        - Order book imbalance (if available)
        - Historical volatility (simplified)
        """
        # Distance factor (closer = higher probability)
        distance_pct = abs(threshold - current_price) / current_price * 100
        
        # Time factor (more time = higher probability of crossing)
        hours_remaining = time_remaining.total_seconds() / 3600
        
        # Base probability using distance and time
        # Simplified model: assume ~2% daily volatility for BTC
        daily_volatility = 0.02  # 2% per day
        days_remaining = hours_remaining / 24
        
        # Expected range (simplified normal distribution approximation)
        expected_move_pct = daily_volatility * (days_remaining ** 0.5) * 100
        
        # Calculate probability based on distance vs expected move
        if expected_move_pct == 0:
            base_prob = 0.5
        else:
            # Z-score approximation
            z_score = distance_pct / expected_move_pct
            
            # Simplified probability (logistic function)
            import math
            if direction == "above":
                if current_price >= threshold:
                    base_prob = 0.95  # Already above
                else:
                    base_prob = 1 / (1 + math.exp(z_score * 1.5))
            else:  # below
                if current_price <= threshold:
                    base_prob = 0.95  # Already below
                else:
                    base_prob = 1 / (1 + math.exp(-z_score * 1.5))
        
        # Adjust for order book imbalance
        if order_book:
            imbalance = order_book.imbalance
            # Positive imbalance = more buying pressure
            if direction == "above":
                base_prob += imbalance * 0.05  # Up to 5% adjustment
            else:
                base_prob -= imbalance * 0.05
        
        # Clamp to valid probability range
        return max(0.01, min(0.99, base_prob))
    
    def _calculate_confidence(
        self,
        distance_pct: float,
        time_remaining: timedelta,
        edge: float,
        order_book_imbalance=None
    ) -> float:
        """Calculate confidence in the opportunity."""
        confidence = 0.5
        
        # Higher edge = higher confidence
        confidence += min(edge * 2, 0.3)  # Up to 30% boost
        
        # Closer to threshold = higher confidence
        if abs(distance_pct) < 1:
            confidence += 0.2
        elif abs(distance_pct) < 3:
            confidence += 0.1
        
        # Order book support
        if order_book_imbalance:
            imbalance = order_book_imbalance.imbalance if hasattr(order_book_imbalance, 'imbalance') else 0
            if abs(imbalance) > 0.3:
                confidence += 0.1
        
        return min(1.0, confidence)
    
    def _is_on_cooldown(self, market_id: str) -> bool:
        """Check if market is on cooldown."""
        if market_id not in self._recent_opportunities:
            return False
        
        last_opportunity = self._recent_opportunities[market_id]
        return datetime.utcnow() - last_opportunity < self._opportunity_cooldown
    
    async def on_threshold_crossing(
        self,
        symbol: str,
        threshold: float,
        direction: str
    ) -> Optional[CryptoOpportunity]:
        """
        Handle threshold crossing event.
        
        This is called immediately when price crosses a threshold,
        allowing for ultra-fast response.
        """
        # Find market for this threshold
        for market in self._markets.values():
            if market.symbol == symbol and market.threshold == threshold:
                logger.info(
                    f"⚡ THRESHOLD CROSSED: {symbol} crossed ${threshold:,.0f} {direction}!"
                )
                
                # Immediately analyze for opportunity
                opportunity = await self._analyze_market(market)
                if opportunity and opportunity.edge >= self.min_edge:
                    return opportunity
        
        return None
    
    def get_market_summary(self) -> Dict[str, dict]:
        """Get summary of all monitored markets."""
        summary = {}
        
        for market_id, market in self._markets.items():
            price_data = self.binance.get_price(market.symbol)
            current_price = price_data.price if price_data else 0
            
            distance = self.binance.get_distance_to_threshold(
                market.symbol,
                market.threshold
            )
            
            summary[market_id] = {
                "question": market.question,
                "symbol": market.symbol,
                "threshold": market.threshold,
                "current_price": current_price,
                "distance_pct": distance,
                "market_yes_price": market.current_yes_price,
                "market_no_price": market.current_no_price,
                "deadline": market.deadline.isoformat()
            }
        
        return summary
