#!/usr/bin/env python3
"""
Polymarket Esports Arbitrage Bot

A high-frequency trading bot that exploits latency between live esports game events
and Polymarket price updates in League of Legends and Dota 2 markets.

Usage:
    python main.py run          # Start the bot
    python main.py status       # Show current status
    python main.py history      # Show trade history
    python main.py backtest     # Run backtesting
"""

import asyncio
import signal
import sys
from datetime import datetime, timedelta
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box

from src.config import get_config, reload_config
from src.logger import setup_logging, get_logger
from src.engine.execution_engine import ExecutionEngine
from src.database import get_database

# Initialize
app = typer.Typer(
    name="polymarket-esports-bot",
    help="Polymarket Esports Arbitrage Trading Bot",
    add_completion=False,
)
console = Console()
logger = None

# Global engine reference for signal handling
_engine: Optional[ExecutionEngine] = None


def setup():
    """Initialize logging and configuration."""
    global logger
    setup_logging()
    logger = get_logger("main")


@app.command()
def run(
    paper: bool = typer.Option(True, "--paper/--live", help="Paper trading mode"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt (for automated deployments)"),
):
    """
    Start the esports arbitrage bot.
    
    By default runs in paper trading mode. Use --live for real trading.
    Use --yes to skip the confirmation prompt for automated deployments.
    """
    setup()
    
    # Override config if needed
    config = get_config()
    if not paper:
        config.development.paper_trading = False
    if debug:
        config.development.debug_mode = True
        config.monitoring.log_level = "DEBUG"
    
    console.print(Panel.fit(
        "[bold green]🎮 Polymarket Esports Arbitrage Bot[/bold green]\n\n"
        f"Mode: [yellow]{'Paper Trading' if paper else '🔴 LIVE TRADING'}[/yellow]\n"
        f"Initial Capital: [cyan]${config.trading.initial_capital:.2f}[/cyan]\n"
        f"Min Edge: [cyan]{config.trading.min_edge_threshold:.1%}[/cyan]\n"
        f"Max Position: [cyan]{config.trading.max_position_size_pct:.0%}[/cyan]",
        title="Configuration",
        border_style="green",
    ))
    
    if not paper and not yes:
        confirm = typer.confirm(
            "⚠️  You are about to start LIVE trading with real money. Continue?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit()
    
    # Set up signal handlers
    global _engine
    _engine = ExecutionEngine()
    
    def signal_handler(sig, frame):
        console.print("\n[yellow]Shutting down gracefully...[/yellow]")
        if _engine:
            asyncio.create_task(_engine.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the bot
    try:
        asyncio.run(_engine.start())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if debug:
            raise
        raise typer.Exit(1)


@app.command()
def status():
    """Show current bot status and open positions."""
    setup()
    
    db = get_database()
    summary = db.get_performance_summary()
    today = db.get_daily_stats()
    
    # Overall performance table
    table = Table(title="📊 Performance Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Total Trades", str(summary.get("total_trades", 0)))
    table.add_row("Win Rate", f"{summary.get('win_rate', 0):.1%}")
    table.add_row("Total P&L", f"${summary.get('total_pnl', 0):.2f}")
    table.add_row("Avg P&L/Trade", f"${summary.get('avg_pnl_per_trade', 0):.2f}")
    table.add_row("Total Volume", f"${summary.get('total_volume', 0):.2f}")
    
    console.print(table)
    
    # Today's stats
    if today:
        console.print()
        today_table = Table(title=f"📅 Today ({today['date']})", box=box.ROUNDED)
        today_table.add_column("Metric", style="cyan")
        today_table.add_column("Value", style="green")
        
        today_table.add_row("Trades", str(today.get("total_trades", 0)))
        today_table.add_row("Win Rate", f"{today.get('win_rate', 0):.1%}")
        today_table.add_row("P&L", f"${today.get('net_pnl', 0):.2f}")
        
        console.print(today_table)
    else:
        console.print("[dim]No trades today[/dim]")


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of trades to show"),
    game: Optional[str] = typer.Option(None, "--game", "-g", help="Filter by game (lol/dota)"),
):
    """Show recent trade history."""
    setup()
    
    from src.models import Game
    
    db = get_database()
    
    game_filter = None
    if game:
        game_filter = Game.LOL if game.lower() in ["lol", "league"] else Game.DOTA2
    
    trades = db.get_trades(game=game_filter, limit=limit)
    
    if not trades:
        console.print("[dim]No trades found[/dim]")
        return
    
    table = Table(title="📜 Trade History", box=box.ROUNDED)
    table.add_column("Time", style="dim")
    table.add_column("Game", style="cyan")
    table.add_column("Side")
    table.add_column("Size", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Hold", justify="right")
    table.add_column("Exit Reason", style="dim")
    
    for trade in trades:
        pnl = trade["net_pnl"]
        pnl_style = "green" if pnl >= 0 else "red"
        pnl_str = f"[{pnl_style}]${pnl:+.2f}[/{pnl_style}]"
        
        hold_time = trade["hold_duration"]
        if hold_time < 60:
            hold_str = f"{hold_time:.0f}s"
        else:
            hold_str = f"{hold_time/60:.1f}m"
        
        table.add_row(
            trade["entry_time"].strftime("%m/%d %H:%M") if trade["entry_time"] else "",
            trade["game"].upper()[:3],
            trade["side"].upper(),
            f"${trade['size']:.2f}",
            f"{trade['entry_price']:.3f}",
            f"{trade['exit_price']:.3f}",
            pnl_str,
            hold_str,
            trade["exit_reason"],
        )
    
    console.print(table)


@app.command()
def config():
    """Show current configuration."""
    setup()
    
    cfg = get_config()
    
    console.print(Panel.fit(
        f"[bold]Trading Parameters[/bold]\n"
        f"  Initial Capital: ${cfg.trading.initial_capital:.2f}\n"
        f"  Max Position Size: {cfg.trading.max_position_size_pct:.0%}\n"
        f"  Min Edge Threshold: {cfg.trading.min_edge_threshold:.1%}\n"
        f"  Max Slippage: {cfg.trading.max_slippage:.1%}\n"
        f"  Stop Loss: {cfg.trading.stop_loss_pct:.1%}\n"
        f"  Take Profit: {cfg.trading.take_profit_pct:.1%}\n"
        f"  Max Concurrent Positions: {cfg.trading.max_concurrent_positions}\n\n"
        f"[bold]Risk Management[/bold]\n"
        f"  Daily Loss Limit: {cfg.risk.daily_loss_limit_pct:.0%}\n"
        f"  Max Drawdown: {cfg.risk.max_drawdown_pct:.0%}\n"
        f"  Loss Cooldown: {cfg.risk.loss_cooldown_seconds}s\n\n"
        f"[bold]Execution[/bold]\n"
        f"  Price Check Interval: {cfg.execution.price_check_interval_ms}ms\n"
        f"  Game Poll Interval: {cfg.execution.game_state_poll_interval_ms}ms\n\n"
        f"[bold]Mode[/bold]\n"
        f"  Paper Trading: {'Yes' if cfg.development.paper_trading else 'No'}\n"
        f"  Debug Mode: {'Yes' if cfg.development.debug_mode else 'No'}",
        title="⚙️ Configuration",
        border_style="blue",
    ))


@app.command()
def markets():
    """List available esports markets on Polymarket."""
    setup()
    
    from src.trading.polymarket_client import PolymarketClient
    from src.models import Game
    
    async def fetch_markets():
        client = PolymarketClient()
        await client.connect()
        
        try:
            lol_markets = await client.get_esports_markets(Game.LOL)
            dota_markets = await client.get_esports_markets(Game.DOTA2)
            return lol_markets, dota_markets
        finally:
            await client.disconnect()
    
    console.print("[dim]Fetching markets...[/dim]")
    
    try:
        lol_markets, dota_markets = asyncio.run(fetch_markets())
    except Exception as e:
        console.print(f"[red]Error fetching markets: {e}[/red]")
        raise typer.Exit(1)
    
    if lol_markets:
        table = Table(title="🎮 League of Legends Markets", box=box.ROUNDED)
        table.add_column("Market ID", style="dim")
        table.add_column("Question")
        table.add_column("Yes Price", justify="right", style="green")
        table.add_column("No Price", justify="right", style="red")
        
        for market in lol_markets[:10]:
            table.add_row(
                market.market_id[:8] + "...",
                market.question[:50] + ("..." if len(market.question) > 50 else ""),
                f"{market.yes_price:.3f}",
                f"{market.no_price:.3f}",
            )
        
        console.print(table)
    
    if dota_markets:
        console.print()
        table = Table(title="⚔️ Dota 2 Markets", box=box.ROUNDED)
        table.add_column("Market ID", style="dim")
        table.add_column("Question")
        table.add_column("Yes Price", justify="right", style="green")
        table.add_column("No Price", justify="right", style="red")
        
        for market in dota_markets[:10]:
            table.add_row(
                market.market_id[:8] + "...",
                market.question[:50] + ("..." if len(market.question) > 50 else ""),
                f"{market.yes_price:.3f}",
                f"{market.no_price:.3f}",
            )
        
        console.print(table)
    
    if not lol_markets and not dota_markets:
        console.print("[yellow]No esports markets found[/yellow]")


@app.command()
def live():
    """Show live matches being tracked (interactive)."""
    setup()
    
    from src.esports.pandascore import PandaScoreProvider
    from src.models import Game
    
    async def fetch_live():
        config = get_config()
        provider = PandaScoreProvider(config.esports.pandascore_api_key)
        await provider.connect()
        
        try:
            matches = await provider.get_live_matches()
            return matches
        finally:
            await provider.disconnect()
    
    console.print("[dim]Fetching live matches...[/dim]")
    
    try:
        matches = asyncio.run(fetch_live())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[dim]Make sure PANDASCORE_API_KEY is set in .env[/dim]")
        raise typer.Exit(1)
    
    if not matches:
        console.print("[yellow]No live matches found[/yellow]")
        return
    
    table = Table(title="🔴 Live Esports Matches", box=box.ROUNDED)
    table.add_column("Match ID", style="dim")
    table.add_column("Game", style="cyan")
    table.add_column("Team 1")
    table.add_column("vs", style="dim")
    table.add_column("Team 2")
    table.add_column("Status")
    
    for match in matches:
        opponents = match.get("opponents", [])
        team1 = opponents[0].get("opponent", {}).get("name", "TBD") if len(opponents) > 0 else "TBD"
        team2 = opponents[1].get("opponent", {}).get("name", "TBD") if len(opponents) > 1 else "TBD"
        
        game = match.get("game", Game.LOL)
        game_str = "LoL" if game == Game.LOL else "Dota2"
        
        table.add_row(
            str(match.get("id", ""))[:8],
            game_str,
            team1,
            "vs",
            team2,
            "[green]LIVE[/green]",
        )
    
    console.print(table)
    console.print(f"\n[dim]Found {len(matches)} live matches[/dim]")


@app.command()
def version():
    """Show version information."""
    from src import __version__
    
    console.print(Panel.fit(
        f"[bold]Polymarket Esports Arbitrage Bot[/bold]\n"
        f"Version: {__version__}\n"
        f"Python: {sys.version.split()[0]}",
        border_style="blue",
    ))


if __name__ == "__main__":
    app()




