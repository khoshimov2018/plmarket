"""
Position tracking and P&L management.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
import uuid

from src.models import (
    Position, PositionStatus, Order, Side, TradeRecord, Game
)
from src.trading.polymarket_client import PolymarketClient
from src.config import get_config
from src.logger import get_logger, trade_logger


logger = get_logger("positions")


class PositionTracker:
    """
    Tracks open positions and calculates P&L.
    
    Responsibilities:
    - Track position entry and exit
    - Monitor unrealized P&L
    - Handle stop loss and take profit
    - Record trade history
    """
    
    def __init__(self, client: PolymarketClient):
        self.client = client
        self.config = get_config()
        
        # Open positions
        self._positions: dict[str, Position] = {}
        
        # Closed trades
        self._trade_history: list[TradeRecord] = []
        
        # P&L tracking
        self._realized_pnl = Decimal("0")
        self._peak_equity = Decimal(str(self.config.trading.initial_capital))
        self._current_drawdown = Decimal("0")
        
        # Daily tracking
        self._daily_pnl = Decimal("0")
        self._daily_trades = 0
        self._daily_start = datetime.utcnow().date()
    
    def open_position(self, order: Order, match_id: str, game: Game) -> Position:
        """
        Open a new position from a filled order.
        
        Args:
            order: The filled entry order
            match_id: Associated match ID
            game: Game type (LoL/Dota)
            
        Returns:
            The opened position
        """
        position_id = f"pos_{uuid.uuid4().hex[:12]}"
        
        entry_price = order.average_fill_price or order.price
        
        # Calculate stop loss and take profit prices
        stop_loss = None
        take_profit = None
        
        if order.side == Side.BUY:
            stop_loss = entry_price * Decimal(str(1 - self.config.trading.stop_loss_pct))
            take_profit = entry_price * Decimal(str(1 + self.config.trading.take_profit_pct))
        else:
            stop_loss = entry_price * Decimal(str(1 + self.config.trading.stop_loss_pct))
            take_profit = entry_price * Decimal(str(1 - self.config.trading.take_profit_pct))
        
        position = Position(
            position_id=position_id,
            market_id=order.market_id,
            token_id=order.token_id,
            side=order.side,
            size=order.filled_size,
            entry_price=entry_price,
            current_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            entry_order_id=order.order_id,
        )
        
        self._positions[position_id] = position
        
        trade_logger.log_position_opened(
            position_id=position_id,
            market_id=order.market_id,
            side=order.side.value,
            size=float(order.filled_size),
            entry_price=float(entry_price),
        )
        
        logger.info(
            "Position opened",
            position_id=position_id,
            size=str(order.filled_size),
            entry_price=str(entry_price),
            stop_loss=str(stop_loss),
            take_profit=str(take_profit),
        )
        
        return position
    
    async def update_prices(self) -> None:
        """Update current prices for all open positions."""
        for position in self._positions.values():
            if position.status != PositionStatus.OPEN:
                continue
            
            # Get current market price
            yes_price, no_price = await self.client.get_market_price(position.market_id)
            
            # Determine which price applies
            current_price = Decimal(str(yes_price))  # Simplified - would need token type
            position.current_price = current_price
            
            # Calculate unrealized P&L
            if position.side == Side.BUY:
                position.unrealized_pnl = (
                    (current_price - position.entry_price) * position.size
                )
            else:
                position.unrealized_pnl = (
                    (position.entry_price - current_price) * position.size
                )
    
    def check_exit_conditions(self) -> list[Position]:
        """
        Check which positions should be closed based on stop loss/take profit.
        
        Returns:
            List of positions that should be closed
        """
        positions_to_close = []
        
        for position in self._positions.values():
            if position.status != PositionStatus.OPEN:
                continue
            
            current = position.current_price
            
            # Check stop loss
            if position.stop_loss_price:
                if position.side == Side.BUY and current <= position.stop_loss_price:
                    position.status = PositionStatus.STOPPED_OUT
                    positions_to_close.append(position)
                    continue
                elif position.side == Side.SELL and current >= position.stop_loss_price:
                    position.status = PositionStatus.STOPPED_OUT
                    positions_to_close.append(position)
                    continue
            
            # Check take profit
            if position.take_profit_price:
                if position.side == Side.BUY and current >= position.take_profit_price:
                    positions_to_close.append(position)
                    continue
                elif position.side == Side.SELL and current <= position.take_profit_price:
                    positions_to_close.append(position)
                    continue
        
        return positions_to_close
    
    def close_position(
        self,
        position: Position,
        exit_order: Order,
        reason: str = "manual"
    ) -> TradeRecord:
        """
        Close a position and record the trade.
        
        Args:
            position: The position to close
            exit_order: The exit order
            reason: Why the position was closed
            
        Returns:
            The completed trade record
        """
        exit_price = exit_order.average_fill_price or exit_order.price
        
        # Calculate P&L
        if position.side == Side.BUY:
            gross_pnl = (exit_price - position.entry_price) * position.size
        else:
            gross_pnl = (position.entry_price - exit_price) * position.size
        
        # Estimate fees (Polymarket fee structure)
        fee_rate = Decimal("0.0015")  # 0.15% estimated
        fees = position.size * (position.entry_price + exit_price) * fee_rate
        
        net_pnl = gross_pnl - fees
        
        # Create trade record
        trade = TradeRecord(
            trade_id=f"trade_{uuid.uuid4().hex[:12]}",
            market_id=position.market_id,
            match_id="",  # Would need to track this
            game=Game.LOL,  # Would need to track this
            side=position.side,
            token_type="yes",  # Would need to track this
            size=position.size,
            entry_price=position.entry_price,
            exit_price=exit_price,
            gross_pnl=gross_pnl,
            fees=fees,
            net_pnl=net_pnl,
            entry_time=position.opened_at,
            exit_time=datetime.utcnow(),
            hold_duration_seconds=(datetime.utcnow() - position.opened_at).total_seconds(),
            entry_edge=0.0,  # Would need to track this
            exit_reason=reason,
        )
        
        # Update position
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()
        position.realized_pnl = net_pnl
        position.exit_order_id = exit_order.order_id
        
        # Remove from active positions
        if position.position_id in self._positions:
            del self._positions[position.position_id]
        
        # Update totals
        self._realized_pnl += net_pnl
        self._daily_pnl += net_pnl
        self._daily_trades += 1
        
        # Add to history
        self._trade_history.append(trade)
        
        # Log
        trade_logger.log_position_closed(
            position_id=position.position_id,
            exit_price=float(exit_price),
            pnl=float(net_pnl),
            reason=reason,
        )
        
        logger.info(
            "Position closed",
            position_id=position.position_id,
            net_pnl=f"${net_pnl:.2f}",
            hold_time=f"{trade.hold_duration_seconds:.1f}s",
            reason=reason,
        )
        
        return trade
    
    def get_open_positions(self) -> list[Position]:
        """Get all open positions."""
        return [p for p in self._positions.values() if p.status == PositionStatus.OPEN]
    
    @property
    def open_position_count(self) -> int:
        """Number of open positions."""
        return len(self.get_open_positions())
    
    @property
    def total_exposure(self) -> Decimal:
        """Total capital at risk in open positions."""
        return sum(p.size * p.entry_price for p in self.get_open_positions())
    
    @property
    def unrealized_pnl(self) -> Decimal:
        """Total unrealized P&L across open positions."""
        return sum(p.unrealized_pnl for p in self.get_open_positions())
    
    @property
    def total_pnl(self) -> Decimal:
        """Total P&L (realized + unrealized)."""
        return self._realized_pnl + self.unrealized_pnl
    
    def get_metrics(self) -> dict:
        """Get position tracking metrics."""
        trades = self._trade_history
        
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": float(self._realized_pnl),
                "avg_trade_pnl": 0.0,
                "avg_hold_time": 0.0,
                "open_positions": self.open_position_count,
            }
        
        winning = [t for t in trades if t.net_pnl > 0]
        losing = [t for t in trades if t.net_pnl <= 0]
        
        avg_pnl = sum(t.net_pnl for t in trades) / len(trades)
        avg_hold = sum(t.hold_duration_seconds for t in trades) / len(trades)
        
        return {
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(trades) if trades else 0.0,
            "total_pnl": float(self._realized_pnl),
            "avg_trade_pnl": float(avg_pnl),
            "avg_hold_time": avg_hold,
            "open_positions": self.open_position_count,
            "daily_pnl": float(self._daily_pnl),
            "daily_trades": self._daily_trades,
        }
    
    def reset_daily_stats(self) -> None:
        """Reset daily statistics (call at start of each day)."""
        today = datetime.utcnow().date()
        if today != self._daily_start:
            self._daily_pnl = Decimal("0")
            self._daily_trades = 0
            self._daily_start = today




