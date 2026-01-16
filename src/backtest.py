"""
Backtesting framework for the esports arbitrage strategy.

This module allows testing the strategy against historical match data
to evaluate performance without risking real capital.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Generator
import random

from src.models import (
    Game, GameState, GameEvent, Team, 
    TradingOpportunity, TradeRecord, Side
)
from src.engine.arbitrage_detector import ArbitrageDetector
from src.esports.lol_provider import LoLDataProvider
from src.esports.dota_provider import DotaDataProvider
from src.config import get_config
from src.logger import get_logger


logger = get_logger("backtest")


@dataclass
class SimulatedMarket:
    """Simulated market for backtesting."""
    
    market_id: str
    true_prob: float  # Actual probability (we know the outcome)
    current_price: float  # Current market price
    
    # Market dynamics
    price_lag_seconds: float = 2.0  # How slow is the market to react
    noise_std: float = 0.02  # Random price noise
    
    # State
    last_event_time: Optional[datetime] = None
    pending_price_update: Optional[float] = None
    
    def update_true_prob(self, new_prob: float, event_time: datetime) -> None:
        """Update true probability after game event."""
        self.true_prob = new_prob
        self.pending_price_update = new_prob
        self.last_event_time = event_time
    
    def get_current_price(self, current_time: datetime) -> float:
        """
        Get current market price.
        Simulates delayed price discovery.
        """
        if self.pending_price_update and self.last_event_time:
            elapsed = (current_time - self.last_event_time).total_seconds()
            
            if elapsed >= self.price_lag_seconds:
                # Market has caught up
                self.current_price = self.pending_price_update
                self.pending_price_update = None
            else:
                # Market is still lagging - partial adjustment
                progress = elapsed / self.price_lag_seconds
                old_price = self.current_price
                self.current_price = old_price + (self.pending_price_update - old_price) * progress * 0.5
        
        # Add noise
        noise = random.gauss(0, self.noise_std)
        return max(0.01, min(0.99, self.current_price + noise))


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    
    # Overall metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    
    # P&L
    gross_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    
    # Capital tracking
    starting_capital: Decimal = Decimal("0")
    ending_capital: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    peak_capital: Decimal = Decimal("0")
    
    # Trade details
    trades: list[TradeRecord] = field(default_factory=list)
    
    # Timing
    avg_hold_time_seconds: float = 0.0
    total_duration: timedelta = timedelta()
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades
    
    @property
    def return_pct(self) -> float:
        if self.starting_capital == 0:
            return 0.0
        return float((self.ending_capital - self.starting_capital) / self.starting_capital * 100)
    
    @property
    def profit_factor(self) -> Optional[float]:
        wins = sum(float(t.net_pnl) for t in self.trades if t.net_pnl > 0)
        losses = abs(sum(float(t.net_pnl) for t in self.trades if t.net_pnl < 0))
        if losses == 0:
            return None
        return wins / losses
    
    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "net_pnl": float(self.net_pnl),
            "return_pct": self.return_pct,
            "max_drawdown": float(self.max_drawdown),
            "profit_factor": self.profit_factor,
            "avg_hold_time": self.avg_hold_time_seconds,
        }


class BacktestEngine:
    """
    Backtesting engine for simulating strategy performance.
    
    Uses synthetic game data to test the arbitrage detection
    and execution logic.
    """
    
    def __init__(
        self,
        starting_capital: float = 900.0,
        min_edge: float = 0.02,
        max_position_pct: float = 0.10,
        price_lag_seconds: float = 2.0,
    ):
        self.config = get_config()
        
        self.starting_capital = Decimal(str(starting_capital))
        self.min_edge = min_edge
        self.max_position_pct = max_position_pct
        self.price_lag_seconds = price_lag_seconds
        
        self.detector = ArbitrageDetector()
        self.lol_provider = LoLDataProvider("")  # No API key needed for backtest
        self.dota_provider = DotaDataProvider("")
    
    def generate_synthetic_match(
        self,
        game: Game,
        duration_minutes: float = 35.0,
        volatility: float = 1.0,
    ) -> Generator[tuple[GameState, SimulatedMarket, datetime], None, bool]:
        """
        Generate a synthetic match with realistic game events.
        
        Yields tuples of (game_state, market, timestamp) at each time step.
        Returns True if team 1 wins, False if team 2 wins.
        """
        # Create teams
        team1 = Team(id="team1", name="Team Alpha", short_name="TA")
        team2 = Team(id="team2", name="Team Beta", short_name="TB")
        
        # Initial state
        start_time = datetime.utcnow() - timedelta(minutes=duration_minutes)
        
        game_state = GameState(
            match_id="backtest_match",
            game=game,
            team1=team1,
            team2=team2,
            game_number=1,
            game_time_seconds=0,
            series_format=1,
        )
        
        # Initial 50/50 probability
        market = SimulatedMarket(
            market_id="backtest_market",
            true_prob=0.5,
            current_price=0.5,
            price_lag_seconds=self.price_lag_seconds,
        )
        
        # Simulate game in 10-second increments
        total_seconds = int(duration_minutes * 60)
        time_step = 10
        
        # Track who's winning
        team1_advantage = 0.0
        
        for elapsed in range(0, total_seconds, time_step):
            current_time = start_time + timedelta(seconds=elapsed)
            game_state.game_time_seconds = float(elapsed)
            
            # Generate random events
            event_chance = 0.15 * volatility  # ~15% chance of event per step
            
            if random.random() < event_chance:
                # Generate event
                event_type = random.choice(["kill", "kill", "tower", "objective"])
                event_team = team1 if random.random() < 0.5 + team1_advantage * 0.1 else team2
                
                # Event impact
                if event_type == "kill":
                    kills = random.randint(1, 3)
                    if event_team.id == "team1":
                        game_state.team1_kills += kills
                        team1_advantage += 0.02 * kills
                    else:
                        game_state.team2_kills += kills
                        team1_advantage -= 0.02 * kills
                
                elif event_type == "tower":
                    if event_team.id == "team1":
                        game_state.team1_towers += 1
                        team1_advantage += 0.03
                    else:
                        game_state.team2_towers += 1
                        team1_advantage -= 0.03
                
                elif event_type == "objective":
                    # Big objective like Baron/Roshan
                    impact = 0.08 * volatility
                    if event_team.id == "team1":
                        team1_advantage += impact
                    else:
                        team1_advantage -= impact
                
                # Update gold based on advantage
                base_gold = 1000 + elapsed * 30  # ~30 gold per second per team
                game_state.team1_gold = int(base_gold * (1 + team1_advantage * 0.2))
                game_state.team2_gold = int(base_gold * (1 - team1_advantage * 0.2))
                
                # Update true probability
                new_prob = 0.5 + team1_advantage
                new_prob = max(0.1, min(0.9, new_prob))
                market.update_true_prob(new_prob, current_time)
            
            # Calculate win probabilities
            provider = self.lol_provider if game == Game.LOL else self.dota_provider
            probs = provider._calculate_win_probability(game_state)
            game_state.team1_win_prob = probs[0]
            game_state.team2_win_prob = probs[1]
            
            yield game_state, market, current_time
        
        # Determine winner based on final state
        return team1_advantage > 0
    
    def run_single_match_backtest(
        self,
        game: Game = Game.LOL,
        duration_minutes: float = 35.0,
    ) -> BacktestResult:
        """Run backtest on a single synthetic match."""
        result = BacktestResult(
            starting_capital=self.starting_capital,
            ending_capital=self.starting_capital,
            peak_capital=self.starting_capital,
        )
        
        capital = self.starting_capital
        open_position: Optional[dict] = None
        
        generator = self.generate_synthetic_match(game, duration_minutes)
        
        try:
            for game_state, market, current_time in generator:
                # Get current market price (with lag)
                market_price = market.get_current_price(current_time)
                
                # Create mock market info
                from src.models import MarketInfo
                market_info = MarketInfo(
                    market_id=market.market_id,
                    condition_id="",
                    question="Backtest market",
                    token_id_yes="yes",
                    token_id_no="no",
                    match_id="backtest",
                    game=game,
                    team1_name=game_state.team1.name,
                    team2_name=game_state.team2.name,
                    yes_price=market_price,
                    no_price=1 - market_price,
                )
                
                # Check for opportunities
                opportunity = self.detector.detect_opportunity(
                    game_state=game_state,
                    market=market_info,
                )
                
                # Handle open position
                if open_position:
                    # Check for exit
                    entry_price = open_position["entry_price"]
                    current_value = market_price if open_position["side"] == "yes" else 1 - market_price
                    
                    pnl_pct = (current_value - entry_price) / entry_price
                    
                    # Exit conditions
                    should_exit = False
                    exit_reason = ""
                    
                    if pnl_pct >= 0.10:  # Take profit
                        should_exit = True
                        exit_reason = "take_profit"
                    elif pnl_pct <= -0.05:  # Stop loss
                        should_exit = True
                        exit_reason = "stop_loss"
                    elif random.random() < 0.02:  # Random exit (simulating other exits)
                        should_exit = True
                        exit_reason = "manual"
                    
                    if should_exit:
                        # Close position
                        size = open_position["size"]
                        gross_pnl = size * Decimal(str(pnl_pct))
                        fees = size * Decimal("0.003")  # 0.3% round trip
                        net_pnl = gross_pnl - fees
                        
                        capital += net_pnl
                        
                        trade = TradeRecord(
                            trade_id=f"bt_{result.total_trades}",
                            market_id=market.market_id,
                            match_id="backtest",
                            game=game,
                            side=Side.BUY,
                            token_type=open_position["side"],
                            size=size,
                            entry_price=Decimal(str(entry_price)),
                            exit_price=Decimal(str(current_value)),
                            gross_pnl=gross_pnl,
                            fees=fees,
                            net_pnl=net_pnl,
                            entry_time=open_position["entry_time"],
                            exit_time=current_time,
                            hold_duration_seconds=(current_time - open_position["entry_time"]).total_seconds(),
                            entry_edge=open_position["edge"],
                            exit_reason=exit_reason,
                        )
                        
                        result.trades.append(trade)
                        result.total_trades += 1
                        if net_pnl > 0:
                            result.winning_trades += 1
                        else:
                            result.losing_trades += 1
                        
                        result.gross_pnl += gross_pnl
                        result.total_fees += fees
                        result.net_pnl += net_pnl
                        
                        # Update peak/drawdown
                        if capital > result.peak_capital:
                            result.peak_capital = capital
                        drawdown = result.peak_capital - capital
                        if drawdown > result.max_drawdown:
                            result.max_drawdown = drawdown
                        
                        open_position = None
                
                # Open new position if opportunity and no current position
                if opportunity and not open_position:
                    position_size = capital * Decimal(str(self.max_position_pct))
                    
                    open_position = {
                        "side": opportunity.target_token,
                        "entry_price": opportunity.market_prob,
                        "size": position_size,
                        "entry_time": current_time,
                        "edge": opportunity.edge,
                    }
        
        except StopIteration:
            pass
        
        # Close any remaining position
        if open_position:
            result.total_trades += 1
        
        result.ending_capital = capital
        
        if result.trades:
            result.avg_hold_time_seconds = sum(
                t.hold_duration_seconds for t in result.trades
            ) / len(result.trades)
        
        return result
    
    def run_monte_carlo(
        self,
        num_matches: int = 100,
        game: Game = Game.LOL,
    ) -> dict:
        """
        Run Monte Carlo simulation over many synthetic matches.
        
        Returns statistics about strategy performance.
        """
        results = []
        
        for i in range(num_matches):
            if i % 10 == 0:
                logger.info(f"Backtest progress: {i}/{num_matches}")
            
            result = self.run_single_match_backtest(game)
            results.append(result)
        
        # Aggregate results
        total_trades = sum(r.total_trades for r in results)
        total_winning = sum(r.winning_trades for r in results)
        total_pnl = sum(float(r.net_pnl) for r in results)
        
        returns = [r.return_pct for r in results]
        
        summary = {
            "num_matches": num_matches,
            "total_trades": total_trades,
            "avg_trades_per_match": total_trades / num_matches,
            "overall_win_rate": total_winning / total_trades if total_trades > 0 else 0,
            "total_pnl": total_pnl,
            "avg_pnl_per_match": total_pnl / num_matches,
            "avg_return_pct": sum(returns) / len(returns) if returns else 0,
            "min_return_pct": min(returns) if returns else 0,
            "max_return_pct": max(returns) if returns else 0,
            "positive_matches": sum(1 for r in results if r.net_pnl > 0),
        }
        
        logger.info(
            "Monte Carlo complete",
            matches=num_matches,
            total_pnl=f"${total_pnl:.2f}",
            win_rate=f"{summary['overall_win_rate']:.1%}",
        )
        
        return summary


def run_backtest_cli():
    """CLI entry point for backtesting."""
    from rich.console import Console
    from rich.table import Table
    
    console = Console()
    
    console.print("[bold]Running Monte Carlo Backtest...[/bold]")
    console.print()
    
    engine = BacktestEngine()
    
    # Run simulation
    results = engine.run_monte_carlo(num_matches=50, game=Game.LOL)
    
    # Display results
    table = Table(title="📊 Backtest Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Matches Simulated", str(results["num_matches"]))
    table.add_row("Total Trades", str(results["total_trades"]))
    table.add_row("Avg Trades/Match", f"{results['avg_trades_per_match']:.1f}")
    table.add_row("Win Rate", f"{results['overall_win_rate']:.1%}")
    table.add_row("Total P&L", f"${results['total_pnl']:.2f}")
    table.add_row("Avg P&L/Match", f"${results['avg_pnl_per_match']:.2f}")
    table.add_row("Avg Return", f"{results['avg_return_pct']:.1f}%")
    table.add_row("Best Match", f"{results['max_return_pct']:.1f}%")
    table.add_row("Worst Match", f"{results['min_return_pct']:.1f}%")
    table.add_row("Profitable Matches", f"{results['positive_matches']}/{results['num_matches']}")
    
    console.print(table)


if __name__ == "__main__":
    run_backtest_cli()




