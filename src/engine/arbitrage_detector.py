"""
Core arbitrage detection logic.
Identifies mispricing between live game state and market odds.
"""

from datetime import datetime, timedelta
from typing import Optional
import uuid

from src.models import (
    Game, GameState, GameEvent, MarketInfo, 
    TradingOpportunity, Side
)
from src.config import get_config
from src.logger import get_logger, trade_logger


logger = get_logger("arbitrage")


class ArbitrageDetector:
    """
    Detects arbitrage opportunities by comparing:
    1. Our model's win probability (based on live game state)
    2. Polymarket's current pricing
    
    The edge is the difference. If our model says Team A has 65% win probability
    but the market is pricing it at 55%, that's a 10% edge opportunity.
    """
    
    def __init__(self):
        self.config = get_config()
        
        # Tracking
        self._opportunities_found = 0
        self._opportunities_executed = 0
        
        # Recent opportunities to avoid duplicates
        self._recent_opportunities: dict[str, datetime] = {}
        self._cooldown_seconds = 10  # Don't signal same opportunity within 10s
    
    def detect_opportunity(
        self,
        game_state: GameState,
        market: MarketInfo,
        event: Optional[GameEvent] = None,
    ) -> Optional[TradingOpportunity]:
        """
        Detect if there's a trading opportunity based on model vs market prices.
        
        Args:
            game_state: Current game state with our win probability estimate
            market: Polymarket market info with current prices
            event: Optional triggering event
            
        Returns:
            TradingOpportunity if edge exceeds threshold, None otherwise
        """
        # Get our model probability
        model_prob_team1 = game_state.team1_win_prob
        model_prob_team2 = game_state.team2_win_prob
        
        # Get market probability (from yes token price)
        # Assuming YES token = Team 1 wins
        market_prob_team1 = market.yes_price
        market_prob_team2 = market.no_price
        
        # Calculate edge for both directions
        edge_team1 = model_prob_team1 - market_prob_team1
        edge_team2 = model_prob_team2 - market_prob_team2
        
        min_edge = self.config.trading.min_edge_threshold
        
        opportunity: Optional[TradingOpportunity] = None
        
        # Check if Team 1 is underpriced (we should BUY YES)
        if edge_team1 >= min_edge:
            opportunity = self._create_opportunity(
                market=market,
                game_state=game_state,
                model_prob=model_prob_team1,
                market_prob=market_prob_team1,
                edge=edge_team1,
                side=Side.BUY,
                target_token="yes",
                event=event,
            )
        
        # Check if Team 1 is overpriced (we should BUY NO / SELL YES)
        elif edge_team2 >= min_edge:
            opportunity = self._create_opportunity(
                market=market,
                game_state=game_state,
                model_prob=model_prob_team2,
                market_prob=market_prob_team2,
                edge=edge_team2,
                side=Side.BUY,
                target_token="no",
                event=event,
            )
        
        if opportunity:
            # Check for cooldown on this market
            market_key = f"{market.market_id}_{opportunity.target_token}"
            if market_key in self._recent_opportunities:
                last_time = self._recent_opportunities[market_key]
                if (datetime.utcnow() - last_time).total_seconds() < self._cooldown_seconds:
                    logger.debug(
                        "Opportunity on cooldown",
                        market_id=market.market_id,
                    )
                    return None
            
            # Record this opportunity
            self._recent_opportunities[market_key] = datetime.utcnow()
            self._opportunities_found += 1
            
            trade_logger.log_opportunity_detected(
                market_id=market.market_id,
                match_id=game_state.match_id,
                edge=opportunity.edge,
                model_prob=opportunity.model_prob,
                market_prob=opportunity.market_prob,
                event_type=event.event_type if event else None,
            )
            
            logger.info(
                "🎯 Opportunity detected",
                market=market.question[:50],
                edge=f"{opportunity.edge:.2%}",
                side=opportunity.side.value,
                target=opportunity.target_token,
            )
        
        return opportunity
    
    def _create_opportunity(
        self,
        market: MarketInfo,
        game_state: GameState,
        model_prob: float,
        market_prob: float,
        edge: float,
        side: Side,
        target_token: str,
        event: Optional[GameEvent],
    ) -> TradingOpportunity:
        """Create a TradingOpportunity object."""
        
        # Calculate recommended size based on edge
        # Higher edge = larger position (within limits)
        base_size = 10.0  # Base position in USD
        edge_multiplier = min(5.0, edge / self.config.trading.min_edge_threshold)
        recommended_size = base_size * edge_multiplier
        
        # Maximum price we're willing to pay
        # Slightly above market to ensure fill, but below our fair value
        max_slippage = self.config.trading.max_slippage
        if side == Side.BUY:
            max_price = market_prob * (1 + max_slippage)
        else:
            max_price = market_prob * (1 - max_slippage)
        
        # Opportunity expires quickly - this is a speed game
        expires_at = datetime.utcnow() + timedelta(seconds=5)
        
        return TradingOpportunity(
            opportunity_id=f"opp_{uuid.uuid4().hex[:12]}",
            market=market,
            game_state=game_state,
            model_prob=model_prob,
            market_prob=market_prob,
            edge=edge,
            side=side,
            target_token=target_token,
            recommended_size=recommended_size,
            max_price=max_price,
            expires_at=expires_at,
            triggering_event=event,
        )
    
    def detect_event_opportunity(
        self,
        game_state: GameState,
        market: MarketInfo,
        event: GameEvent,
        prob_change: float,
    ) -> Optional[TradingOpportunity]:
        """
        Detect opportunity from a specific game event.
        
        This is called when we detect a significant in-game event
        that should shift probabilities but the market hasn't reacted yet.
        
        Args:
            game_state: Current game state
            market: Market info
            event: The triggering event
            prob_change: Expected probability change from the event
            
        Returns:
            TradingOpportunity if we should trade
        """
        # Determine which team the event favors
        event_team_id = event.team_id
        
        if event_team_id == game_state.team1.id:
            # Event favors team 1 - we expect YES price to increase
            expected_prob = min(0.95, market.yes_price + prob_change)
            edge = prob_change  # The market hasn't priced this in yet
            
            if edge >= self.config.trading.min_edge_threshold:
                return self._create_opportunity(
                    market=market,
                    game_state=game_state,
                    model_prob=expected_prob,
                    market_prob=market.yes_price,
                    edge=edge,
                    side=Side.BUY,
                    target_token="yes",
                    event=event,
                )
        else:
            # Event favors team 2 - we expect NO price to increase
            expected_prob = min(0.95, market.no_price + prob_change)
            edge = prob_change
            
            if edge >= self.config.trading.min_edge_threshold:
                return self._create_opportunity(
                    market=market,
                    game_state=game_state,
                    model_prob=expected_prob,
                    market_prob=market.no_price,
                    edge=edge,
                    side=Side.BUY,
                    target_token="no",
                    event=event,
                )
        
        return None
    
    def cleanup_old_opportunities(self) -> None:
        """Remove old entries from recent opportunities cache."""
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        
        old_keys = [
            k for k, v in self._recent_opportunities.items()
            if v < cutoff
        ]
        
        for key in old_keys:
            del self._recent_opportunities[key]
    
    @property
    def metrics(self) -> dict:
        """Get detector metrics."""
        return {
            "opportunities_found": self._opportunities_found,
            "opportunities_executed": self._opportunities_executed,
            "cached_opportunities": len(self._recent_opportunities),
        }




