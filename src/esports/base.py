"""
Base class for esports data providers.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, List, Dict
from datetime import datetime

from src.models import Game, GameState, GameEvent, Team, MatchStatus


class BaseEsportsProvider(ABC):
    """Abstract base class for esports data providers."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._is_connected = False
    
    @property
    @abstractmethod
    def supported_games(self) -> List[Game]:
        """Return list of games this provider supports."""
        pass
    
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the data source."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the data source."""
        pass
    
    @abstractmethod
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """
        Get all currently live matches.
        
        Args:
            game: Optional filter for specific game
            
        Returns:
            List of live match information
        """
        pass
    
    @abstractmethod
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """
        Get current state of a specific match.
        
        Args:
            match_id: Unique match identifier
            
        Returns:
            Current game state or None if match not found
        """
        pass
    
    @abstractmethod
    async def subscribe_to_match(self, match_id: str) -> AsyncIterator[GameEvent]:
        """
        Subscribe to real-time events for a match.
        
        Args:
            match_id: Unique match identifier
            
        Yields:
            Game events as they occur
        """
        pass
    
    @abstractmethod
    async def get_upcoming_matches(
        self, 
        game: Optional[Game] = None,
        hours_ahead: int = 24
    ) -> List[Dict]:
        """
        Get upcoming scheduled matches.
        
        Args:
            game: Optional filter for specific game
            hours_ahead: How far ahead to look
            
        Returns:
            List of upcoming match information
        """
        pass
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
    
    @staticmethod
    def parse_team(team_data: dict) -> Team:
        """Parse team data into Team model."""
        return Team(
            id=str(team_data.get("id", "")),
            name=team_data.get("name", "Unknown"),
            short_name=team_data.get("acronym", team_data.get("short_name", "")),
            logo_url=team_data.get("image_url"),
        )
    
    @staticmethod
    def calculate_game_duration(start_time: datetime) -> float:
        """Calculate game duration in seconds from start time."""
        if start_time is None:
            return 0.0
        delta = datetime.utcnow() - start_time
        return max(0.0, delta.total_seconds())




