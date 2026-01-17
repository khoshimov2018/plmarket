"""
Order management for the trading bot.
Handles order lifecycle, fills, and execution tracking.
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional, Callable, Awaitable, List, Dict, Any
import uuid

from src.models import Order, Side, OrderStatus, TradingOpportunity
from src.trading.polymarket_client import PolymarketClient
from src.config import get_config
from src.logger import get_logger, trade_logger


logger = get_logger("order_manager")


class OrderManager:
    """
    Manages order lifecycle and execution.
    
    Responsibilities:
    - Order creation and validation
    - Execution with latency optimization
    - Order tracking and status updates
    - Fill handling
    """
    
    def __init__(self, client: PolymarketClient):
        self.client = client
        self.config = get_config()
        
        # Active orders
        self._pending_orders: Dict[str, Order] = {}
        self._filled_orders: Dict[str, Order] = {}
        
        # Callbacks
        self._on_fill: Optional[Callable[[Order], Awaitable[None]]] = None
        
        # Execution metrics
        self._total_orders = 0
        self._successful_orders = 0
        self._failed_orders = 0
        self._total_latency_ms = 0.0
    
    def set_on_fill_callback(
        self, 
        callback: Callable[[Order], Awaitable[None]]
    ) -> None:
        """Set callback for when orders are filled."""
        self._on_fill = callback
    
    async def execute_opportunity(
        self, 
        opportunity: TradingOpportunity
    ) -> Optional[Order]:
        """
        Execute a trading opportunity.
        
        This is the main entry point for turning detected opportunities
        into actual trades.
        
        Args:
            opportunity: The trading opportunity to execute
            
        Returns:
            The executed order or None if execution failed
        """
        # Validate opportunity is still valid
        if not self._validate_opportunity(opportunity):
            logger.warning(
                "Opportunity no longer valid",
                opportunity_id=opportunity.opportunity_id,
            )
            return None
        
        # Calculate position size
        size = await self._calculate_position_size(opportunity)
        if size <= Decimal("0"):
            logger.warning("Calculated position size is zero")
            return None
        
        # Determine token to trade
        token_id = (
            opportunity.market.token_id_yes 
            if opportunity.target_token == "yes" 
            else opportunity.market.token_id_no
        )
        
        # Execute with timing
        start_time = datetime.utcnow()
        
        order = await self.client.place_order(
            token_id=token_id,
            side=opportunity.side,
            size=size,
            price=Decimal(str(opportunity.max_price)),
        )
        
        end_time = datetime.utcnow()
        latency_ms = (end_time - start_time).total_seconds() * 1000
        
        self._total_orders += 1
        self._total_latency_ms += latency_ms
        
        if order:
            order.opportunity_id = opportunity.opportunity_id
            self._pending_orders[order.order_id] = order
            self._successful_orders += 1
            
            trade_logger.log_order_submitted(
                order_id=order.order_id,
                market_id=opportunity.market.market_id,
                side=opportunity.side.value,
                size=float(size),
                price=float(opportunity.max_price),
            )
            
            # If order was immediately filled (common in liquid markets)
            if order.status == OrderStatus.FILLED:
                await self._handle_fill(order, latency_ms)
            
            logger.info(
                "Order executed",
                order_id=order.order_id,
                latency_ms=f"{latency_ms:.1f}",
                edge=f"{opportunity.edge:.2%}",
            )
        else:
            self._failed_orders += 1
            logger.error(
                "Order execution failed",
                opportunity_id=opportunity.opportunity_id,
            )
        
        return order
    
    def _validate_opportunity(self, opportunity: TradingOpportunity) -> bool:
        """Validate that an opportunity is still viable."""
        # Check if expired
        if opportunity.expires_at and datetime.utcnow() > opportunity.expires_at:
            return False
        
        # Check minimum edge
        if abs(opportunity.edge) < self.config.trading.min_edge_threshold:
            return False
        
        # Check market is still active
        if not opportunity.market.is_active:
            return False
        
        return True
    
    async def _calculate_position_size(
        self, 
        opportunity: TradingOpportunity
    ) -> Decimal:
        """
        Calculate optimal position size based on edge and risk parameters.
        
        Uses a modified Kelly Criterion approach, scaled down for safety.
        """
        # Get current balance
        balance = await self.client.get_balance()
        available = balance.get("available", Decimal("0"))
        
        if available <= Decimal("0"):
            return Decimal("0")
        
        # Maximum position size from config
        max_size = available * Decimal(str(self.config.trading.max_position_size_pct))
        
        # Kelly fraction (scaled down by 0.25 for safety)
        # Kelly = edge / odds
        edge = abs(opportunity.edge)
        price = opportunity.max_price
        
        if price <= 0 or price >= 1:
            return Decimal("0")
        
        # Simplified Kelly for binary outcomes
        # f = p - q/b where b = (1-p)/p for fair odds
        kelly_fraction = edge / (1 - price) if price < 1 else 0
        kelly_fraction = min(0.25, kelly_fraction * 0.25)  # Quarter Kelly
        
        kelly_size = available * Decimal(str(kelly_fraction))
        
        # Use smaller of Kelly size and max size
        size = min(kelly_size, max_size)
        
        # Recommended size from opportunity (if any)
        if opportunity.recommended_size > 0:
            size = min(size, Decimal(str(opportunity.recommended_size)))
        
        # Round to reasonable precision
        size = size.quantize(Decimal("0.01"))
        
        return max(Decimal("1.00"), size)  # Minimum $1 trade
    
    async def _handle_fill(self, order: Order, latency_ms: float) -> None:
        """Handle a filled order."""
        # Move from pending to filled
        if order.order_id in self._pending_orders:
            del self._pending_orders[order.order_id]
        
        self._filled_orders[order.order_id] = order
        
        trade_logger.log_order_filled(
            order_id=order.order_id,
            fill_price=float(order.average_fill_price or order.price),
            fill_size=float(order.filled_size),
            latency_ms=latency_ms,
        )
        
        # Trigger callback
        if self._on_fill:
            await self._on_fill(order)
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        success = await self.client.cancel_order(order_id)
        
        if success and order_id in self._pending_orders:
            order = self._pending_orders.pop(order_id)
            order.status = OrderStatus.CANCELLED
            logger.info(f"Order cancelled: {order_id}")
        
        return success
    
    async def cancel_all_orders(self) -> int:
        """Cancel all pending orders."""
        cancelled = 0
        
        for order_id in list(self._pending_orders.keys()):
            if await self.cancel_order(order_id):
                cancelled += 1
        
        return cancelled
    
    def get_pending_orders(self) -> List[Order]:
        """Get all pending orders."""
        return list(self._pending_orders.values())
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get an order by ID."""
        return (
            self._pending_orders.get(order_id) or 
            self._filled_orders.get(order_id)
        )
    
    @property
    def metrics(self) -> Dict[str, Any]:
        """Get execution metrics."""
        avg_latency = (
            self._total_latency_ms / self._total_orders 
            if self._total_orders > 0 else 0
        )
        
        success_rate = (
            self._successful_orders / self._total_orders 
            if self._total_orders > 0 else 0
        )
        
        return {
            "total_orders": self._total_orders,
            "successful_orders": self._successful_orders,
            "failed_orders": self._failed_orders,
            "success_rate": success_rate,
            "average_latency_ms": avg_latency,
            "pending_orders": len(self._pending_orders),
        }




