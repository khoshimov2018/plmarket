# 🎮 Polymarket Esports Arbitrage Bot

A high-frequency trading bot that exploits latency between live esports game events and Polymarket price updates in League of Legends and Dota 2 markets.

## The Strategy

> "It started with $900, quietly deployed on Polymarket. Three months later, the account was up $208,521."

This bot's strategy is built around exploiting the time lag between:
1. **Live game events** (kills, objectives, tower destruction)
2. **Market price updates** on Polymarket

When a significant in-game event occurs (like a Baron/Roshan kill or team wipe), the probability of the leading team winning changes instantly. But the market takes seconds to react. This delay is the edge.

### How It Works

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Live Game API  │────▶│  Win Probability │────▶│  Compare with   │
│  (PandaScore)   │     │  Model           │     │  Market Odds    │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Execute Trade  │◀────│  Risk Check      │◀────│  Opportunity    │
│  on Polymarket  │     │  (size, limits)  │     │  Detected!      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

1. **Monitor Live Matches**: Poll esports data providers for real-time game state
2. **Calculate Win Probability**: Use gold lead, kills, towers, and game phase to estimate true win probability
3. **Detect Mispricing**: Compare our model's probability to Polymarket's current odds
4. **Execute Quickly**: Place trades before the market catches up
5. **Manage Risk**: Position sizing, stop losses, and daily limits

## Features

- 🎯 **Real-time game monitoring** for LoL and Dota 2
- 📊 **Win probability model** based on gold, kills, objectives, and game phase
- ⚡ **Low-latency execution** on Polymarket
- 🛡️ **Risk management** with stop losses, position limits, and daily caps
- 📈 **Paper trading mode** for testing strategies
- 💾 **Trade history** with SQLite persistence
- 📱 **CLI interface** with rich terminal output

## Installation

### Prerequisites

- Python 3.11+
- [PandaScore API key](https://pandascore.co/) (for esports data)
- Polymarket account with API credentials

### Setup

1. **Clone and install dependencies:**

```bash
cd polymarket
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. **Configure environment:**

```bash
cp env.example .env
```

Edit `.env` with your credentials:

```env
# Required
POLYMARKET_PRIVATE_KEY=0x...
PANDASCORE_API_KEY=your_pandascore_key

# For live trading (optional)
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
```

3. **Test the setup:**

```bash
python main.py live    # Check live matches
python main.py markets # Check available markets
```

## Usage

### Start the Bot

```bash
# Paper trading (default, safe mode)
python main.py run

# Live trading (real money!)
python main.py run --live

# With debug logging
python main.py run --debug
```

### Other Commands

```bash
# Show current status and P&L
python main.py status

# View trade history
python main.py history
python main.py history --limit 50 --game lol

# Show configuration
python main.py config

# Check live matches
python main.py live

# List esports markets
python main.py markets
```

## Configuration

Key parameters in `.env`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `INITIAL_CAPITAL` | 900 | Starting capital in USDC |
| `MIN_EDGE_THRESHOLD` | 0.02 | Minimum edge (2%) to trade |
| `MAX_POSITION_SIZE_PCT` | 0.10 | Max 10% of capital per trade |
| `MAX_SLIPPAGE` | 0.01 | Maximum 1% slippage |
| `STOP_LOSS_PCT` | 0.05 | 5% stop loss |
| `TAKE_PROFIT_PCT` | 0.10 | 10% take profit |
| `MAX_CONCURRENT_POSITIONS` | 5 | Max open positions |
| `DAILY_LOSS_LIMIT_PCT` | 0.15 | Stop trading after 15% daily loss |

## Win Probability Model

The bot uses a multi-factor model to estimate win probability:

### League of Legends

```python
factors = {
    "gold_lead": weight based on game phase (early: 0.15, mid: 0.25, late: 0.35),
    "kill_lead": 0.8% per kill,
    "tower_lead": 3% per tower,
    "objectives": Baron (8%), Dragon Soul (5%), Elder (10%)
}
```

### Dota 2

```python
factors = {
    "gold_lead": lower weight due to comeback mechanics,
    "kill_lead": 0.5% per kill (lower than LoL),
    "tower_lead": 2.5% per tower,
    "roshan": 6% for Aegis,
    "high_ground_defense": bonus for defending team
}
```

## Architecture

```
polymarket/
├── main.py                 # CLI entry point
├── src/
│   ├── config.py          # Configuration management
│   ├── models.py          # Data models
│   ├── logger.py          # Structured logging
│   ├── database.py        # Trade history persistence
│   ├── esports/           # Game data providers
│   │   ├── base.py        # Abstract provider
│   │   ├── pandascore.py  # PandaScore API client
│   │   ├── lol_provider.py # LoL-specific logic
│   │   └── dota_provider.py # Dota 2-specific logic
│   ├── trading/           # Polymarket integration
│   │   ├── polymarket_client.py # CLOB API client
│   │   ├── order_manager.py     # Order lifecycle
│   │   └── position_tracker.py  # Position/P&L tracking
│   └── engine/            # Core trading engine
│       ├── arbitrage_detector.py # Opportunity detection
│       ├── market_matcher.py     # Match↔Market mapping
│       └── execution_engine.py   # Main orchestrator
└── data/                  # SQLite database
```

## Risk Warning

⚠️ **This bot trades with real money. Use at your own risk.**

- Start with paper trading to understand the system
- The edge may be smaller than expected due to competition
- Esports markets have unique risks (match fixing, technical issues)
- Market liquidity varies significantly by match
- Past performance does not guarantee future results

## Improving the Bot

### Better Data Sources

1. **GRID API**: Official data partner for many tournaments
2. **Stream parsing**: Extract events from Twitch streams (adds latency)
3. **Multiple providers**: Cross-reference for reliability

### Model Improvements

1. **Team-specific models**: Different teams have different playstyles
2. **Historical analysis**: Train on past matches
3. **Player statistics**: Individual player impact
4. **Draft analysis**: Team composition effects

### Execution Optimization

1. **Websocket connections**: Lower latency than polling
2. **Co-location**: Reduce network latency
3. **Order book analysis**: Better price discovery
4. **Multi-account**: Scale position sizes

## License

MIT License - Use freely, trade responsibly.

## Acknowledgments

- [PandaScore](https://pandascore.co/) for esports data
- [Polymarket](https://polymarket.com/) for prediction markets
- The original TeemuTeemuTeemu bot for inspiration




