"""
Main execution engine that orchestrates the entire trading system.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict

from src.models import Game, GameState, GameEvent, MarketInfo, TradingOpportunity
from src.config import get_config
from src.logger import get_logger, trade_logger, game_logger

from src.esports.lol_provider import LoLDataProvider
from src.esports.dota_provider import DotaDataProvider
from src.esports.opendota import OpenDotaProvider
from src.esports.lolesports import LoLEsportsProvider
from src.esports.grid_provider import GridProvider
from src.trading.polymarket_client import PolymarketClient
from src.trading.order_manager import OrderManager
from src.trading.position_tracker import PositionTracker
from src.engine.arbitrage_detector import ArbitrageDetector
from src.engine.market_matcher import MarketMatcher


logger = get_logger("engine")


class ExecutionEngine:
    """
    The main orchestrator for the esports arbitrage bot.
    
    Flow:
    1. Monitor live esports matches
    2. Track game state changes in real-time
    3. Compare our win probability model to Polymarket prices
    4. Execute trades when edge exceeds threshold
    5. Manage positions and risk
    """
    
    def __init__(self):
        self.config = get_config()
        
        # Initialize components (will be set up in start())
        # FASTEST provider - GRID.gg (paid, WebSocket streaming)
        self.grid: Optional[GridProvider] = None
        
        # Primary providers (fast, free APIs)
        self.lol_esports: Optional[LoLEsportsProvider] = None  # Official Riot data
        self.opendota: Optional[OpenDotaProvider] = None  # Free Dota 2 API
        
        # Fallback providers (PandaScore - paid/slower)
        self.lol_provider: Optional[LoLDataProvider] = None
        self.dota_provider: Optional[DotaDataProvider] = None
        self.polymarket: Optional[PolymarketClient] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_tracker: Optional[PositionTracker] = None
        self.arbitrage_detector: Optional[ArbitrageDetector] = None
        self.market_matcher: Optional[MarketMatcher] = None
        
        # State tracking
        self._is_running = False
        self._tracked_matches: Dict[str, GameState] = {}
        self._match_to_market: Dict[str, MarketInfo] = {}
        self._active_subscriptions: Dict[str, asyncio.Task] = {}
        
        # Performance tracking
        self._start_time: Optional[datetime] = None
        self._total_opportunities = 0
        self._executed_trades = 0
    
    async def start(self) -> None:
        """Initialize and start the execution engine."""
        logger.info("🚀 Starting Esports Arbitrage Bot...")
        
        # Initialize GRID.gg - FASTEST provider (paid, WebSocket)
        self.grid = GridProvider()
        
        # Initialize PRIMARY data providers (fast, free)
        # These are the key to latency arbitrage - fastest possible data
        self.lol_esports = LoLEsportsProvider()  # Official Riot - fastest for LoL
        self.opendota = OpenDotaProvider(self.config.esports.opendota_api_key)  # With API key for higher rate limits
        
        # Initialize FALLBACK providers (PandaScore - may need paid plan)
        self.lol_provider = LoLDataProvider(self.config.esports.pandascore_api_key)
        self.dota_provider = DotaDataProvider(self.config.esports.pandascore_api_key)
        
        # Initialize trading components
        self.polymarket = PolymarketClient()
        
        # Connect to services - GRID first (fastest), then others
        await asyncio.gather(
            self.grid.connect(),
            self.lol_esports.connect(),
            self.opendota.connect(),
            self.polymarket.connect(),
        )
        
        if self.grid.enabled:
            logger.info("✅ GRID.gg provider enabled - FASTEST data source active!")
        
        # Try to connect fallback providers (may fail with free tier)
        try:
            await asyncio.gather(
                self.lol_provider.connect(),
                self.dota_provider.connect(),
            )
        except Exception as e:
            logger.warning(f"Fallback providers failed to connect: {e}")
        
        # Initialize managers
        self.order_manager = OrderManager(self.polymarket)
        self.position_tracker = PositionTracker(self.polymarket)
        self.arbitrage_detector = ArbitrageDetector()
        self.market_matcher = MarketMatcher()
        
        # Set up order fill callback
        self.order_manager.set_on_fill_callback(self._on_order_filled)
        
        self._is_running = True
        self._start_time = datetime.utcnow()
        
        # Log configuration
        mode = "PAPER TRADING" if self.config.development.paper_trading else "LIVE TRADING"
        logger.info(
            f"🎮 Bot started in {mode} mode",
            initial_capital=f"${self.config.trading.initial_capital:.2f}",
            min_edge=f"{self.config.trading.min_edge_threshold:.1%}",
            max_position=f"{self.config.trading.max_position_size_pct:.0%}",
        )
        
        # Start main loop
        await self._run_main_loop()
    
    async def stop(self) -> None:
        """Stop the execution engine gracefully."""
        logger.info("Stopping bot...")
        
        self._is_running = False
        
        # Cancel all subscriptions
        for task in self._active_subscriptions.values():
            task.cancel()
        
        # Cancel any pending orders
        if self.order_manager:
            cancelled = await self.order_manager.cancel_all_orders()
            logger.info(f"Cancelled {cancelled} pending orders")
        
        # Disconnect from services
        if self.lol_esports:
            await self.lol_esports.disconnect()
        if self.opendota:
            await self.opendota.disconnect()
        if self.lol_provider:
            await self.lol_provider.disconnect()
        if self.dota_provider:
            await self.dota_provider.disconnect()
        if self.polymarket:
            await self.polymarket.disconnect()
        
        # Log final stats
        await self._log_session_summary()
        
        logger.info("Bot stopped")
    
    async def _run_main_loop(self) -> None:
        """Main execution loop."""
        # Start background tasks
        tasks = [
            asyncio.create_task(self._market_discovery_loop()),
            asyncio.create_task(self._match_monitoring_loop()),
            asyncio.create_task(self._position_management_loop()),
            asyncio.create_task(self._metrics_logging_loop()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            raise
    
    async def _market_discovery_loop(self) -> None:
        """
        Periodically discover and cache esports markets on Polymarket.
        """
        while self._is_running:
            try:
                # Fetch esports markets
                lol_markets = await self.polymarket.get_esports_markets(Game.LOL)
                dota_markets = await self.polymarket.get_esports_markets(Game.DOTA2)
                
                all_markets = lol_markets + dota_markets
                
                logger.info(
                    "Markets refreshed",
                    lol_markets=len(lol_markets),
                    dota_markets=len(dota_markets),
                )
                
                # Log market details for debugging
                if lol_markets:
                    for m in lol_markets[:3]:  # First 3
                        logger.debug(f"LoL Market: {m.question[:60]}...")
                if dota_markets:
                    for m in dota_markets[:3]:
                        logger.debug(f"Dota Market: {m.question[:60]}...")
                
                # CRITICAL: Warn if no markets available
                if not all_markets:
                    logger.warning(
                        "⚠️ NO ESPORTS MARKETS AVAILABLE ON POLYMARKET! "
                        "The bot cannot trade without active markets. "
                        "Check https://polymarket.com for esports events."
                    )
                
            except Exception as e:
                logger.error(f"Market discovery error: {e}")
            
            # Refresh every 5 minutes
            await asyncio.sleep(300)
    
    async def _match_monitoring_loop(self) -> None:
        """
        Monitor live matches and subscribe to events.
        
        Priority order for data sources:
        1. GRID.gg (fastest, paid) - WebSocket streaming
        2. LoL Esports API (official Riot data)
        3. OpenDota (free Dota 2 API)
        4. PandaScore (fallback)
        """
        while self._is_running:
            try:
                # Get live matches from ALL sources, prioritize GRID
                grid_matches = []
                lol_matches = []
                dota_matches = []
                
                # GRID: Try GRID.gg first (FASTEST - paid API)
                if self.grid and self.grid.enabled:
                    try:
                        grid_matches = await self.grid.get_live_matches()
                        if grid_matches:
                            logger.info(f"🚀 Got {len(grid_matches)} matches from GRID.gg (FASTEST)")
                    except Exception as e:
                        logger.debug(f"GRID API failed: {e}")
                
                # LoL: Try official esports API (fast, free)
                try:
                    lol_matches = await self.lol_esports.get_live_matches()
                    if lol_matches:
                        logger.debug(f"Got {len(lol_matches)} LoL matches from official API")
                except Exception as e:
                    logger.debug(f"LoL Esports API failed: {e}")
                
                # Dota: Try OpenDota (free, but may have generic team names)
                try:
                    dota_matches = await self.opendota.get_live_matches()
                    if dota_matches:
                        logger.debug(f"Got {len(dota_matches)} Dota matches from OpenDota")
                except Exception as e:
                    logger.debug(f"OpenDota API failed: {e}")
                
                # Fallback to PandaScore if primary sources found nothing
                if not lol_matches and not grid_matches and self.lol_provider:
                    try:
                        lol_matches = await self.lol_provider.get_live_matches()
                    except Exception:
                        pass
                
                if not dota_matches and not grid_matches and self.dota_provider:
                    try:
                        dota_matches = await self.dota_provider.get_live_matches()
                    except Exception:
                        pass
                
                # GRID matches take priority (have real team names)
                all_matches = grid_matches + lol_matches + dota_matches
                
                for match_data in all_matches:
                    # Get match ID - different sources use different keys
                    match_id = str(match_data.get("match_id", match_data.get("id", "")))
                    
                    # Skip if already tracking
                    if match_id in self._active_subscriptions:
                        continue
                    
                    # Get full match state using FAST provider
                    game = match_data.get("game", Game.LOL)
                    source = match_data.get("source", "")
                    
                    # Extract team names - different sources use different formats
                    # PandaScore uses "opponents" array, others use "team1"/"team2"
                    if "opponents" in match_data:
                        opponents = match_data.get("opponents", [])
                        team1_name = opponents[0].get("opponent", {}).get("name", "Unknown") if len(opponents) > 0 else "Unknown"
                        team2_name = opponents[1].get("opponent", {}).get("name", "Unknown") if len(opponents) > 1 else "Unknown"
                    else:
                        team1_name = match_data.get("team1", {}).get("name", match_data.get("team1_name", "Unknown"))
                        team2_name = match_data.get("team2", {}).get("name", match_data.get("team2_name", "Unknown"))
                    
                    logger.debug(
                        "Processing match",
                        match_id=match_id,
                        game=game.value if hasattr(game, 'value') else str(game),
                        source=source,
                        team1=team1_name,
                        team2=team2_name,
                    )
                    
                    # Skip matches without real team names early
                    if team1_name in ["Unknown", "Radiant", "Dire", "Team 1", "Team 2", ""] or \
                       team2_name in ["Unknown", "Radiant", "Dire", "Team 1", "Team 2", ""]:
                        logger.debug(f"Skipping match {match_id} - missing team names")
                        continue
                    
                    # Select the fastest provider based on source
                    # Priority: GRID > LoL Esports > OpenDota > PandaScore
                    if source == "grid" and self.grid and self.grid.enabled:
                        provider = self.grid
                    elif game == Game.LOL:
                        if source == "lolesports":
                            provider = self.lol_esports
                        elif self.grid and self.grid.enabled:
                            provider = self.grid  # GRID is faster
                        else:
                            provider = self.lol_esports or self.lol_provider
                    else:  # Dota 2
                        if self.grid and self.grid.enabled:
                            provider = self.grid  # GRID is faster
                        elif source == "opendota":
                            provider = self.opendota
                        else:
                            provider = self.opendota or self.dota_provider
                    
                    game_state = await provider.get_match_state(match_id)
                    
                    if not game_state:
                        logger.debug(f"Could not get game state for match {match_id}")
                        continue
                    
                    # CRITICAL: Skip matches with unknown/generic team names
                    # These will NEVER match Polymarket markets
                    team1_name = game_state.team1.name if game_state.team1 else "Unknown"
                    team2_name = game_state.team2.name if game_state.team2 else "Unknown"
                    
                    if team1_name in ["Unknown", "Radiant", "Dire", "Team 1", "Team 2", ""]:
                        logger.debug(f"Skipping match {match_id} - team1 has generic name: {team1_name}")
                        continue
                    if team2_name in ["Unknown", "Radiant", "Dire", "Team 1", "Team 2", ""]:
                        logger.debug(f"Skipping match {match_id} - team2 has generic name: {team2_name}")
                        continue
                    
                    # Try to find matching market
                    esports_markets = list(self.polymarket._esports_markets.values())
                    
                    logger.info(
                        "🔍 Trying to match live game to market",
                        match_id=match_id,
                        team1=game_state.team1.name if game_state.team1 else "Unknown",
                        team2=game_state.team2.name if game_state.team2 else "Unknown",
                        available_markets=len(esports_markets),
                    )
                    
                    market = self.market_matcher.match_market_to_game_state(
                        esports_markets,
                        game_state
                    )
                    
                    if market:
                        logger.info(
                            "✅ MATCHED! Found market for live game",
                            match_id=match_id,
                            market_id=market.market_id,
                            market_question=market.question[:50] if market.question else "N/A",
                        )
                    else:
                        logger.debug(
                            "❌ No market match found",
                            match_id=match_id,
                            team1=game_state.team1.name if game_state.team1 else "Unknown",
                            team2=game_state.team2.name if game_state.team2 else "Unknown",
                        )
                    
                    if market:
                        # Store mappings
                        self._tracked_matches[match_id] = game_state
                        self._match_to_market[match_id] = market
                        
                        # Subscribe to match events
                        task = asyncio.create_task(
                            self._process_match_events(match_id, game, market)
                        )
                        self._active_subscriptions[match_id] = task
                        
                        game_logger.log_match_started(
                            match_id=match_id,
                            game=game.value,
                            team1=game_state.team1.name,
                            team2=game_state.team2.name,
                        )
                        
                        logger.info(
                            "📡 Subscribed to match",
                            match_id=match_id,
                            teams=f"{game_state.team1.name} vs {game_state.team2.name}",
                            market=market.question[:40],
                        )
                
            except Exception as e:
                logger.error(f"Match monitoring error: {e}")
            
            # Check for new matches every 30 seconds
            await asyncio.sleep(30)
    
    async def _process_match_events(
        self,
        match_id: str,
        game: Game,
        market: MarketInfo,
    ) -> None:
        """
        Process events for a specific match.
        This is where the magic happens - detecting and acting on price discrepancies.
        
        Uses the FASTEST available data source for each game type.
        """
        # Select fastest provider for this game
        if game == Game.LOL:
            provider = self.lol_esports or self.lol_provider
        else:
            provider = self.opendota or self.dota_provider
        
        try:
            async for event in provider.subscribe_to_match(match_id):
                if not self._is_running:
                    break
                
                # Get current game state
                game_state = await provider.get_match_state(match_id)
                if not game_state:
                    continue
                
                # Update our tracked state
                self._tracked_matches[match_id] = game_state
                
                # Calculate probability impact of this event
                prob_change = provider.analyze_event_impact(event, game_state)
                
                if abs(prob_change) >= 0.01:  # At least 1% impact
                    game_logger.log_game_event(
                        match_id=match_id,
                        event_type=event.event_type,
                        team=event.details.get("team_name", ""),
                        game_time=event.game_time_seconds,
                        prob_change=prob_change,
                    )
                
                # Handle game-ending events - exit positions before resolution
                if event.event_type in ["game_end", "game_ending"]:
                    await self._handle_game_ending(match_id, event)
                    if event.event_type == "game_end":
                        break  # Stop processing this match
                
                # Refresh market prices
                yes_price, no_price = await self.polymarket.get_market_price(
                    market.market_id
                )
                market.yes_price = yes_price
                market.no_price = no_price
                market.last_price_update = datetime.utcnow()
                
                # Detect arbitrage opportunity
                opportunity = self.arbitrage_detector.detect_event_opportunity(
                    game_state=game_state,
                    market=market,
                    event=event,
                    prob_change=prob_change,
                )
                
                if opportunity:
                    await self._execute_opportunity(opportunity)
                
                # Also check for general mispricing
                general_opportunity = self.arbitrage_detector.detect_opportunity(
                    game_state=game_state,
                    market=market,
                )
                
                if general_opportunity and (not opportunity or 
                    general_opportunity.opportunity_id != opportunity.opportunity_id):
                    await self._execute_opportunity(general_opportunity)
        
        except asyncio.CancelledError:
            logger.debug(f"Match subscription cancelled: {match_id}")
        except Exception as e:
            logger.error(f"Error processing match {match_id}: {e}")
        finally:
            # Cleanup
            if match_id in self._active_subscriptions:
                del self._active_subscriptions[match_id]
            if match_id in self._tracked_matches:
                del self._tracked_matches[match_id]
    
    async def _handle_game_ending(
        self,
        match_id: str,
        event: GameEvent,
    ) -> None:
        """
        Handle game-ending events by closing positions before market resolution.
        
        This is critical - if we hold through resolution, we get 0 or 1,
        but if we exit early, we capture most of the profit with less risk.
        """
        # Find positions for this match
        positions = [
            p for p in self.position_tracker.get_open_positions()
            if p.market_id in [m.market_id for m in self._match_to_market.values() 
                               if self._match_to_market.get(match_id)]
        ]
        
        if not positions:
            return
        
        logger.info(
            "🏁 Game ending detected - closing positions",
            match_id=match_id,
            event_type=event.event_type,
            positions=len(positions),
        )
        
        for position in positions:
            try:
                # Exit at current market price
                exit_order = await self.polymarket.place_order(
                    token_id=position.token_id,
                    side=Side.SELL if position.side == Side.BUY else Side.BUY,
                    size=position.size,
                    price=position.current_price,
                )
                
                if exit_order:
                    self.position_tracker.close_position(
                        position=position,
                        exit_order=exit_order,
                        reason="game_ending",
                    )
                    
                    logger.info(
                        "✅ Position closed before resolution",
                        position_id=position.position_id,
                        pnl=f"${position.unrealized_pnl:.2f}",
                    )
            except Exception as e:
                logger.error(f"Failed to close position {position.position_id}: {e}")
    
    async def _execute_opportunity(
        self,
        opportunity: TradingOpportunity,
    ) -> None:
        """Execute a detected trading opportunity."""
        # Check risk limits
        if not await self._check_risk_limits():
            logger.warning("Risk limits exceeded, skipping opportunity")
            return
        
        # Check concurrent position limit
        if self.position_tracker.open_position_count >= self.config.trading.max_concurrent_positions:
            logger.warning("Maximum concurrent positions reached")
            return
        
        self._total_opportunities += 1
        
        # Execute the trade
        order = await self.order_manager.execute_opportunity(opportunity)
        
        if order:
            self._executed_trades += 1
            
            # Open position
            self.position_tracker.open_position(
                order=order,
                match_id=opportunity.game_state.match_id,
                game=opportunity.game_state.game,
            )
    
    async def _on_order_filled(self, order) -> None:
        """Callback when an order is filled."""
        logger.debug(f"Order filled: {order.order_id}")
    
    async def _position_management_loop(self) -> None:
        """
        Manage open positions - check for stop loss, take profit, etc.
        """
        while self._is_running:
            try:
                # Update prices for all positions
                await self.position_tracker.update_prices()
                
                # Check exit conditions
                positions_to_close = self.position_tracker.check_exit_conditions()
                
                for position in positions_to_close:
                    # Execute exit order
                    exit_order = await self.polymarket.place_order(
                        token_id=position.token_id,
                        side=Side.SELL if position.side == Side.BUY else Side.BUY,
                        size=position.size,
                        price=position.current_price,
                    )
                    
                    if exit_order:
                        reason = (
                            "stop_loss" if position.status == PositionStatus.STOPPED_OUT
                            else "take_profit"
                        )
                        self.position_tracker.close_position(
                            position=position,
                            exit_order=exit_order,
                            reason=reason,
                        )
                
            except Exception as e:
                logger.error(f"Position management error: {e}")
            
            # Check positions every second
            await asyncio.sleep(1)
    
    async def _check_risk_limits(self) -> bool:
        """Check if we're within risk limits."""
        metrics = self.position_tracker.get_metrics()
        
        # Check daily loss limit
        daily_pnl = metrics.get("daily_pnl", 0)
        max_daily_loss = (
            self.config.trading.initial_capital * 
            self.config.risk.daily_loss_limit_pct
        )
        
        if daily_pnl < -max_daily_loss:
            logger.warning(
                "Daily loss limit reached",
                daily_pnl=f"${daily_pnl:.2f}",
                limit=f"${max_daily_loss:.2f}",
            )
            return False
        
        # Check total exposure
        total_exposure = float(self.position_tracker.total_exposure)
        max_exposure = (
            self.config.trading.initial_capital * 
            self.config.trading.max_position_size_pct * 
            self.config.trading.max_concurrent_positions
        )
        
        if total_exposure > max_exposure:
            logger.warning(
                "Maximum exposure reached",
                exposure=f"${total_exposure:.2f}",
            )
            return False
        
        return True
    
    async def _metrics_logging_loop(self) -> None:
        """Periodically log performance metrics."""
        while self._is_running:
            try:
                # Log current status
                position_metrics = self.position_tracker.get_metrics()
                order_metrics = self.order_manager.metrics
                detector_metrics = self.arbitrage_detector.metrics
                
                # Calculate runtime
                runtime = datetime.utcnow() - self._start_time if self._start_time else timedelta()
                hours = runtime.total_seconds() / 3600
                
                logger.info(
                    "📊 Status Update",
                    runtime=f"{hours:.1f}h",
                    total_pnl=f"${position_metrics.get('total_pnl', 0):.2f}",
                    win_rate=f"{position_metrics.get('win_rate', 0):.1%}",
                    trades=position_metrics.get('total_trades', 0),
                    open_positions=position_metrics.get('open_positions', 0),
                    opportunities=detector_metrics.get('opportunities_found', 0),
                    avg_latency=f"{order_metrics.get('average_latency_ms', 0):.1f}ms",
                )
                
                # Reset daily stats if needed
                self.position_tracker.reset_daily_stats()
                
                # Cleanup old opportunity cache
                self.arbitrage_detector.cleanup_old_opportunities()
                
            except Exception as e:
                logger.error(f"Metrics logging error: {e}")
            
            # Log every 5 minutes
            await asyncio.sleep(300)
    
    async def _log_session_summary(self) -> None:
        """Log summary when stopping."""
        if not self._start_time:
            return
        
        runtime = datetime.utcnow() - self._start_time
        metrics = self.position_tracker.get_metrics()
        
        trade_logger.log_daily_summary(
            trades=metrics.get("total_trades", 0),
            win_rate=metrics.get("win_rate", 0),
            pnl=metrics.get("total_pnl", 0),
            volume=float(self.position_tracker.total_exposure),
        )
        
        logger.info(
            "📈 Session Summary",
            runtime=str(runtime),
            total_trades=metrics.get("total_trades", 0),
            total_pnl=f"${metrics.get('total_pnl', 0):.2f}",
            win_rate=f"{metrics.get('win_rate', 0):.1%}",
            opportunities_found=self._total_opportunities,
            executed_trades=self._executed_trades,
        )


# Need to import these for the position management
from src.models import Side, PositionStatus




