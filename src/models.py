"""
Data models for the Polymarket Esports Arbitrage Bot.
Defines all core data structures used throughout the system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List
from decimal import Decimal


class Game(Enum):
    """Supported esports games."""
    LOL = "league_of_legends"
    DOTA2 = "dota2"


class MatchStatus(Enum):
    """Status of an esports match."""
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class Side(Enum):
    """Trading side."""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """Status of a trade order."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PositionStatus(Enum):
    """Status of a trading position."""
    OPEN = "open"
    CLOSED = "closed"
    STOPPED_OUT = "stopped_out"


@dataclass
class Team:
    """Esports team information."""
    id: str
    name: str
    short_name: str
    logo_url: Optional[str] = None


@dataclass
class GameEvent:
    """A significant in-game event that could affect odds."""
    event_type: str  # kill, tower, dragon, baron, roshan, etc.
    timestamp: datetime
    game_time_seconds: float
    team_id: str
    value: float  # Gold value or importance score
    details: dict = field(default_factory=dict)


@dataclass
class GameState:
    """Current state of a live esports match."""
    match_id: str
    game: Game
    team1: Team
    team2: Team
    
    # Current game state
    game_number: int  # Which game in a series (1, 2, 3, etc.)
    game_time_seconds: float
    
    # Team stats
    team1_kills: int = 0
    team2_kills: int = 0
    team1_gold: int = 0
    team2_gold: int = 0
    team1_towers: int = 0
    team2_towers: int = 0
    
    # Series score (for Bo3, Bo5)
    team1_series_score: int = 0
    team2_series_score: int = 0
    series_format: int = 1  # Best of 1, 3, 5
    
    # Recent events
    recent_events: List[GameEvent] = field(default_factory=list)
    
    # Computed probabilities (our estimate)
    team1_win_prob: float = 0.5
    team2_win_prob: float = 0.5
    
    # Timestamps
    last_update: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def gold_lead(self) -> int:
        """Team 1's gold lead (negative if team 2 is ahead)."""
        return self.team1_gold - self.team2_gold
    
    @property
    def kill_lead(self) -> int:
        """Team 1's kill lead (negative if team 2 is ahead)."""
        return self.team1_kills - self.team2_kills
    
    @property
    def tower_lead(self) -> int:
        """Team 1's tower lead (negative if team 2 is ahead)."""
        return self.team1_towers - self.team2_towers


@dataclass
class MarketInfo:
    """Information about a Polymarket market."""
    market_id: str
    condition_id: str
    question: str
    
    # Token IDs for yes/no outcomes
    token_id_yes: str
    token_id_no: str
    
    # Associated match info
    match_id: str
    game: Game
    team1_name: str
    team2_name: str
    
    # Market state
    is_active: bool = True
    end_date: Optional[datetime] = None
    
    # Last known prices
    yes_price: float = 0.5
    no_price: float = 0.5
    last_price_update: datetime = field(default_factory=datetime.utcnow)


@dataclass  
class OrderBook:
    """Order book snapshot for a market."""
    market_id: str
    timestamp: datetime
    
    # Best bid/ask for YES token
    best_bid_yes: float = 0.0
    best_ask_yes: float = 0.0
    bid_size_yes: float = 0.0
    ask_size_yes: float = 0.0
    
    # Best bid/ask for NO token
    best_bid_no: float = 0.0
    best_ask_no: float = 0.0
    bid_size_no: float = 0.0
    ask_size_no: float = 0.0
    
    @property
    def spread_yes(self) -> float:
        """Bid-ask spread for YES token."""
        return self.best_ask_yes - self.best_bid_yes
    
    @property
    def mid_price_yes(self) -> float:
        """Mid price for YES token."""
        if self.best_bid_yes > 0 and self.best_ask_yes > 0:
            return (self.best_bid_yes + self.best_ask_yes) / 2
        return 0.0


@dataclass
class TradingOpportunity:
    """An identified arbitrage opportunity."""
    opportunity_id: str
    market: MarketInfo
    game_state: GameState
    
    # Pricing discrepancy
    model_prob: float  # Our estimated probability
    market_prob: float  # Current market probability
    edge: float  # model_prob - market_prob (or inverse for sells)
    
    # Recommended trade
    side: Side  # BUY = we think underpriced, SELL = overpriced
    target_token: str  # "yes" or "no"
    
    # Execution parameters
    recommended_size: float
    max_price: float  # Maximum price we're willing to pay
    
    # Timing
    detected_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    
    # Event that triggered this opportunity
    triggering_event: Optional[GameEvent] = None


@dataclass
class Order:
    """A trade order."""
    order_id: str
    market_id: str
    token_id: str
    
    side: Side
    size: Decimal
    price: Decimal
    
    status: OrderStatus = OrderStatus.PENDING
    filled_size: Decimal = Decimal("0")
    average_fill_price: Optional[Decimal] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = None
    
    # Linked opportunity
    opportunity_id: Optional[str] = None
    
    # Error handling
    error_message: Optional[str] = None
    retry_count: int = 0


@dataclass
class Position:
    """An open trading position."""
    position_id: str
    market_id: str
    token_id: str
    
    # Position details
    side: Side
    size: Decimal
    entry_price: Decimal
    current_price: Decimal = Decimal("0")
    
    status: PositionStatus = PositionStatus.OPEN
    
    # P&L
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    
    # Risk management
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    
    # Timestamps
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    
    # Linked orders
    entry_order_id: str = ""
    exit_order_id: Optional[str] = None


@dataclass
class TradeRecord:
    """Historical record of a completed trade."""
    trade_id: str
    market_id: str
    match_id: str
    game: Game
    
    # Trade details
    side: Side
    token_type: str  # "yes" or "no"
    size: Decimal
    entry_price: Decimal
    exit_price: Decimal
    
    # P&L
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    
    # Timing
    entry_time: datetime
    exit_time: datetime
    hold_duration_seconds: float
    
    # Context
    entry_edge: float
    exit_reason: str  # "target_hit", "stop_loss", "manual", "market_close"
    
    # Game context at entry
    game_state_at_entry: Optional[dict] = None


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics."""
    period_start: datetime
    period_end: datetime
    
    # Trade counts
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    
    # P&L
    gross_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    
    # Returns
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    
    # Risk metrics
    sharpe_ratio: Optional[float] = None
    win_rate: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    profit_factor: Optional[float] = None
    
    # Volume
    total_volume: Decimal = Decimal("0")
    avg_position_size: Decimal = Decimal("0")
    
    # Timing
    avg_hold_time_seconds: float = 0.0
    
    @property
    def win_rate_pct(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100




