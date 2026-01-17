"""
Esports data providers for live game state monitoring.

Data Source Priority (for latency arbitrage):
1. GridProvider - GRID.gg (FASTEST - paid, WebSocket streaming)
2. LoLEsportsProvider - Official Riot data (fast for LoL)
3. OpenDotaProvider - Free API (fast for Dota 2)
4. PandaScoreProvider - Paid API (fallback)
"""

from src.esports.base import BaseEsportsProvider
from src.esports.pandascore import PandaScoreProvider
from src.esports.lol_provider import LoLDataProvider
from src.esports.dota_provider import DotaDataProvider
from src.esports.opendota import OpenDotaProvider
from src.esports.lolesports import LoLEsportsProvider
from src.esports.grid_provider import GridProvider

__all__ = [
    "BaseEsportsProvider",
    "GridProvider",
    "PandaScoreProvider", 
    "LoLDataProvider",
    "DotaDataProvider",
    "OpenDotaProvider",
    "LoLEsportsProvider",
]




