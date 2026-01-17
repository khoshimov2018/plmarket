"""
OpenDota API provider for real-time Dota 2 match data.
https://docs.opendota.com - FREE API with live match data.

This is a key data source for the latency arbitrage strategy.
OpenDota provides real-time game state updates that we can use
to detect events BEFORE the market prices them in.
"""

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional, List, Dict, Union
import aiohttp

from src.models import Game, GameState, GameEvent, Team, MatchStatus
from src.esports.base import BaseEsportsProvider
from src.logger import get_logger


logger = get_logger("opendota")


class OpenDotaProvider(BaseEsportsProvider):
    """
    OpenDota API client for real-time Dota 2 data.
    
    Key advantages:
    - FREE with generous rate limits (higher with API key)
    - Real-time match data via Steam API
    - Detailed game state (kills, gold, items, etc.)
    - API key provides higher rate limits and better access
    """
    
    BASE_URL = "https://api.opendota.com/api"
    STEAM_API_URL = "https://api.steampowered.com"
    
    # Pro match tracking
    LIVE_MATCHES_ENDPOINT = "/live"
    PRO_MATCHES_ENDPOINT = "/proMatches"
    
    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self._session: Optional[aiohttp.ClientSession] = None
        self._tracked_matches: dict[str, dict] = {}
        self._last_states: dict[str, GameState] = {}
        # Store API key for authenticated requests
        self._opendota_api_key = api_key
    
    @property
    def supported_games(self) -> List[Game]:
        return [Game.DOTA2]
    
    async def connect(self) -> None:
        """Initialize HTTP session."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        self._is_connected = True
        logger.info("Connected to OpenDota API")
    
    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._is_connected = False
        logger.info("Disconnected from OpenDota API")
    
    async def _request(self, endpoint: str, params: Optional[dict] = None) -> Union[Dict, List]:
        """Make API request with optional API key authentication."""
        if not self._session:
            raise RuntimeError("Not connected to OpenDota API")
        
        url = f"{self.BASE_URL}{endpoint}"
        
        # Add API key if available for higher rate limits
        if params is None:
            params = {}
        if self._opendota_api_key:
            params["api_key"] = self._opendota_api_key
        
        async with self._session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()
    
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """
        Get currently live professional Dota 2 matches.
        
        Returns matches from top-tier tournaments that are likely
        to have Polymarket markets.
        """
        try:
            # Get live matches from OpenDota
            data = await self._request("/live")
            
            live_matches = []
            for match in data:
                # Filter for pro/high-tier matches
                if self._is_notable_match(match):
                    match["game"] = Game.DOTA2
                    match["source"] = "opendota"
                    live_matches.append(match)
                    
                    # Track this match
                    match_id = str(match.get("match_id", ""))
                    self._tracked_matches[match_id] = match
            
            logger.debug(f"Found {len(live_matches)} live Dota 2 matches")
            return live_matches
            
        except Exception as e:
            logger.error(f"Error fetching live matches: {e}")
            return []
    
    def _is_notable_match(self, match: dict) -> bool:
        """Check if match is notable enough to have a Polymarket market."""
        # Check for pro teams (have team IDs AND team names)
        radiant_team = match.get("radiant_team", {})
        dire_team = match.get("dire_team", {})
        
        # CRITICAL: Only consider matches with ACTUAL team names
        # Matches without team names will NEVER match Polymarket markets
        radiant_name = radiant_team.get("team_name") or radiant_team.get("name")
        dire_name = dire_team.get("team_name") or dire_team.get("name")
        
        # Skip if either team name is missing or generic
        if not radiant_name or radiant_name in ["Radiant", "Unknown", ""]:
            return False
        if not dire_name or dire_name in ["Dire", "Unknown", ""]:
            return False
        
        # Must have team IDs to be a pro match
        if not radiant_team.get("team_id") or not dire_team.get("team_id"):
            return False
        
        logger.info(f"🎮 Pro Dota match: {radiant_name} vs {dire_name}")
        return True
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """
        Get current state of a live Dota 2 match.
        
        This is the KEY method for latency arbitrage - it gives us
        real-time game state that we compare against market prices.
        """
        try:
            # First check our tracked matches cache
            if match_id in self._tracked_matches:
                match_data = self._tracked_matches[match_id]
            else:
                # Fetch from live endpoint
                live_matches = await self._request("/live")
                match_data = None
                for m in live_matches:
                    if str(m.get("match_id")) == str(match_id):
                        match_data = m
                        self._tracked_matches[match_id] = m
                        break
                
                if not match_data:
                    return None
            
            return self._parse_live_match(match_data)
            
        except Exception as e:
            logger.error(f"Error fetching match state {match_id}: {e}")
            return None
    
    def _parse_live_match(self, data: dict) -> Optional[GameState]:
        """Parse OpenDota live match data into GameState."""
        
        # Extract team info
        radiant = data.get("radiant_team", {})
        dire = data.get("dire_team", {})
        
        # Get team names - CRITICAL: Don't use generic "Radiant/Dire" 
        # as they will never match Polymarket markets
        radiant_name = radiant.get("team_name") or radiant.get("name")
        dire_name = dire.get("team_name") or dire.get("name")
        
        # If no team names, this is a pub match - skip it entirely
        if not radiant_name or radiant_name in ["Radiant", "Unknown", ""]:
            radiant_name = None
        if not dire_name or dire_name in ["Dire", "Unknown", ""]:
            dire_name = None
        
        # CRITICAL: Return None if we don't have real team names
        # These matches will NEVER match Polymarket markets
        if not radiant_name or not dire_name:
            logger.debug(f"Skipping pub match - no team names: radiant={radiant}, dire={dire}")
            return None
        
        # Log pro match found
        logger.info(f"🎮 Pro Dota match found: {radiant_name} vs {dire_name}")
        
        team1 = Team(
            id=str(radiant.get("team_id", "radiant")),
            name=radiant_name,
            short_name=radiant.get("team_tag") or radiant_name[:3].upper(),
            logo_url=radiant.get("team_logo"),
        )
        
        team2 = Team(
            id=str(dire.get("team_id", "dire")),
            name=dire_name,
            short_name=dire.get("team_tag") or dire_name[:3].upper(),
            logo_url=dire.get("team_logo"),
        )
        
        # Extract game state
        # OpenDota provides real-time scoreboard data
        scoreboard = data.get("scoreboard", {})
        
        radiant_score = scoreboard.get("radiant", {})
        dire_score = scoreboard.get("dire", {})
        
        # Kills
        radiant_kills = data.get("radiant_score", 0)
        dire_kills = data.get("dire_score", 0)
        
        # Gold (net worth)
        radiant_gold = radiant_score.get("net_worth", 0)
        dire_gold = dire_score.get("net_worth", 0)
        
        # If no scoreboard, estimate from score
        if radiant_gold == 0:
            # Rough estimate: 500 gold per kill average
            radiant_gold = 10000 + radiant_kills * 500
            dire_gold = 10000 + dire_kills * 500
        
        # Towers (barracks count as structures)
        radiant_towers = radiant_score.get("tower_kills", 0)
        dire_towers = dire_score.get("tower_kills", 0)
        
        # Game duration
        game_time = data.get("game_time", 0)
        if game_time == 0:
            # Calculate from start time
            start_time = data.get("activate_time", data.get("start_time", 0))
            if start_time:
                game_time = int(datetime.utcnow().timestamp()) - start_time
        
        # Series info
        series_type = data.get("series_type", 0)  # 0=Bo1, 1=Bo3, 2=Bo5
        series_format = {0: 1, 1: 3, 2: 5}.get(series_type, 1)
        
        radiant_wins = data.get("radiant_series_wins", 0)
        dire_wins = data.get("dire_series_wins", 0)
        
        state = GameState(
            match_id=str(data.get("match_id", "")),
            game=Game.DOTA2,
            team1=team1,
            team2=team2,
            game_number=radiant_wins + dire_wins + 1,
            game_time_seconds=float(game_time),
            team1_kills=radiant_kills,
            team2_kills=dire_kills,
            team1_gold=radiant_gold,
            team2_gold=dire_gold,
            team1_towers=radiant_towers,
            team2_towers=dire_towers,
            team1_series_score=radiant_wins,
            team2_series_score=dire_wins,
            series_format=series_format,
        )
        
        return state
    
    async def subscribe_to_match(self, match_id: str) -> AsyncIterator[GameEvent]:
        """
        Subscribe to real-time events for a Dota 2 match.
        
        Polls the live endpoint frequently to detect changes.
        This is where we detect events FAST.
        """
        poll_interval = 0.5  # 500ms - aggressive polling for speed
        last_state: Optional[GameState] = None
        
        logger.info(f"Subscribing to Dota 2 match {match_id}")
        
        try:
            while True:
                try:
                    current_state = await self.get_match_state(match_id)
                    
                    if current_state is None:
                        # Match might have ended
                        logger.info(f"Match {match_id} ended or not found")
                        break
                    
                    if last_state is not None:
                        # Detect changes and emit events
                        events = self._detect_events(last_state, current_state)
                        for event in events:
                            yield event
                    
                    last_state = current_state
                    self._last_states[match_id] = current_state
                    
                except Exception as e:
                    logger.error(f"Error polling match {match_id}: {e}")
                
                await asyncio.sleep(poll_interval)
                
        except asyncio.CancelledError:
            logger.debug(f"Match subscription cancelled: {match_id}")
    
    def _detect_events(self, old: GameState, new: GameState) -> List[GameEvent]:
        """
        Detect game events by comparing states.
        
        This is CRITICAL for latency arbitrage - we need to detect
        kills, objectives, and other events as fast as possible.
        """
        events = []
        now = datetime.utcnow()
        
        # Detect kills (most frequent event)
        radiant_kill_diff = new.team1_kills - old.team1_kills
        dire_kill_diff = new.team2_kills - old.team2_kills
        
        if radiant_kill_diff > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=float(radiant_kill_diff),
                details={
                    "team_name": new.team1.name,
                    "kills": radiant_kill_diff,
                    "total_kills": new.team1_kills,
                }
            ))
            
            # Check for team wipe (5 kills in short time)
            if radiant_kill_diff >= 5:
                events.append(GameEvent(
                    event_type="team_wipe",
                    timestamp=now,
                    game_time_seconds=new.game_time_seconds,
                    team_id=new.team1.id,
                    value=5.0,
                    details={"team_name": new.team1.name}
                ))
        
        if dire_kill_diff > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=float(dire_kill_diff),
                details={
                    "team_name": new.team2.name,
                    "kills": dire_kill_diff,
                    "total_kills": new.team2_kills,
                }
            ))
            
            if dire_kill_diff >= 5:
                events.append(GameEvent(
                    event_type="team_wipe",
                    timestamp=now,
                    game_time_seconds=new.game_time_seconds,
                    team_id=new.team2.id,
                    value=5.0,
                    details={"team_name": new.team2.name}
                ))
        
        # Detect tower kills
        radiant_tower_diff = new.team1_towers - old.team1_towers
        dire_tower_diff = new.team2_towers - old.team2_towers
        
        if radiant_tower_diff > 0:
            events.append(GameEvent(
                event_type="tower",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=float(radiant_tower_diff) * 200,  # Gold value
                details={
                    "team_name": new.team1.name,
                    "towers": radiant_tower_diff,
                }
            ))
        
        if dire_tower_diff > 0:
            events.append(GameEvent(
                event_type="tower",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=float(dire_tower_diff) * 200,
                details={
                    "team_name": new.team2.name,
                    "towers": dire_tower_diff,
                }
            ))
        
        # Detect large gold swings (Roshan, big teamfight, etc.)
        gold_swing = abs(new.gold_lead - old.gold_lead)
        if gold_swing > 2000:  # Significant gold swing
            winning_team = new.team1 if new.gold_lead > old.gold_lead else new.team2
            
            # Check if it might be Roshan
            event_type = "objective"
            if gold_swing > 3000 and new.game_time_seconds > 600:  # After 10 min
                event_type = "roshan"
            
            events.append(GameEvent(
                event_type=event_type,
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=winning_team.id,
                value=float(gold_swing),
                details={
                    "team_name": winning_team.name,
                    "gold_swing": gold_swing,
                }
            ))
        
        # Detect barracks (mega creeps threat)
        if new.team1_towers >= 8 and old.team1_towers < 8:
            events.append(GameEvent(
                event_type="barracks",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=1000.0,
                details={"team_name": new.team1.name, "mega_creeps_threat": True}
            ))
        
        if new.team2_towers >= 8 and old.team2_towers < 8:
            events.append(GameEvent(
                event_type="barracks",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=1000.0,
                details={"team_name": new.team2.name, "mega_creeps_threat": True}
            ))
        
        return events
    
    async def get_upcoming_matches(
        self,
        game: Optional[Game] = None,
        hours_ahead: int = 24
    ) -> List[Dict]:
        """Get upcoming pro matches."""
        try:
            # OpenDota doesn't have upcoming matches endpoint
            # Use recent pro matches to find teams that might play
            data = await self._request("/proMatches")
            
            # Return recent matches as reference
            return data[:20] if data else []
            
        except Exception as e:
            logger.error(f"Error fetching upcoming matches: {e}")
            return []
    
    def analyze_event_impact(self, event: GameEvent, state: GameState) -> float:
        """
        Analyze the probability impact of a Dota 2 event.
        
        This determines how much the win probability should shift
        based on what just happened in the game.
        """
        base_impact = 0.0
        game_time = state.game_time_seconds
        
        # Time multiplier - late game events matter more
        if game_time < 600:  # Before 10 min
            time_mult = 0.5
        elif game_time < 1200:  # 10-20 min
            time_mult = 0.8
        elif game_time < 1800:  # 20-30 min
            time_mult = 1.0
        elif game_time < 2400:  # 30-40 min
            time_mult = 1.3
        else:  # 40+ min
            time_mult = 1.5
        
        event_type = event.event_type.lower()
        
        if event_type == "kill":
            kills = event.details.get("kills", 1)
            base_impact = 0.008 * kills  # 0.8% per kill
            
        elif event_type == "team_wipe":
            base_impact = 0.06  # 6% for team wipe
            
        elif event_type == "tower":
            towers = event.details.get("towers", 1)
            base_impact = 0.015 * towers  # 1.5% per tower
            
        elif event_type == "roshan":
            base_impact = 0.07  # 7% for Roshan (Aegis)
            
        elif event_type == "barracks":
            base_impact = 0.10  # 10% for barracks (mega creeps threat)
            
        elif event_type == "objective":
            gold_value = event.value
            if gold_value > 5000:
                base_impact = 0.08
            elif gold_value > 3000:
                base_impact = 0.05
            else:
                base_impact = 0.03
        
        return min(0.25, base_impact * time_mult)
