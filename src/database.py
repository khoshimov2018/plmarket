"""
Database management for trade history and analytics.
Uses SQLite for simplicity and persistence.
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, List
import json

from sqlalchemy import create_engine, Column, String, Float, DateTime, Integer, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker as async_sessionmaker

from src.config import get_config
from src.models import TradeRecord, Game, Side
from src.logger import get_logger


logger = get_logger("database")

Base = declarative_base()


class TradeHistoryTable(Base):
    """SQLAlchemy model for trade history."""
    
    __tablename__ = "trade_history"
    
    trade_id = Column(String, primary_key=True)
    market_id = Column(String, index=True)
    match_id = Column(String, index=True)
    game = Column(String)
    
    side = Column(String)
    token_type = Column(String)
    size = Column(Float)
    entry_price = Column(Float)
    exit_price = Column(Float)
    
    gross_pnl = Column(Float)
    fees = Column(Float)
    net_pnl = Column(Float)
    
    entry_time = Column(DateTime)
    exit_time = Column(DateTime)
    hold_duration_seconds = Column(Float)
    
    entry_edge = Column(Float)
    exit_reason = Column(String)
    
    game_state_json = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class DailyStatsTable(Base):
    """Daily aggregated statistics."""
    
    __tablename__ = "daily_stats"
    
    date = Column(String, primary_key=True)
    
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    
    gross_pnl = Column(Float, default=0.0)
    fees = Column(Float, default=0.0)
    net_pnl = Column(Float, default=0.0)
    
    total_volume = Column(Float, default=0.0)
    
    lol_trades = Column(Integer, default=0)
    dota_trades = Column(Integer, default=0)
    
    avg_edge = Column(Float, default=0.0)
    avg_hold_time = Column(Float, default=0.0)
    
    updated_at = Column(DateTime, default=datetime.utcnow)


class MarketCacheTable(Base):
    """Cache for market information."""
    
    __tablename__ = "market_cache"
    
    market_id = Column(String, primary_key=True)
    condition_id = Column(String)
    question = Column(Text)
    game = Column(String)
    
    token_id_yes = Column(String)
    token_id_no = Column(String)
    
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Database:
    """Database manager for the trading bot."""
    
    def __init__(self):
        config = get_config()
        self.db_path = config.database.database_path
        
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create engine
        db_url = f"sqlite:///{self.db_path}"
        self.engine = create_engine(db_url, echo=False)
        
        # Create session factory
        self.Session = sessionmaker(bind=self.engine)
        
        # Initialize database
        Base.metadata.create_all(self.engine)
        
        logger.info(f"Database initialized at {self.db_path}")
    
    def save_trade(self, trade: TradeRecord) -> None:
        """Save a trade record to the database."""
        with self.Session() as session:
            record = TradeHistoryTable(
                trade_id=trade.trade_id,
                market_id=trade.market_id,
                match_id=trade.match_id,
                game=trade.game.value,
                side=trade.side.value,
                token_type=trade.token_type,
                size=float(trade.size),
                entry_price=float(trade.entry_price),
                exit_price=float(trade.exit_price),
                gross_pnl=float(trade.gross_pnl),
                fees=float(trade.fees),
                net_pnl=float(trade.net_pnl),
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                hold_duration_seconds=trade.hold_duration_seconds,
                entry_edge=trade.entry_edge,
                exit_reason=trade.exit_reason,
                game_state_json=json.dumps(trade.game_state_at_entry) if trade.game_state_at_entry else None,
            )
            session.add(record)
            session.commit()
            
            logger.debug(f"Trade saved: {trade.trade_id}")
    
    def get_trades(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        game: Optional[Game] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Get trade history with optional filters."""
        with self.Session() as session:
            query = session.query(TradeHistoryTable)
            
            if start_date:
                query = query.filter(TradeHistoryTable.entry_time >= start_date)
            if end_date:
                query = query.filter(TradeHistoryTable.exit_time <= end_date)
            if game:
                query = query.filter(TradeHistoryTable.game == game.value)
            
            query = query.order_by(TradeHistoryTable.exit_time.desc()).limit(limit)
            
            trades = []
            for row in query.all():
                trades.append({
                    "trade_id": row.trade_id,
                    "market_id": row.market_id,
                    "game": row.game,
                    "side": row.side,
                    "size": row.size,
                    "entry_price": row.entry_price,
                    "exit_price": row.exit_price,
                    "net_pnl": row.net_pnl,
                    "entry_time": row.entry_time,
                    "exit_time": row.exit_time,
                    "hold_duration": row.hold_duration_seconds,
                    "exit_reason": row.exit_reason,
                })
            
            return trades
    
    def get_daily_stats(self, date: Optional[str] = None) -> Optional[dict]:
        """Get stats for a specific date."""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        with self.Session() as session:
            row = session.query(DailyStatsTable).filter(
                DailyStatsTable.date == date
            ).first()
            
            if row:
                return {
                    "date": row.date,
                    "total_trades": row.total_trades,
                    "winning_trades": row.winning_trades,
                    "losing_trades": row.losing_trades,
                    "net_pnl": row.net_pnl,
                    "total_volume": row.total_volume,
                    "win_rate": row.winning_trades / row.total_trades if row.total_trades > 0 else 0,
                }
            
            return None
    
    def update_daily_stats(self, trade: TradeRecord) -> None:
        """Update daily statistics with a new trade."""
        date = trade.exit_time.strftime("%Y-%m-%d")
        
        with self.Session() as session:
            row = session.query(DailyStatsTable).filter(
                DailyStatsTable.date == date
            ).first()
            
            if row is None:
                row = DailyStatsTable(date=date)
                session.add(row)
            
            row.total_trades += 1
            if float(trade.net_pnl) > 0:
                row.winning_trades += 1
            else:
                row.losing_trades += 1
            
            row.gross_pnl += float(trade.gross_pnl)
            row.fees += float(trade.fees)
            row.net_pnl += float(trade.net_pnl)
            row.total_volume += float(trade.size)
            
            if trade.game == Game.LOL:
                row.lol_trades += 1
            else:
                row.dota_trades += 1
            
            # Update averages
            row.avg_hold_time = (
                (row.avg_hold_time * (row.total_trades - 1) + trade.hold_duration_seconds)
                / row.total_trades
            )
            
            row.updated_at = datetime.utcnow()
            
            session.commit()
    
    def get_performance_summary(self) -> dict:
        """Get overall performance summary."""
        with self.Session() as session:
            trades = session.query(TradeHistoryTable).all()
            
            if not trades:
                return {
                    "total_trades": 0,
                    "total_pnl": 0.0,
                    "win_rate": 0.0,
                }
            
            total_trades = len(trades)
            winning = len([t for t in trades if t.net_pnl > 0])
            total_pnl = sum(t.net_pnl for t in trades)
            total_volume = sum(t.size for t in trades)
            
            return {
                "total_trades": total_trades,
                "winning_trades": winning,
                "losing_trades": total_trades - winning,
                "win_rate": winning / total_trades,
                "total_pnl": total_pnl,
                "total_volume": total_volume,
                "avg_pnl_per_trade": total_pnl / total_trades,
            }


# Global database instance
_db: Optional[Database] = None


def get_database() -> Database:
    """Get or create the global database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db




