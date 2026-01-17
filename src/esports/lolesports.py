"""
LoL Esports API provider for real-time League of Legends match data.
https://lolesports.com - Official Riot Games esports data.

This provides the FASTEST possible data for LoL esports events
because it's the official source that broadcasts use.
"""

import asyncio
from datetime import datetime
from typing import AsyncIterator, Optional, List, Dict
import aiohttp

from src.models import Game, GameState, GameEvent, Team
from src.esports.base import BaseEsportsProvider
from src.logger import get_logger


logger = get_logger("lolesports")


class LoLEsportsProvider(BaseEsportsProvider):
    """
    Official LoL Esports API client.
    
    Key advantages:
    - Official Riot data (fastest possible)
    - Real-time game state during broadcasts
    - Detailed events (kills, objectives, gold)
    - No API key required for public endpoints
    """
    
    # Official LoL Esports API endpoints
    BASE_URL = "https://esports-api.lolesports.com/persisted/gw"
    LIVE_URL = "https://feed.lolesports.com/livestats/v1"
    
    # API key (public, used by the website)
    API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
    
    def __init__(self, api_key: str = ""):
        super().__init__(api_key or self.API_KEY)
        self._session: Optional[aiohttp.ClientSession] = None
        self._live_games: Dict[str, dict] = {}
        self._last_states: Dict[str, GameState] = {}
    
    @property
    def supported_games(self) -> List[Game]:
        return [Game.LOL]
    
    async def connect(self) -> None:
        """Initialize HTTP session."""
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        )
        self._is_connected = True
        logger.info("Connected to LoL Esports API")
    
    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._is_connected = False
        logger.info("Disconnected from LoL Esports API")
    
    async def _request(self, url: str, params: Optional[dict] = None) -> dict:
        """Make API request."""
        if not self._session:
            raise RuntimeError("Not connected to LoL Esports API")
        
        if params is None:
            params = {}
        params["hl"] = "en-US"  # Language
        
        async with self._session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()
    
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """
        Get currently live LoL esports matches.
        
        This hits the official esports API to find ongoing games.
        """
        try:
            # Get live events
            url = f"{self.BASE_URL}/getLive"
            data = await self._request(url)
            
            live_matches = []
            events = data.get("data", {}).get("schedule", {}).get("events", [])
            
            for event in events:
                if event.get("state") == "inProgress":
                    match_data = self._parse_event_to_match(event)
                    if match_data:
                        live_matches.append(match_data)
                        self._live_games[match_data["match_id"]] = match_data
            
            logger.debug(f"Found {len(live_matches)} live LoL matches")
            return live_matches
            
        except Exception as e:
            logger.error(f"Error fetching live matches: {e}")
            return []
    
    def _parse_event_to_match(self, event: dict) -> Optional[dict]:
        """Parse LoL Esports event into match data."""
        try:
            match_info = event.get("match", {})
            teams = match_info.get("teams", [])
            
            if len(teams) < 2:
                return None
            
            team1_name = teams[0].get("name", "")
            team2_name = teams[1].get("name", "")
            league_name = event.get("league", {}).get("name", "")
            
            # Log the match for debugging
            logger.debug(f"Found LoL match: {team1_name} vs {team2_name} ({league_name})")
            
            # Store match data in format compatible with execution engine
            match_data = {
                "match_id": event.get("id", ""),
                "id": event.get("id", ""),  # Also store as 'id' for compatibility
                "game": Game.LOL,
                "source": "lolesports",
                "league": league_name,
                "team1": {
                    "id": teams[0].get("code", ""),
                    "name": team1_name,
                    "code": teams[0].get("code", "T1"),
                    "image": teams[0].get("image", ""),
                },
                "team2": {
                    "id": teams[1].get("code", ""),
                    "name": team2_name,
                    "code": teams[1].get("code", "T2"),
                    "image": teams[1].get("image", ""),
                },
                "games": match_info.get("games", []),
                "strategy": match_info.get("strategy", {}),
                # Store raw event for get_match_state
                "_raw_event": event,
            }
            
            return match_data
        except Exception as e:
            logger.error(f"Error parsing event: {e}")
            return None
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """
        Get current state of a live LoL match.
        
        Uses the live stats feed for real-time data.
        """
        try:
            # Get live game details
            if match_id not in self._live_games:
                await self.get_live_matches()
            
            match_data = self._live_games.get(match_id)
            if not match_data:
                return None
            
            # Try to get live stats
            live_stats = await self._get_live_stats(match_id)
            
            return self._build_game_state(match_data, live_stats)
            
        except Exception as e:
            logger.error(f"Error fetching match state {match_id}: {e}")
            return None
    
    async def _get_live_stats(self, match_id: str) -> Optional[dict]:
        """Get real-time stats from live feed."""
        try:
            # The live stats endpoint
            url = f"{self.LIVE_URL}/window/{match_id}"
            
            async with self._session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    frames = data.get("frames", [])
                    if frames:
                        latest = frames[-1]
                        # Log that we got real stats
                        blue_team = latest.get("blueTeam", {})
                        red_team = latest.get("redTeam", {})
                        logger.info(
                            f"📊 LIVE STATS for {match_id}: "
                            f"Blue kills={blue_team.get('totalKills', 0)} gold={blue_team.get('totalGold', 0)} | "
                            f"Red kills={red_team.get('totalKills', 0)} gold={red_team.get('totalGold', 0)}"
                        )
                    return data
                else:
                    logger.debug(f"Live stats returned {response.status} for {match_id}")
                return None
                
        except Exception as e:
            logger.debug(f"Could not get live stats for {match_id}: {e}")
            return None
    
    def _build_game_state(self, match_data: dict, live_stats: Optional[dict]) -> GameState:
        """Build GameState from match data and live stats."""
        
        team1_data = match_data.get("team1", {})
        team2_data = match_data.get("team2", {})
        
        team1 = Team(
            id=team1_data.get("id", "team1"),
            name=team1_data.get("name", "Team 1"),
            short_name=team1_data.get("code", "T1"),
            logo_url=team1_data.get("image"),
        )
        
        team2 = Team(
            id=team2_data.get("id", "team2"),
            name=team2_data.get("name", "Team 2"),
            short_name=team2_data.get("code", "T2"),
            logo_url=team2_data.get("image"),
        )
        
        # Default values
        game_time = 0.0
        team1_kills = 0
        team2_kills = 0
        team1_gold = 0
        team2_gold = 0
        team1_towers = 0
        team2_towers = 0
        
        # Parse live stats if available
        if live_stats:
            frames = live_stats.get("frames", [])
            if frames:
                latest_frame = frames[-1]
                
                # Game time
                game_time = latest_frame.get("rfc460Timestamp", 0)
                if isinstance(game_time, str):
                    # Parse timestamp
                    try:
                        dt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
                        game_time = (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds()
                    except:
                        game_time = 0
                
                # Team stats from frame
                blue_team = latest_frame.get("blueTeam", {})
                red_team = latest_frame.get("redTeam", {})
                
                team1_kills = blue_team.get("totalKills", 0)
                team2_kills = red_team.get("totalKills", 0)
                team1_gold = blue_team.get("totalGold", 0)
                team2_gold = red_team.get("totalGold", 0)
                team1_towers = blue_team.get("towers", 0)
                team2_towers = red_team.get("towers", 0)
        
        # Series info
        strategy = match_data.get("strategy", {})
        series_format = strategy.get("count", 1)
        
        games = match_data.get("games", [])
        team1_wins = sum(1 for g in games if g.get("winner") == team1.id)
        team2_wins = sum(1 for g in games if g.get("winner") == team2.id)
        
        state = GameState(
            match_id=match_data.get("match_id", ""),
            game=Game.LOL,
            team1=team1,
            team2=team2,
            game_number=team1_wins + team2_wins + 1,
            game_time_seconds=game_time,
            team1_kills=team1_kills,
            team2_kills=team2_kills,
            team1_gold=team1_gold,
            team2_gold=team2_gold,
            team1_towers=team1_towers,
            team2_towers=team2_towers,
            team1_series_score=team1_wins,
            team2_series_score=team2_wins,
            series_format=series_format,
        )
        
        # Calculate win probability based on game state
        state.team1_win_prob, state.team2_win_prob = self._calculate_win_probability(state)
        
        return state
    
    def _calculate_win_probability(self, state: GameState) -> tuple:
        """
        Calculate win probability based on current game state.
        Uses gold lead, kill lead, objective control, and game time.
        """
        # Base probability starts at 50/50
        base_prob = 0.5
        
        # If no meaningful data, return 50/50
        if state.team1_gold == 0 and state.team2_gold == 0:
            if state.team1_kills == 0 and state.team2_kills == 0:
                return (0.5, 0.5)
        
        # Determine game phase (affects weight of factors)
        game_minutes = state.game_time_seconds / 60 if state.game_time_seconds else 0
        
        if game_minutes < 10:
            phase = "early"
            gold_weight = 0.15
            kill_weight = 0.10
            tower_weight = 0.05
        elif game_minutes < 25:
            phase = "mid"
            gold_weight = 0.25
            kill_weight = 0.15
            tower_weight = 0.15
        else:
            phase = "late"
            gold_weight = 0.35
            kill_weight = 0.10
            tower_weight = 0.25
        
        # Gold advantage factor
        total_gold = state.team1_gold + state.team2_gold
        if total_gold > 0:
            gold_diff = state.team1_gold - state.team2_gold
            # Normalize: 10k gold lead = ~0.3 probability shift
            gold_factor = gold_diff / 30000  # 30k gold = max factor
            gold_factor = max(-0.35, min(0.35, gold_factor))
        else:
            gold_factor = 0
        
        # Kill advantage factor
        total_kills = state.team1_kills + state.team2_kills
        if total_kills > 0:
            kill_diff = state.team1_kills - state.team2_kills
            # Normalize: 10 kill lead = ~0.15 probability shift
            kill_factor = kill_diff / 20  # 20 kills = max factor
            kill_factor = max(-0.20, min(0.20, kill_factor))
        else:
            kill_factor = 0
        
        # Tower advantage factor
        total_towers = state.team1_towers + state.team2_towers
        if total_towers > 0:
            tower_diff = state.team1_towers - state.team2_towers
            # Each tower is significant
            tower_factor = tower_diff / 11  # 11 towers = max factor
            tower_factor = max(-0.25, min(0.25, tower_factor))
        else:
            tower_factor = 0
        
        # Combine factors with phase-appropriate weights
        adjustment = (
            gold_factor * gold_weight +
            kill_factor * kill_weight +
            tower_factor * tower_weight
        )
        
        # Calculate final probability
        team1_prob = base_prob + adjustment
        team1_prob = max(0.05, min(0.95, team1_prob))  # Clamp to 5-95%
        
        logger.debug(
            f"win_probability_calculated: match={state.match_id} phase={phase} "
            f"gold_factor={gold_factor:.3f} kill_factor={kill_factor:.3f} "
            f"tower_factor={tower_factor:.3f} team1_prob={team1_prob:.2%}"
        )
        
        return (team1_prob, 1 - team1_prob)
    
    async def subscribe_to_match(self, match_id: str) -> AsyncIterator[GameEvent]:
        """
        Subscribe to real-time events for a LoL match.
        
        Polls the live feed aggressively for speed.
        """
        poll_interval = 0.3  # 300ms - very aggressive for latency edge
        last_state: Optional[GameState] = None
        
        logger.info(f"Subscribing to LoL match {match_id}")
        
        try:
            while True:
                try:
                    current_state = await self.get_match_state(match_id)
                    
                    if current_state is None:
                        logger.info(f"Match {match_id} ended or not found")
                        break
                    
                    if last_state is not None:
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
        """Detect game events by comparing states."""
        events = []
        now = datetime.utcnow()
        
        # Detect kills
        blue_kill_diff = new.team1_kills - old.team1_kills
        red_kill_diff = new.team2_kills - old.team2_kills
        
        if blue_kill_diff > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=float(blue_kill_diff),
                details={
                    "team_name": new.team1.name,
                    "kills": blue_kill_diff,
                    "total_kills": new.team1_kills,
                }
            ))
            
            # Ace detection
            if blue_kill_diff >= 5:
                events.append(GameEvent(
                    event_type="ace",
                    timestamp=now,
                    game_time_seconds=new.game_time_seconds,
                    team_id=new.team1.id,
                    value=5.0,
                    details={"team_name": new.team1.name}
                ))
        
        if red_kill_diff > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=float(red_kill_diff),
                details={
                    "team_name": new.team2.name,
                    "kills": red_kill_diff,
                    "total_kills": new.team2_kills,
                }
            ))
            
            if red_kill_diff >= 5:
                events.append(GameEvent(
                    event_type="ace",
                    timestamp=now,
                    game_time_seconds=new.game_time_seconds,
                    team_id=new.team2.id,
                    value=5.0,
                    details={"team_name": new.team2.name}
                ))
        
        # Detect tower kills
        blue_tower_diff = new.team1_towers - old.team1_towers
        red_tower_diff = new.team2_towers - old.team2_towers
        
        if blue_tower_diff > 0:
            events.append(GameEvent(
                event_type="tower",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=float(blue_tower_diff) * 250,
                details={
                    "team_name": new.team1.name,
                    "towers": blue_tower_diff,
                }
            ))
        
        if red_tower_diff > 0:
            events.append(GameEvent(
                event_type="tower",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=float(red_tower_diff) * 250,
                details={
                    "team_name": new.team2.name,
                    "towers": red_tower_diff,
                }
            ))
        
        # Detect large gold swings (Baron, Dragon, big fight)
        gold_swing = abs(new.gold_lead - old.gold_lead)
        if gold_swing > 1500:
            winning_team = new.team1 if new.gold_lead > old.gold_lead else new.team2
            
            # Guess objective type based on gold value and time
            event_type = "objective"
            if gold_swing > 3000:
                event_type = "baron"  # Baron gives ~3k+ gold
            elif gold_swing > 1500 and new.game_time_seconds > 1200:
                event_type = "dragon"  # Dragon soul or elder
            
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
        
        # Detect inhibitor (tower lead of 9+)
        if new.team1_towers >= 9 and old.team1_towers < 9:
            events.append(GameEvent(
                event_type="inhibitor",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=500.0,
                details={"team_name": new.team1.name}
            ))
        
        if new.team2_towers >= 9 and old.team2_towers < 9:
            events.append(GameEvent(
                event_type="inhibitor",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=500.0,
                details={"team_name": new.team2.name}
            ))
        
        return events
    
    async def get_upcoming_matches(
        self,
        game: Optional[Game] = None,
        hours_ahead: int = 24
    ) -> List[Dict]:
        """Get upcoming LoL esports matches."""
        try:
            url = f"{self.BASE_URL}/getSchedule"
            data = await self._request(url)
            
            events = data.get("data", {}).get("schedule", {}).get("events", [])
            
            upcoming = []
            for event in events:
                if event.get("state") == "unstarted":
                    match_data = self._parse_event_to_match(event)
                    if match_data:
                        upcoming.append(match_data)
            
            return upcoming[:20]
            
        except Exception as e:
            logger.error(f"Error fetching upcoming matches: {e}")
            return []
    
    def analyze_event_impact(self, event: GameEvent, state: GameState) -> float:
        """Analyze probability impact of a LoL event."""
        base_impact = 0.0
        game_time = state.game_time_seconds
        
        # Time multiplier
        if game_time < 900:  # Before 15 min
            time_mult = 0.7
        elif game_time < 1500:  # 15-25 min
            time_mult = 1.0
        elif game_time < 2100:  # 25-35 min
            time_mult = 1.2
        else:  # 35+ min
            time_mult = 1.4
        
        event_type = event.event_type.lower()
        
        if event_type == "kill":
            kills = event.details.get("kills", 1)
            base_impact = 0.01 * kills  # 1% per kill
            
        elif event_type == "ace":
            base_impact = 0.06  # 6% for ace
            
        elif event_type == "tower":
            towers = event.details.get("towers", 1)
            base_impact = 0.015 * towers  # 1.5% per tower
            
        elif event_type == "baron":
            base_impact = 0.08  # 8% for Baron
            
        elif event_type == "dragon":
            base_impact = 0.04  # 4% for dragon (more for soul/elder)
            
        elif event_type == "inhibitor":
            base_impact = 0.06  # 6% for inhibitor
            
        elif event_type == "objective":
            gold_value = event.value
            if gold_value > 3000:
                base_impact = 0.07
            elif gold_value > 1500:
                base_impact = 0.04
            else:
                base_impact = 0.02
        
        return min(0.25, base_impact * time_mult)
