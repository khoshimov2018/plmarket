#!/usr/bin/env python3
"""
Live monitoring dashboard for the Polymarket Esports Arbitrage Bot.
Uses Rich for beautiful terminal UI.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config import get_config
from src.database import get_database
from src.logger import setup_logging


console = Console()


class Dashboard:
    """Real-time monitoring dashboard."""
    
    def __init__(self):
        setup_logging()
        self.config = get_config()
        self.db = get_database()
        
        self.start_time = datetime.utcnow()
        self.refresh_count = 0
        
        # Simulated metrics (would come from actual bot in production)
        self.live_matches = []
        self.recent_trades = []
        self.open_positions = []
        
    def make_layout(self) -> Layout:
        """Create the dashboard layout."""
        layout = Layout(name="root")
        
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3),
        )
        
        layout["main"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=1),
        )
        
        layout["left"].split(
            Layout(name="stats", size=12),
            Layout(name="trades", ratio=1),
        )
        
        layout["right"].split(
            Layout(name="positions", ratio=1),
            Layout(name="matches", ratio=1),
        )
        
        return layout
    
    def generate_header(self) -> Panel:
        """Generate header panel."""
        runtime = datetime.utcnow() - self.start_time
        hours = runtime.total_seconds() / 3600
        
        mode = "📝 PAPER" if self.config.development.paper_trading else "🔴 LIVE"
        
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)
        
        grid.add_row(
            f"[bold]🎮 Polymarket Esports Bot[/bold]",
            f"[yellow]{mode}[/yellow]",
            f"⏱️ {hours:.1f}h | 🔄 {self.refresh_count}",
        )
        
        return Panel(grid, style="white on dark_blue", height=3)
    
    def generate_stats(self) -> Panel:
        """Generate statistics panel."""
        summary = self.db.get_performance_summary()
        today = self.db.get_daily_stats() or {}
        
        # Main stats table
        stats_table = Table(show_header=False, box=None, padding=(0, 2))
        stats_table.add_column("Label", style="dim")
        stats_table.add_column("Value", justify="right")
        stats_table.add_column("Label2", style="dim")
        stats_table.add_column("Value2", justify="right")
        
        total_pnl = summary.get("total_pnl", 0)
        pnl_color = "green" if total_pnl >= 0 else "red"
        
        today_pnl = today.get("net_pnl", 0)
        today_color = "green" if today_pnl >= 0 else "red"
        
        stats_table.add_row(
            "Total P&L", f"[{pnl_color}]${total_pnl:+,.2f}[/{pnl_color}]",
            "Today P&L", f"[{today_color}]${today_pnl:+,.2f}[/{today_color}]",
        )
        stats_table.add_row(
            "Total Trades", str(summary.get("total_trades", 0)),
            "Today Trades", str(today.get("total_trades", 0)),
        )
        stats_table.add_row(
            "Win Rate", f"{summary.get('win_rate', 0):.1%}",
            "Today Win Rate", f"{today.get('win_rate', 0):.1%}",
        )
        stats_table.add_row(
            "Avg P&L/Trade", f"${summary.get('avg_pnl_per_trade', 0):.2f}",
            "Volume", f"${summary.get('total_volume', 0):,.0f}",
        )
        
        # Capital tracking
        initial = self.config.trading.initial_capital
        current = initial + total_pnl
        roi = (current - initial) / initial * 100
        roi_color = "green" if roi >= 0 else "red"
        
        capital_text = (
            f"\n💰 Capital: ${initial:,.0f} → [{roi_color}]${current:,.2f}[/{roi_color}] "
            f"([{roi_color}]{roi:+.1f}%[/{roi_color}])"
        )
        
        return Panel(
            Text.from_markup(str(stats_table) + capital_text),
            title="📊 Performance",
            border_style="green",
        )
    
    def generate_trades(self) -> Panel:
        """Generate recent trades panel."""
        trades = self.db.get_trades(limit=10)
        
        table = Table(box=box.SIMPLE, expand=True)
        table.add_column("Time", style="dim", width=12)
        table.add_column("Game", width=5)
        table.add_column("Side", width=5)
        table.add_column("P&L", justify="right", width=10)
        table.add_column("Edge", justify="right", width=8)
        table.add_column("Hold", justify="right", width=8)
        
        if not trades:
            table.add_row("", "[dim]No trades yet[/dim]", "", "", "", "")
        else:
            for trade in trades:
                pnl = trade["net_pnl"]
                pnl_color = "green" if pnl >= 0 else "red"
                
                hold = trade["hold_duration"]
                hold_str = f"{hold:.0f}s" if hold < 60 else f"{hold/60:.1f}m"
                
                time_str = trade["exit_time"].strftime("%H:%M:%S") if trade["exit_time"] else ""
                
                table.add_row(
                    time_str,
                    trade["game"][:3].upper(),
                    trade["side"][:1].upper(),
                    f"[{pnl_color}]${pnl:+.2f}[/{pnl_color}]",
                    "2.1%",  # Would come from actual trade
                    hold_str,
                )
        
        return Panel(table, title="📜 Recent Trades", border_style="blue")
    
    def generate_positions(self) -> Panel:
        """Generate open positions panel."""
        table = Table(box=box.SIMPLE, expand=True)
        table.add_column("Market", width=20)
        table.add_column("Side", width=5)
        table.add_column("Size", justify="right", width=8)
        table.add_column("P&L", justify="right", width=10)
        
        if not self.open_positions:
            table.add_row("[dim]No open positions[/dim]", "", "", "")
        else:
            for pos in self.open_positions:
                pnl = pos.get("pnl", 0)
                pnl_color = "green" if pnl >= 0 else "red"
                
                table.add_row(
                    pos.get("market", "")[:18],
                    pos.get("side", "")[:1].upper(),
                    f"${pos.get('size', 0):.0f}",
                    f"[{pnl_color}]${pnl:+.2f}[/{pnl_color}]",
                )
        
        return Panel(table, title="📍 Open Positions", border_style="yellow")
    
    def generate_matches(self) -> Panel:
        """Generate live matches panel."""
        table = Table(box=box.SIMPLE, expand=True)
        table.add_column("Game", width=5)
        table.add_column("Match", width=20)
        table.add_column("Status", width=10)
        
        if not self.live_matches:
            table.add_row("[dim]Scanning...[/dim]", "", "")
        else:
            for match in self.live_matches[:5]:
                table.add_row(
                    match.get("game", "")[:3],
                    match.get("teams", "")[:18],
                    "[green]●[/green] LIVE",
                )
        
        return Panel(table, title="🎮 Live Matches", border_style="cyan")
    
    def generate_footer(self) -> Panel:
        """Generate footer panel."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        grid = Table.grid(expand=True)
        grid.add_column(justify="left")
        grid.add_column(justify="center")
        grid.add_column(justify="right")
        
        grid.add_row(
            f"[dim]Last update: {now}[/dim]",
            "[dim]Press Ctrl+C to exit[/dim]",
            f"[dim]Edge: ≥{self.config.trading.min_edge_threshold:.1%} | Max: {self.config.trading.max_position_size_pct:.0%}[/dim]",
        )
        
        return Panel(grid, style="dim", height=3)
    
    async def run(self):
        """Run the dashboard."""
        layout = self.make_layout()
        
        with Live(layout, refresh_per_second=2, screen=True):
            while True:
                # Update panels
                layout["header"].update(self.generate_header())
                layout["stats"].update(self.generate_stats())
                layout["trades"].update(self.generate_trades())
                layout["positions"].update(self.generate_positions())
                layout["matches"].update(self.generate_matches())
                layout["footer"].update(self.generate_footer())
                
                self.refresh_count += 1
                
                await asyncio.sleep(1)


def main():
    """Entry point for the dashboard."""
    console.print("[bold]Starting dashboard...[/bold]")
    
    dashboard = Dashboard()
    
    try:
        asyncio.run(dashboard.run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped[/yellow]")


if __name__ == "__main__":
    main()




