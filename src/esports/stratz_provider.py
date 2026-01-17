"""
Stratz API provider for real-time Dota 2 match data.
https://stratz.com - FREE API with live match data and win probabilities.

This is a KEY alternative to PandaScore for Dota 2 data.
Stratz provides:
- Live match data during pro games
- Win probability calculations
- Detailed game state (kills, gold, net worth)
- GraphQL API for flexible queries
"""

import asyncio
from datetime import datetime
from typing import AsyncIterator, Optional, List, Dict, Any
import aiohttp

from src.models import Game, GameState, GameEvent, Team
from src.esports.base import BaseEsportsProvider
from src.logger import get_logger


logger = get_logger("stratz")


class StratzProvider(BaseEsportsProvider):
    """
    Stratz API client for real-time Dota 2 data.
    
    Key advantages:
    - FREE with generous rate limits
    - Real-time match data during pro games
    - Built-in win probability calculations
    - Detailed game state (kills, gold, net worth, towers)
    - GraphQL API for efficient queries
    """
    
    # GraphQL endpoint
    GRAPHQL_URL = "https://api.stratz.com/graphql"
    
    # REST endpoints (alternative)
    REST_URL = "https://api.stratz.com/api/v1"
    
    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self._session: Optional[aiohttp.ClientSession] = None
        self._tracked_matches: Dict[str, dict] = {}
        self._last_states: Dict[str, GameState] = {}
    
    @property
    def supported_games(self) -> List[Game]:
        return [Game.DOTA2]
    
    async def connect(self) -> None:
        """Initialize HTTP session."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Add API key if provided (for higher rate limits)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15)
        )
        self._is_connected = True
        logger.info("Connected to Stratz API")
    
    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._is_connected = False
        logger.info("Disconnected from Stratz API")
    
    async def _graphql_query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute GraphQL query."""
        if not self._session:
            raise RuntimeError("Not connected to Stratz API")
        
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        
        async with self._session.post(self.GRAPHQL_URL, json=payload) as response:
            if response.status != 200:
                text = await response.text()
                logger.error(f"Stratz API error: {response.status} - {text}")
                response.raise_for_status()
            
            data = await response.json()
            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
            
            return data.get("data", {})
    
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """
        Get currently live professional Dota 2 matches.
        
        Uses Stratz's live match query to find ongoing pro games.
        """
        try:
            # GraphQL query for live matches
            query = """
            query {
                live {
                    matches {
                        matchId
                        gameTime
                        radiantTeam {
                            id
                            name
                            tag
                        }
                        direTeam {
                            id
                            name
                            tag
                        }
                        radiantScore
                        direScore
                        league {
                            id
                            displayName
                            tier
                        }
                        players {
                            steamAccountId
                            heroId
                            isRadiant
                            networth
                            kills
                            deaths
                            assists
                        }
                        buildingState
                        winRateValues
                    }
                }
            }
            """
            
            data = await self._graphql_query(query)
            
            live_matches = []
            matches = data.get("live", {}).get("matches", [])
            
            for match in matches:
                # Only include matches with real team names
                radiant_team = match.get("radiantTeam") or {}
                dire_team = match.get("direTeam") or {}
                
                radiant_name = radiant_team.get("name", "")
                dire_name = dire_team.get("name", "")
                
                # Skip pub games (no team names)
                if not radiant_name or not dire_name:
                    continue
                
                # Skip if generic names
                if radiant_name in ["Radiant", "Team Radiant", ""] or \
                   dire_name in ["Dire", "Team Dire", ""]:
                    continue
                
                match_data = {
                    "match_id": str(match.get("matchId", "")),
                    "id": str(match.get("matchId", "")),
                    "game": Game.DOTA2,
                    "source": "stratz",
                    "team1": {
                        "id": str(radiant_team.get("id", "")),
                        "name": radiant_name,
                        "tag": radiant_team.get("tag", ""),
                    },
                    "team2": {
                        "id": str(dire_team.get("id", "")),
                        "name": dire_name,
                        "tag": dire_team.get("tag", ""),
                    },
                    "team1_name": radiant_name,
                    "team2_name": dire_name,
                    "league": match.get("league", {}).get("displayName", ""),
                    "league_tier": match.get("league", {}).get("tier", 0),
                    # Store full match data for get_match_state
                    "_raw": match,
                }
                
                live_matches.append(match_data)
                self._tracked_matches[str(match.get("matchId", ""))] = match
            
            logger.debug(f"Found {len(live_matches)} live Dota 2 matches from Stratz")
            return live_matches
            
        except Exception as e:
            logger.error(f"Error fetching live matches from Stratz: {e}")
            return []
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """
        Get current state of a live match.
        
        Stratz provides real-time data including:
        - Kills, gold, net worth
        - Tower status
        - Win probability
        """
        try:
            # First check if we have cached data
            if match_id in self._tracked_matches:
                match = self._tracked_matches[match_id]
                return self._parse_match_state(match)
            
            # Otherwise query for the match
            query = """
            query($matchId: Long!) {
                live {
                    match(id: $matchId) {
                        matchId
                        gameTime
                        radiantTeam {
                            id
                            name
                            tag
                        }
                        direTeam {
                            id
                            name
                            tag
                        }
                        radiantScore
                        direScore
                        league {
                            id
                            displayName
                        }
                        players {
                            steamAccountId
                            heroId
                            isRadiant
                            networth
                            kills
                            deaths
                            assists
                            goldPerMinute
                            experiencePerMinute
                        }
                        buildingState
                        winRateValues
                    }
                }
            }
            """
            
            data = await self._graphql_query(query, {"matchId": int(match_id)})
            match = data.get("live", {}).get("match")
            
            if not match:
                logger.debug(f"Match {match_id} not found in Stratz live data")
                return None
            
            self._tracked_matches[match_id] = match
            return self._parse_match_state(match)
            
        except Exception as e:
            logger.error(f"Error fetching match state from Stratz: {e}")
            return None
    
    def _parse_match_state(self, match: Dict) -> Optional[GameState]:
        """Parse Stratz match data into GameState."""
        try:
            radiant_team = match.get("radiantTeam") or {}
            dire_team = match.get("direTeam") or {}
            
            team1 = Team(
                id=str(radiant_team.get("id", "")),
                name=radiant_team.get("name", "Radiant"),
                tag=radiant_team.get("tag", ""),
            )
            team2 = Team(
                id=str(dire_team.get("id", "")),
                name=dire_team.get("name", "Dire"),
                tag=dire_team.get("tag", ""),
            )
            
            # Calculate team stats from players
            players = match.get("players", [])
            
            radiant_kills = 0
            dire_kills = 0
            radiant_gold = 0
            dire_gold = 0
            
            for player in players:
                if player.get("isRadiant"):
                    radiant_kills += player.get("kills", 0)
                    radiant_gold += player.get("networth", 0)
                else:
                    dire_kills += player.get("kills", 0)
                    dire_gold += player.get("networth", 0)
            
            # Also use score if available (more accurate)
            radiant_kills = match.get("radiantScore", radiant_kills) or radiant_kills
            dire_kills = match.get("direScore", dire_kills) or dire_kills
            
            # Parse building state for towers
            building_state = match.get("buildingState", 0)
            radiant_towers, dire_towers = self._parse_building_state(building_state)
            
            # Get win probability from Stratz (they calculate it!)
            win_rate_values = match.get("winRateValues", [])
            win_probability = 0.5
            if win_rate_values and len(win_rate_values) > 0:
                # Last value is most recent
                win_probability = win_rate_values[-1] / 100.0 if win_rate_values[-1] else 0.5
            
            game_time = match.get("gameTime", 0) or 0
            
            state = GameState(
                match_id=str(match.get("matchId", "")),
                game=Game.DOTA2,
                team1=team1,
                team2=team2,
                game_number=1,
                game_time_seconds=float(game_time),
                team1_kills=radiant_kills,
                team2_kills=dire_kills,
                team1_gold=radiant_gold,
                team2_gold=dire_gold,
                team1_towers=11 - radiant_towers,  # Towers destroyed
                team2_towers=11 - dire_towers,
                team1_win_probability=win_probability,
            )
            
            logger.debug(
                f"Stratz match state: {team1.name} vs {team2.name}, "
                f"kills={radiant_kills}-{dire_kills}, "
                f"gold={radiant_gold}-{dire_gold}, "
                f"win_prob={win_probability:.1%}"
            )
            
            return state
            
        except Exception as e:
            logger.error(f"Error parsing Stratz match state: {e}")
            return None
    
    def _parse_building_state(self, building_state: int) -> tuple:
        """
        Parse Dota 2 building state bitmask.
        
        Returns (radiant_towers_standing, dire_towers_standing)
        """
        # Building state is a bitmask
        # Bits 0-10: Radiant buildings
        # Bits 11-21: Dire buildings
        
        radiant_towers = bin(building_state & 0x7FF).count('1')  # First 11 bits
        dire_towers = bin((building_state >> 11) & 0x7FF).count('1')  # Next 11 bits
        
        return radiant_towers, dire_towers
    
    async def subscribe_to_match(self, match_id: str) -> AsyncIterator[GameEvent]:
        """
        Subscribe to match events via polling.
        
        Stratz doesn't have WebSocket, so we poll for changes.
        """
        last_state: Optional[GameState] = None
        poll_interval = 2.0  # Poll every 2 seconds
        
        logger.info(f"Subscribing to Stratz match {match_id}")
        
        try:
            while True:
                try:
                    # Refresh live matches to update cache
                    await self.get_live_matches()
                    
                    current_state = await self.get_match_state(match_id)
                    
                    if current_state is None:
                        # Match might have ended
                        logger.info(f"Match {match_id} ended or not found in Stratz")
                        yield GameEvent(
                            event_type="game_end",
                            timestamp=datetime.utcnow(),
                            game_time_seconds=0,
                            team_id="",
                            value=0.0,
                            details={"reason": "match_not_found"}
                        )
                        break
                    
                    if last_state is not None:
                        events = self._detect_state_changes(last_state, current_state)
                        for event in events:
                            yield event
                    
                    last_state = current_state
                    self._last_states[match_id] = current_state
                    
                except Exception as e:
                    logger.error(f"Error polling Stratz match {match_id}: {e}")
                
                await asyncio.sleep(poll_interval)
                
        except asyncio.CancelledError:
            logger.debug(f"Stratz subscription cancelled for match {match_id}")
    
    def _detect_state_changes(
        self, 
        old: GameState, 
        new: GameState
    ) -> List[GameEvent]:
        """Detect significant changes between game states."""
        events = []
        now = datetime.utcnow()
        
        # Detect kills
        kill_diff_t1 = new.team1_kills - old.team1_kills
        kill_diff_t2 = new.team2_kills - old.team2_kills
        
        if kill_diff_t1 > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id if new.team1 else "",
                value=float(kill_diff_t1),
                details={"team_name": new.team1.name if new.team1 else "", "kills": kill_diff_t1}
            ))
        
        if kill_diff_t2 > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id if new.team2 else "",
                value=float(kill_diff_t2),
                details={"team_name": new.team2.name if new.team2 else "", "kills": kill_diff_t2}
            ))
        
        # Detect tower kills
        tower_diff_t1 = new.team1_towers - old.team1_towers
        tower_diff_t2 = new.team2_towers - old.team2_towers
        
        if tower_diff_t1 > 0:
            events.append(GameEvent(
                event_type="tower_kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id if new.team1 else "",
                value=float(tower_diff_t1),
                details={"team_name": new.team1.name if new.team1 else "", "towers": tower_diff_t1}
            ))
        
        if tower_diff_t2 > 0:
            events.append(GameEvent(
                event_type="tower_kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id if new.team2 else "",
                value=float(tower_diff_t2),
                details={"team_name": new.team2.name if new.team2 else "", "towers": tower_diff_t2}
            ))
        
        # Detect significant gold swings (>2000 gold change)
        old_gold_diff = old.team1_gold - old.team2_gold
        new_gold_diff = new.team1_gold - new.team2_gold
        gold_swing = abs(new_gold_diff - old_gold_diff)
        
        if gold_swing > 2000:
            events.append(GameEvent(
                event_type="gold_swing",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id if new_gold_diff > old_gold_diff else new.team2.id if new.team2 else "",
                value=float(gold_swing),
                details={
                    "gold_swing": gold_swing,
                    "new_lead": new_gold_diff,
                    "old_lead": old_gold_diff,
                }
            ))
        
        # Detect win probability changes (>5% change)
        old_prob = old.team1_win_probability or 0.5
        new_prob = new.team1_win_probability or 0.5
        prob_change = new_prob - old_prob
        
        if abs(prob_change) > 0.05:
            events.append(GameEvent(
                event_type="probability_shift",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id if prob_change > 0 else new.team2.id if new.team2 else "",
                value=prob_change,
                details={
                    "old_probability": old_prob,
                    "new_probability": new_prob,
                    "change": prob_change,
                }
            ))
        
        return events
    
    def analyze_event_impact(self, event: GameEvent, game_state: GameState) -> float:
        """
        Analyze the probability impact of an event.
        
        Stratz already provides win probability, so we use their calculation
        plus our own adjustments for recent events.
        """
        base_impact = 0.0
        
        if event.event_type == "kill":
            # Each kill worth ~1-2% depending on game time
            game_minutes = game_state.game_time_seconds / 60
            if game_minutes < 15:
                base_impact = 0.015  # Early game kills matter more
            elif game_minutes < 30:
                base_impact = 0.01
            else:
                base_impact = 0.008  # Late game kills matter less
            
            base_impact *= event.value  # Multiply by number of kills
        
        elif event.event_type == "tower_kill":
            base_impact = 0.02 * event.value  # Towers are significant
        
        elif event.event_type == "gold_swing":
            # Gold swings indicate team fights or objectives
            gold_swing = event.details.get("gold_swing", 0)
            base_impact = min(gold_swing / 50000, 0.05)  # Cap at 5%
        
        elif event.event_type == "probability_shift":
            # Use Stratz's own probability change
            base_impact = abs(event.details.get("change", 0))
        
        return base_impact
