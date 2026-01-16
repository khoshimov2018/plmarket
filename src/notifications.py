"""
Notification system for trade alerts.
Supports Discord webhooks and Telegram bots.
"""

import asyncio
from datetime import datetime
from typing import Optional
from decimal import Decimal

import httpx

from src.config import get_config
from src.models import Order, Position, TradingOpportunity, TradeRecord
from src.logger import get_logger


logger = get_logger("notifications")


class NotificationService:
    """
    Send notifications about trading activity.
    
    Supports:
    - Discord webhooks
    - Telegram bots
    """
    
    def __init__(self):
        self.config = get_config()
        self._client: Optional[httpx.AsyncClient] = None
    
    async def connect(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(timeout=10.0)
    
    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
    
    @property
    def is_enabled(self) -> bool:
        return self.config.monitoring.enable_notifications
    
    async def send_discord(self, message: str, embed: Optional[dict] = None) -> bool:
        """Send message to Discord webhook."""
        webhook_url = self.config.monitoring.discord_webhook_url
        if not webhook_url or not self._client:
            return False
        
        try:
            payload = {"content": message}
            if embed:
                payload["embeds"] = [embed]
            
            response = await self._client.post(webhook_url, json=payload)
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")
            return False
    
    async def send_telegram(self, message: str) -> bool:
        """Send message to Telegram."""
        bot_token = self.config.monitoring.telegram_bot_token
        chat_id = self.config.monitoring.telegram_chat_id
        
        if not bot_token or not chat_id or not self._client:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            }
            
            response = await self._client.post(url, json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")
            return False
    
    async def notify_opportunity(self, opportunity: TradingOpportunity) -> None:
        """Notify about a detected opportunity."""
        if not self.is_enabled:
            return
        
        message = (
            f"🎯 **Opportunity Detected**\n"
            f"Market: {opportunity.market.question[:50]}...\n"
            f"Edge: {opportunity.edge:.2%}\n"
            f"Side: {opportunity.side.value.upper()} {opportunity.target_token.upper()}\n"
            f"Model: {opportunity.model_prob:.1%} vs Market: {opportunity.market_prob:.1%}"
        )
        
        # Discord embed
        embed = {
            "title": "🎯 Opportunity Detected",
            "color": 0x00ff00,  # Green
            "fields": [
                {"name": "Market", "value": opportunity.market.question[:100], "inline": False},
                {"name": "Edge", "value": f"{opportunity.edge:.2%}", "inline": True},
                {"name": "Side", "value": f"{opportunity.side.value.upper()}", "inline": True},
                {"name": "Token", "value": opportunity.target_token.upper(), "inline": True},
                {"name": "Model Prob", "value": f"{opportunity.model_prob:.1%}", "inline": True},
                {"name": "Market Prob", "value": f"{opportunity.market_prob:.1%}", "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        await asyncio.gather(
            self.send_discord("", embed=embed),
            self.send_telegram(message.replace("**", "<b>").replace("**", "</b>")),
        )
    
    async def notify_trade_opened(self, order: Order, position: Position) -> None:
        """Notify about a new position."""
        if not self.is_enabled:
            return
        
        message = (
            f"📈 **Position Opened**\n"
            f"Size: ${float(order.filled_size):.2f}\n"
            f"Entry: {float(order.average_fill_price or order.price):.3f}\n"
            f"Side: {order.side.value.upper()}\n"
            f"Stop Loss: {float(position.stop_loss_price or 0):.3f}\n"
            f"Take Profit: {float(position.take_profit_price or 0):.3f}"
        )
        
        embed = {
            "title": "📈 Position Opened",
            "color": 0x3498db,  # Blue
            "fields": [
                {"name": "Size", "value": f"${float(order.filled_size):.2f}", "inline": True},
                {"name": "Entry Price", "value": f"{float(order.average_fill_price or order.price):.3f}", "inline": True},
                {"name": "Side", "value": order.side.value.upper(), "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        await asyncio.gather(
            self.send_discord("", embed=embed),
            self.send_telegram(message.replace("**", "<b>").replace("**", "</b>")),
        )
    
    async def notify_trade_closed(self, trade: TradeRecord) -> None:
        """Notify about a closed trade."""
        if not self.is_enabled:
            return
        
        pnl = float(trade.net_pnl)
        emoji = "🟢" if pnl >= 0 else "🔴"
        color = 0x00ff00 if pnl >= 0 else 0xff0000
        
        message = (
            f"{emoji} **Position Closed**\n"
            f"P&L: ${pnl:+.2f}\n"
            f"Entry: {float(trade.entry_price):.3f} → Exit: {float(trade.exit_price):.3f}\n"
            f"Hold Time: {trade.hold_duration_seconds:.1f}s\n"
            f"Reason: {trade.exit_reason}"
        )
        
        embed = {
            "title": f"{emoji} Position Closed",
            "color": color,
            "fields": [
                {"name": "P&L", "value": f"${pnl:+.2f}", "inline": True},
                {"name": "Entry", "value": f"{float(trade.entry_price):.3f}", "inline": True},
                {"name": "Exit", "value": f"{float(trade.exit_price):.3f}", "inline": True},
                {"name": "Hold Time", "value": f"{trade.hold_duration_seconds:.1f}s", "inline": True},
                {"name": "Reason", "value": trade.exit_reason, "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        await asyncio.gather(
            self.send_discord("", embed=embed),
            self.send_telegram(message.replace("**", "<b>").replace("**", "</b>")),
        )
    
    async def notify_daily_summary(
        self,
        trades: int,
        win_rate: float,
        pnl: float,
        volume: float,
    ) -> None:
        """Send daily trading summary."""
        if not self.is_enabled:
            return
        
        emoji = "📈" if pnl >= 0 else "📉"
        color = 0x00ff00 if pnl >= 0 else 0xff0000
        
        message = (
            f"{emoji} **Daily Summary**\n"
            f"Trades: {trades}\n"
            f"Win Rate: {win_rate:.1%}\n"
            f"P&L: ${pnl:+.2f}\n"
            f"Volume: ${volume:,.0f}"
        )
        
        embed = {
            "title": f"{emoji} Daily Summary",
            "color": color,
            "fields": [
                {"name": "Trades", "value": str(trades), "inline": True},
                {"name": "Win Rate", "value": f"{win_rate:.1%}", "inline": True},
                {"name": "P&L", "value": f"${pnl:+.2f}", "inline": True},
                {"name": "Volume", "value": f"${volume:,.0f}", "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        await asyncio.gather(
            self.send_discord("", embed=embed),
            self.send_telegram(message.replace("**", "<b>").replace("**", "</b>")),
        )
    
    async def notify_error(self, error: str, component: str = "bot") -> None:
        """Notify about an error."""
        if not self.is_enabled:
            return
        
        message = (
            f"⚠️ **Error in {component}**\n"
            f"```{error[:500]}```"
        )
        
        embed = {
            "title": f"⚠️ Error in {component}",
            "color": 0xff9900,  # Orange
            "description": error[:1000],
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        await asyncio.gather(
            self.send_discord("", embed=embed),
            self.send_telegram(f"⚠️ <b>Error in {component}</b>\n{error[:500]}"),
        )
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


# Global notification service
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get or create the global notification service."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service




