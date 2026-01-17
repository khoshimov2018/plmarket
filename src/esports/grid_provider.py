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
    
    # API endpoints - GRID uses api.grid.gg
    GRAPHQL_URL = "https://api.grid.gg/central-data/graphql"
    REST_BASE_URL = "https://api.grid.gg"
    FILE_DOWNLOAD_URL = "https://api.grid.gg/file-download"
    
    def __init__(self):
        config = get_config()
        self._api_key = config.esports.grid_api_key
        
        if not self._api_key:
            logger.warning("GRID API key not configured - provider disabled")
            self._enabled = False
        else:
            # GRID Open Access API is now properly configured!
            self._enabled = True
            logger.info(f"✅ GRID Open Access provider enabled with API key")
        
        self._http_client: Optional[httpx.AsyncClient] = None
        self._ws_connection = None
        self._event_callbacks: List[Callable] = []
        self._is_streaming = False
        
        # Cache for match data
        self._live_matches: Dict[str, dict] = {}
        self._match_states: Dict[str, GameState] = {}
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    async def connect(self) -> None:
        """Initialize HTTP client."""
        if not self._enabled:
            return
            
        # GRID Open Access API uses x-api-key header for authentication
        self._http_client = httpx.AsyncClient(
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        logger.info("✅ GRID Open Access HTTP client connected")
    
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
        Fetch currently live matches from GRID using GraphQL.
        
        Returns matches with actual team names (not generic like OpenDota).
        """
        if not self._enabled or not self._http_client:
            return []
        
        try:
            # GraphQL query for recent esports series
            # Based on GRID docs - get series from last 24 hours
            # Note: GRID uses String type for dates, not DateTime
            from datetime import datetime, timedelta
            
            now = datetime.utcnow()
            start_time = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_time = (now + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            query = """
            query GetRecentSeries($startTime: String, $endTime: String) {
                allSeries(
                    first: 50
                    filter: {
                        startTimeScheduled: {
                            gte: $startTime
                            lte: $endTime
                        }
                    }
                    orderBy: StartTimeScheduled
                    orderDirection: DESC
                ) {
                    totalCount
                    edges {
                        node {
                            id
                            startTimeScheduled
                            format {
                                name
                                nameShortened
                            }
                            tournament {
                                name
                                nameShortened
                            }
                            teams {
                                baseInfo {
                                    name
                                }
                                scoreAdvantage
                            }
                            title {
                                name
                                nameShortened
                            }
                        }
                    }
                }
            }
            """
            
            response = await self._http_client.post(
                self.GRAPHQL_URL,
                json={
                    "query": query,
                    "variables": {
                        "startTime": start_time,
                        "endTime": end_time
                    }
                }
            )
            
            if response.status_code != 200:
                logger.warning(f"GRID GraphQL returned {response.status_code}: {response.text[:200]}")
                return []
            
            data = response.json()
            
            if "errors" in data:
                logger.error(f"GRID GraphQL errors: {data['errors']}")
                return []
            
            edges = data.get("data", {}).get("allSeries", {}).get("edges", [])
            total_count = data.get("data", {}).get("allSeries", {}).get("totalCount", 0)
            
            logger.debug(f"GRID returned {total_count} series in time window")
            
            matches = []
            for edge in edges:
                series = edge.get("node", {})
                teams = series.get("teams", [])
                
                if len(teams) < 2:
                    continue
                
                # Get team names from baseInfo (per GRID schema)
                team1_info = teams[0].get("baseInfo", {}) or {}
                team2_info = teams[1].get("baseInfo", {}) or {}
                team1_name = team1_info.get("name", "")
                team2_name = team2_info.get("name", "")
                
                # Skip if no team names
                if not team1_name or not team2_name:
                    continue
                
                # Get game title
                title = series.get("title", {}) or {}
                game_name = (title.get("name", "") or title.get("nameShortened", "") or "").lower()
                
                # Determine game type
                if "league" in game_name or "lol" in game_name:
                    game = Game.LOL
                elif "dota" in game_name:
                    game = Game.DOTA2
                elif "cs" in game_name or "counter" in game_name:
                    continue  # Skip CS2 for now
                else:
                    continue  # Skip other games
                
                tournament = series.get("tournament", {}) or {}
                tournament_name = tournament.get("name", "") or tournament.get("nameShortened", "")
                
                match_data = {
                    "match_id": series.get("id"),
                    "id": series.get("id"),
                    "game": game,
                    "source": "grid",
                    "tournament": tournament_name,
                    "team1": {
                        "id": str(series.get("id", "")),
                        "name": team1_name,
                    },
                    "team2": {
                        "id": str(series.get("id", "")),
                        "name": team2_name,
                    },
                    "team1_name": team1_name,
                    "team2_name": team2_name,
                    "start_time": series.get("startTimeScheduled"),
                }
                
                # Log the match with actual team names
                logger.info(
                    f"🚀 GRID: {team1_name} vs {team2_name} ({tournament_name}) - {game_name}"
                )
                
                matches.append(match_data)
                self._live_matches[series.get("id")] = match_data
            
            if matches:
                logger.info(f"🎮 Found {len(matches)} matches from GRID (FASTEST source!)")
            
            return matches
            
        except Exception as e:
            logger.error(f"Error fetching GRID live matches: {e}")
            return []
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """
        Get detailed LIVE game state using seriesState query.
        
        GRID's seriesState provides real-time data including:
        - Player kills, deaths, net worth
        - Team scores
        - Game progress (started/finished)
        """
        if not self._enabled or not self._http_client:
            return None
        
        try:
            # Use seriesState for LIVE data (per GRID docs)
            query = """
            query GetLiveSeriesState($seriesId: ID!) {
                seriesState(id: $seriesId) {
                    valid
                    updatedAt
                    format
                    started
                    finished
                    teams {
                        name
                        won
                    }
                    games(filter: { started: true }) {
                        sequenceNumber
                        finished
                        teams {
                            name
                            players {
                                name
                                kills
                                deaths
                                netWorth
                            }
                        }
                    }
                }
            }
            """
            
            response = await self._http_client.post(
                self.GRAPHQL_URL,
                json={
                    "query": query,
                    "variables": {"seriesId": match_id}
                }
            )
            
            if response.status_code != 200:
                logger.debug(f"GRID seriesState failed: {response.status_code}")
                return None
            
            data = response.json()
            
            if "errors" in data:
                logger.debug(f"GRID GraphQL errors: {data['errors']}")
                return None
            
            series_state = data.get("data", {}).get("seriesState")
            if not series_state or not series_state.get("valid"):
                logger.debug(f"GRID seriesState not valid for {match_id}")
                return None
            
            teams = series_state.get("teams", [])
            games = series_state.get("games", [])
            
            if len(teams) < 2:
                return None
            
            # Get the current/latest game (started but not finished)
            current_game = None
            for game in games:
                if not game.get("finished"):
                    current_game = game
                    break
            
            if not current_game and games:
                current_game = games[-1]  # Use latest game
            
            # Calculate team stats from players
            team1_kills = 0
            team2_kills = 0
            team1_gold = 0
            team2_gold = 0
            
            if current_game:
                game_teams = current_game.get("teams", [])
                if len(game_teams) >= 2:
                    # Sum up player stats for each team
                    for player in game_teams[0].get("players", []):
                        team1_kills += player.get("kills", 0) or 0
                        team1_gold += player.get("netWorth", 0) or 0
                    
                    for player in game_teams[1].get("players", []):
                        team2_kills += player.get("kills", 0) or 0
                        team2_gold += player.get("netWorth", 0) or 0
            
            # Create Team objects
            team1 = Team(
                id=str(match_id),
                name=teams[0].get("name", "Team 1"),
                tag="",
            )
            
            team2 = Team(
                id=str(match_id),
                name=teams[1].get("name", "Team 2"),
                tag="",
            )
            
            # Determine game type from cached match data
            cached_match = self._live_matches.get(match_id, {})
            game_type = cached_match.get("game", Game.DOTA2)
            
            # Count series score (games won)
            team1_series = 1 if teams[0].get("won") else 0
            team2_series = 1 if teams[1].get("won") else 0
            
            # Create GameState with LIVE data
            game_state = GameState(
                match_id=match_id,
                game=game_type,
                team1=team1,
                team2=team2,
                game_number=len([g for g in games if not g.get("finished")]) or 1,
                game_time_seconds=0.0,
                team1_kills=team1_kills,
                team2_kills=team2_kills,
                team1_gold=team1_gold,
                team2_gold=team2_gold,
                team1_towers=0,
                team2_towers=0,
                team1_series_score=team1_series,
                team2_series_score=team2_series,
            )
            
            logger.info(
                f"🎮 GRID LIVE: {team1.name} vs {team2.name}, "
                f"kills={team1_kills}-{team2_kills}, "
                f"gold={team1_gold}-{team2_gold}"
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
