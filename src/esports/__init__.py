"""
Esports data providers for live game state monitoring.

Data Source Priority (for latency arbitrage):
1. LoLEsportsProvider - Official Riot data (fastest for LoL)
2. OpenDotaProvider - Free API (fastest for Dota 2)
3. PandaScoreProvider - Paid API (fallback)
"""

from src.esports.base import BaseEsportsProvider
from src.esports.pandascore import PandaScoreProvider
from src.esports.lol_provider import LoLDataProvider
from src.esports.dota_provider import DotaDataProvider
from src.esports.opendota import OpenDotaProvider
from src.esports.lolesports import LoLEsportsProvider

__all__ = [
    "BaseEsportsProvider",
    "PandaScoreProvider", 
    "LoLDataProvider",
    "DotaDataProvider",
    "OpenDotaProvider",
    "LoLEsportsProvider",
]




