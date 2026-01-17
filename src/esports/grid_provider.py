"""
GRID.gg Esports Data Provider.

GRID provides official esports data with WebSocket streaming for:
- League of Legends (LCK, LPL, LEC, etc.)
- Dota 2 (The International, DPC)
- CS2, Valorant, and more

This is the FASTEST data source for esports - used by professional traders.
"""

import asyncio
from datetime import datetime
from typing import Optional, Callable, Any, List, Dict
import json

import httpx
import websockets

from src.models import Game, GameState, Team, GameEvent, EventType
from src.config import get_config
from src.logger import get_logger

logger = get_logger("grid")


class GridProvider:
    """
    GRID.gg esports data provider with WebSocket streaming.
    
    GRID is the official data partner for major esports leagues,
    providing the fastest and most accurate live game data.
    """
    
    # API endpoints
    REST_BASE_URL = "https://api.grid.gg"
    WS_BASE_URL = "wss://api.grid.gg/live"
    
    def __init__(self):
        config = get_config()
        self._api_key = config.esports.grid_api_key
        
        if not self._api_key:
            logger.warning("GRID API key not configured - provider disabled")
            self._enabled = False
        else:
            # GRID API endpoints are not working with current key format
            # Disable until we figure out the correct API endpoints
            # The key might be for file downloads only, not live data
            self._enabled = False
            logger.warning("GRID provider disabled - API endpoints need configuration")
            logger.info("GRID provider initialized (disabled - endpoints not configured)")
        
        self._http_client: Optional[httpx.AsyncClient] = None
        self._ws_connection = None
        self._event_callbacks: list[Callable] = []
        self._is_streaming = False
        
        # Cache for match data
        self._live_matches: dict[str, dict] = {}
        self._match_states: dict[str, GameState] = {}
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    async def connect(self) -> None:
        """Initialize HTTP client."""
        if not self._enabled:
            return
            
        # GRID API uses x-api-key header for authentication
        self._http_client = httpx.AsyncClient(
            base_url=self.REST_BASE_URL,
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        logger.info("GRID HTTP client connected")
    
    async def disconnect(self) -> None:
        """Close connections."""
        if self._http_client:
            await self._http_client.aclose()
        if self._ws_connection:
            await self._ws_connection.close()
        self._is_streaming = False
        logger.info("GRID provider disconnected")
    
    async def get_live_matches(self) -> List[Dict]:
        """
        Fetch currently live matches from GRID.
        
        Returns matches with actual team names (not generic like OpenDota).
        """
        if not self._enabled or not self._http_client:
            return []
        
        try:
            # GRID API - Try REST endpoint for live series
            # Based on docs: https://api.grid.gg/file-download/...
            response = await self._http_client.get("/live-data/series")
            
            if response.status_code == 404:
                # Try alternative endpoint
                response = await self._http_client.get("/series?state=live")
            
            if response.status_code != 200:
                # Log more details for debugging
                logger.warning(
                    f"GRID API returned {response.status_code}",
                    extra={"response": response.text[:300] if response.text else "empty"}
                )
                return []
            
            data = response.json()
            # Handle different response formats
            if isinstance(data, list):
                series_list = data
            elif isinstance(data, dict):
                series_list = data.get("data", data.get("series", data.get("nodes", [])))
            
            matches = []
            for series in series_list:
                teams = series.get("teams", [])
                if len(teams) < 2:
                    continue
                
                game_name = series.get("game", {}).get("name", "").lower()
                
                # Determine game type
                if "league" in game_name or "lol" in game_name:
                    game = Game.LOL
                elif "dota" in game_name:
                    game = Game.DOTA2
                else:
                    continue  # Skip non-LoL/Dota games for now
                
                match_data = {
                    "match_id": series.get("id"),
                    "id": series.get("id"),
                    "game": game,
                    "source": "grid",
                    "tournament": series.get("tournament", {}).get("name", ""),
                    "team1": {
                        "id": teams[0].get("id", ""),
                        "name": teams[0].get("name", ""),
                        "short_name": teams[0].get("shortName", ""),
                        "logo_url": teams[0].get("logoUrl", ""),
                    },
                    "team2": {
                        "id": teams[1].get("id", ""),
                        "name": teams[1].get("name", ""),
                        "short_name": teams[1].get("shortName", ""),
                        "logo_url": teams[1].get("logoUrl", ""),
                    },
                }
                
                # Log the match with actual team names
                logger.info(
                    f"🎮 GRID Live: {match_data['team1']['name']} vs {match_data['team2']['name']} "
                    f"({match_data['tournament']})"
                )
                
                matches.append(match_data)
                self._live_matches[series.get("id")] = match_data
            
            return matches
            
        except Exception as e:
            logger.error(f"Error fetching GRID live matches: {e}")
            return []
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """
        Get detailed game state for a match.
        
        GRID provides real-time game state including:
        - Kills, deaths, assists per team
        - Gold difference
        - Objectives (towers, dragons, barons, etc.)
        - Current game time
        """
        if not self._enabled or not self._http_client:
            return None
        
        try:
            # Get match details from GRID REST API
            response = await self._http_client.get(f"/live-data/series/{match_id}")
            
            if response.status_code == 404:
                # Try alternative endpoint
                response = await self._http_client.get(f"/series/{match_id}")
            
            if response.status_code != 200:
                logger.debug(f"GRID match state failed: {response.status_code}")
                return None
            
            data = response.json()
            # Handle different response formats
            if isinstance(data, dict):
                series = data.get("data", data.get("series", data))
            else:
                series = data
            
            if not series:
                return None
            
            teams = series.get("teams", [])
            games = series.get("games", [])
            
            if len(teams) < 2 or not games:
                return None
            
            # Get the current/latest game
            current_game = None
            for game in games:
                if game.get("state") == "LIVE":
                    current_game = game
                    break
            
            if not current_game:
                current_game = games[-1]  # Use latest game
            
            game_teams = current_game.get("teams", [])
            if len(game_teams) < 2:
                return None
            
            # Parse team stats
            team1_stats = game_teams[0].get("score", {})
            team2_stats = game_teams[1].get("score", {})
            
            # Create Team objects
            team1 = Team(
                id=teams[0].get("id", ""),
                name=teams[0].get("name", "Team 1"),
                short_name=teams[0].get("shortName", "T1"),
            )
            
            team2 = Team(
                id=teams[1].get("id", ""),
                name=teams[1].get("name", "Team 2"),
                short_name=teams[1].get("shortName", "T2"),
            )
            
            # Determine game type from cached match data
            cached_match = self._live_matches.get(match_id, {})
            game_type = cached_match.get("game", Game.LOL)
            
            # Create GameState
            game_state = GameState(
                match_id=match_id,
                game=game_type,
                team1=team1,
                team2=team2,
                game_time=current_game.get("clock", {}).get("currentSeconds", 0),
                team1_score=team1_stats.get("kills", 0),
                team2_score=team2_stats.get("kills", 0),
                team1_gold=team1_stats.get("gold", 0),
                team2_gold=team2_stats.get("gold", 0),
                team1_towers=team1_stats.get("towers", 0),
                team2_towers=team2_stats.get("towers", 0),
                team1_dragons=team1_stats.get("dragons", 0),
                team2_dragons=team2_stats.get("dragons", 0),
                team1_barons=team1_stats.get("barons", 0),
                team2_barons=team2_stats.get("barons", 0),
                status="live",
                source="grid",
            )
            
            self._match_states[match_id] = game_state
            return game_state
            
        except Exception as e:
            logger.error(f"Error fetching GRID match state: {e}")
            return None
    
    async def start_websocket_stream(self, match_id: str, callback: Callable) -> None:
        """
        Start WebSocket streaming for real-time events.
        
        This is the FASTEST way to get game events - sub-second latency!
        """
        if not self._enabled:
            return
        
        self._event_callbacks.append(callback)
        
        try:
            ws_url = f"{self.WS_BASE_URL}?token={self._api_key}"
            
            async with websockets.connect(ws_url) as ws:
                self._ws_connection = ws
                self._is_streaming = True
                
                # Subscribe to match events
                subscribe_msg = {
                    "type": "subscribe",
                    "seriesId": match_id,
                }
                await ws.send(json.dumps(subscribe_msg))
                
                logger.info(f"📡 GRID WebSocket streaming started for match {match_id}")
                
                while self._is_streaming:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        event_data = json.loads(message)
                        
                        # Parse and dispatch event
                        event = self._parse_event(event_data, match_id)
                        if event:
                            for cb in self._event_callbacks:
                                await cb(event)
                                
                    except asyncio.TimeoutError:
                        # Send heartbeat
                        await ws.ping()
                    except Exception as e:
                        logger.error(f"WebSocket error: {e}")
                        break
                        
        except Exception as e:
            logger.error(f"Failed to start GRID WebSocket: {e}")
        finally:
            self._is_streaming = False
    
    def _parse_event(self, data: dict, match_id: str) -> Optional[GameEvent]:
        """Parse GRID WebSocket event into GameEvent."""
        try:
            event_type = data.get("type", "")
            
            # Map GRID event types to our EventType
            type_mapping = {
                "kill": EventType.KILL,
                "tower_destroyed": EventType.TOWER,
                "dragon_killed": EventType.DRAGON,
                "baron_killed": EventType.BARON,
                "inhibitor_destroyed": EventType.INHIBITOR,
                "game_end": EventType.GAME_END,
            }
            
            our_type = type_mapping.get(event_type)
            if not our_type:
                return None
            
            return GameEvent(
                match_id=match_id,
                event_type=our_type,
                team=data.get("team", ""),
                player=data.get("player", ""),
                timestamp=datetime.now(),
                details=data,
            )
            
        except Exception as e:
            logger.error(f"Error parsing GRID event: {e}")
            return None
    
    async def stop_websocket_stream(self) -> None:
        """Stop WebSocket streaming."""
        self._is_streaming = False
        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None
        logger.info("GRID WebSocket streaming stopped")
