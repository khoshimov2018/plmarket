"""
Structured logging configuration for the Polymarket Esports Arbitrage Bot.
Uses structlog for rich, structured logging output.
"""

import sys
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from rich.console import Console
from rich.logging import RichHandler

from src.config import get_config


# Rich console for pretty output
console = Console()


def add_timestamp(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Add ISO timestamp to log events."""
    event_dict["timestamp"] = datetime.utcnow().isoformat()
    return event_dict


def add_component(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Ensure component is present in log events."""
    if "component" not in event_dict:
        event_dict["component"] = "main"
    return event_dict


def setup_logging() -> None:
    """Configure structured logging for the application."""
    config = get_config()
    log_level = getattr(logging, config.monitoring.log_level.upper(), logging.INFO)
    
    # Configure standard library logging
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_time=True,
                show_path=False,
            )
        ],
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("web3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Configure structlog
    processors = [
        structlog.stdlib.filter_by_level,
        add_timestamp,
        add_component,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if config.development.debug_mode:
        # Pretty console output for development
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        # JSON output for production
        processors.append(structlog.processors.JSONRenderer())
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Get a logger instance bound to a specific component."""
    return structlog.get_logger().bind(component=component)


class TradeLogger:
    """Specialized logger for trade activity."""
    
    def __init__(self):
        self.logger = get_logger("trades")
    
    def log_opportunity_detected(
        self,
        market_id: str,
        match_id: str,
        edge: float,
        model_prob: float,
        market_prob: float,
        event_type: Optional[str] = None,
    ) -> None:
        """Log detection of a trading opportunity."""
        self.logger.info(
            "opportunity_detected",
            market_id=market_id,
            match_id=match_id,
            edge=f"{edge:.2%}",
            model_prob=f"{model_prob:.2%}",
            market_prob=f"{market_prob:.2%}",
            triggering_event=event_type,
        )
    
    def log_order_submitted(
        self,
        order_id: str,
        market_id: str,
        side: str,
        size: float,
        price: float,
    ) -> None:
        """Log order submission."""
        self.logger.info(
            "order_submitted",
            order_id=order_id,
            market_id=market_id,
            side=side,
            size=size,
            price=price,
        )
    
    def log_order_filled(
        self,
        order_id: str,
        fill_price: float,
        fill_size: float,
        latency_ms: float,
    ) -> None:
        """Log order fill."""
        self.logger.info(
            "order_filled",
            order_id=order_id,
            fill_price=fill_price,
            fill_size=fill_size,
            latency_ms=latency_ms,
        )
    
    def log_position_opened(
        self,
        position_id: str,
        market_id: str,
        side: str,
        size: float,
        entry_price: float,
    ) -> None:
        """Log position opening."""
        self.logger.info(
            "position_opened",
            position_id=position_id,
            market_id=market_id,
            side=side,
            size=size,
            entry_price=entry_price,
        )
    
    def log_position_closed(
        self,
        position_id: str,
        exit_price: float,
        pnl: float,
        reason: str,
    ) -> None:
        """Log position closing."""
        emoji = "🟢" if pnl >= 0 else "🔴"
        self.logger.info(
            f"{emoji} position_closed",
            position_id=position_id,
            exit_price=exit_price,
            pnl=f"${pnl:.2f}",
            reason=reason,
        )
    
    def log_daily_summary(
        self,
        trades: int,
        win_rate: float,
        pnl: float,
        volume: float,
    ) -> None:
        """Log daily trading summary."""
        self.logger.info(
            "📊 daily_summary",
            total_trades=trades,
            win_rate=f"{win_rate:.1%}",
            net_pnl=f"${pnl:.2f}",
            volume=f"${volume:.2f}",
        )


class GameLogger:
    """Specialized logger for game events."""
    
    def __init__(self):
        self.logger = get_logger("games")
    
    def log_match_started(
        self,
        match_id: str,
        game: str,
        team1: str,
        team2: str,
    ) -> None:
        """Log match start."""
        self.logger.info(
            "🎮 match_started",
            match_id=match_id,
            game=game,
            team1=team1,
            team2=team2,
        )
    
    def log_game_event(
        self,
        match_id: str,
        event_type: str,
        team: str,
        game_time: float,
        prob_change: float,
    ) -> None:
        """Log significant game event."""
        self.logger.debug(
            "game_event",
            match_id=match_id,
            event_type=event_type,
            team=team,
            game_time=f"{game_time:.0f}s",
            prob_change=f"{prob_change:+.2%}",
        )
    
    def log_match_ended(
        self,
        match_id: str,
        winner: str,
        duration_minutes: float,
    ) -> None:
        """Log match end."""
        self.logger.info(
            "🏆 match_ended",
            match_id=match_id,
            winner=winner,
            duration=f"{duration_minutes:.1f}min",
        )


# Global logger instances
trade_logger = TradeLogger()
game_logger = GameLogger()




