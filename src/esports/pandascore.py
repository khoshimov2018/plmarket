"""
PandaScore API provider for esports data.
https://pandascore.co - One of the most comprehensive esports data APIs.
"""

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional, List, Dict, Union

import httpx
from aiolimiter import AsyncLimiter

from src.models import Game, GameState, GameEvent, Team, MatchStatus
from src.esports.base import BaseEsportsProvider
from src.logger import get_logger


logger = get_logger("pandascore")


class PandaScoreProvider(BaseEsportsProvider):
    """PandaScore API client for esports data."""
    
    BASE_URL = "https://api.pandascore.co"
    
    # Rate limit: 1000 requests per hour = ~16.6/min
    # For latency arbitrage, we need to be more aggressive
    RATE_LIMIT = AsyncLimiter(60, 60)  # 60 requests per minute (1/sec average)
    
    GAME_SLUGS = {
        Game.LOL: "lol",
        Game.DOTA2: "dota2",
    }
    
    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._client: Optional[httpx.AsyncClient] = None
        self._event_queues: dict[str, asyncio.Queue] = {}
        self._polling_tasks: dict[str, asyncio.Task] = {}
    
    @property
    def supported_games(self) -> List[Game]:
        return [Game.LOL, Game.DOTA2]
    
    async def connect(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        self._is_connected = True
        logger.info("Connected to PandaScore API")
    
    async def disconnect(self) -> None:
        """Close HTTP client and stop polling."""
        # Cancel all polling tasks
        for task in self._polling_tasks.values():
            task.cancel()
        self._polling_tasks.clear()
        
        if self._client:
            await self._client.aclose()
            self._client = None
        
        self._is_connected = False
        logger.info("Disconnected from PandaScore API")
    
    async def _request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[dict] = None
    ) -> Union[Dict, List]:
        """Make rate-limited API request."""
        if not self._client:
            raise RuntimeError("Not connected to PandaScore API")
        
        async with self.RATE_LIMIT:
            response = await self._client.request(method, endpoint, params=params)
            response.raise_for_status()
            return response.json()
    
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """Get currently live matches."""
        matches = []
        
        games_to_check = [game] if game else self.supported_games
        
        for g in games_to_check:
            slug = self.GAME_SLUGS.get(g)
            if not slug:
                continue
            
            try:
                data = await self._request(
                    "GET", 
                    f"/{slug}/matches/running",
                    params={"per_page": 50}
                )
                
                for match in data:
                    match["game"] = g
                    matches.append(match)
                    
            except httpx.HTTPError as e:
                logger.error(f"Error fetching live {slug} matches", error=str(e))
        
        logger.debug(f"Found {len(matches)} live matches")
        return matches
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """Get current state of a match."""
        try:
            # Try LoL first, then Dota
            for game in self.supported_games:
                slug = self.GAME_SLUGS[game]
                try:
                    match_data = await self._request("GET", f"/{slug}/matches/{match_id}")
                    return self._parse_match_state(match_data, game)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 403:
                        # Free tier doesn't have access to detailed match data
                        # Try to get basic info from running matches instead
                        logger.debug(f"403 on match details (free tier limitation), using basic data")
                        return await self._get_match_state_from_running(match_id, game)
                    if e.response.status_code != 404:
                        raise
                    continue
            
            return None
            
        except httpx.HTTPError as e:
            logger.error(f"Error fetching match {match_id}", error=str(e))
            return None
    
    async def _get_match_state_from_running(self, match_id: str, game: Game) -> Optional[GameState]:
        """Get basic match state from running matches endpoint (free tier fallback)."""
        try:
            slug = self.GAME_SLUGS[game]
            data = await self._request("GET", f"/{slug}/matches/running", params={"per_page": 50})
            
            for match in data:
                if str(match.get("id")) == str(match_id):
                    return self._parse_match_state(match, game)
            
            return None
        except Exception as e:
            logger.debug(f"Could not get match from running: {e}")
            return None
    
    def _parse_match_state(self, match_data: dict, game: Game) -> GameState:
        """Parse PandaScore match data into GameState."""
        opponents = match_data.get("opponents", [])
        team1_data = opponents[0].get("opponent", {}) if len(opponents) > 0 else {}
        team2_data = opponents[1].get("opponent", {}) if len(opponents) > 1 else {}
        
        team1 = self.parse_team(team1_data)
        team2 = self.parse_team(team2_data)
        
        # Get results/scores
        results = match_data.get("results", [])
        team1_score = results[0].get("score", 0) if len(results) > 0 else 0
        team2_score = results[1].get("score", 0) if len(results) > 1 else 0
        
        # Determine series format
        number_of_games = match_data.get("number_of_games", 1)
        
        # Get current game info
        games = match_data.get("games", [])
        current_game = None
        game_number = 1
        
        for i, g in enumerate(games):
            if g.get("status") == "running":
                current_game = g
                game_number = i + 1
                break
        
        # Parse game-specific stats
        team1_kills = 0
        team2_kills = 0
        team1_gold = 0
        team2_gold = 0
        team1_towers = 0
        team2_towers = 0
        game_time = 0.0
        
        if current_game:
            # Extract stats from current game
            teams_stats = current_game.get("teams", [])
            if len(teams_stats) >= 2:
                team1_stats = teams_stats[0]
                team2_stats = teams_stats[1]
                
                team1_kills = team1_stats.get("kills", 0)
                team2_kills = team2_stats.get("kills", 0)
                team1_gold = team1_stats.get("gold_earned", 0)
                team2_gold = team2_stats.get("gold_earned", 0)
                team1_towers = team1_stats.get("tower_kills", 0)
                team2_towers = team2_stats.get("tower_kills", 0)
            
            # Game duration
            game_time = current_game.get("length", 0) or 0
            if game_time == 0:
                begin_at = current_game.get("begin_at")
                if begin_at:
                    start = datetime.fromisoformat(begin_at.replace("Z", "+00:00"))
                    game_time = self.calculate_game_duration(start.replace(tzinfo=None))
        
        return GameState(
            match_id=str(match_data.get("id", "")),
            game=game,
            team1=team1,
            team2=team2,
            game_number=game_number,
            game_time_seconds=game_time,
            team1_kills=team1_kills,
            team2_kills=team2_kills,
            team1_gold=team1_gold,
            team2_gold=team2_gold,
            team1_towers=team1_towers,
            team2_towers=team2_towers,
            team1_series_score=team1_score,
            team2_series_score=team2_score,
            series_format=number_of_games,
        )
    
    async def subscribe_to_match(self, match_id: str) -> AsyncIterator[GameEvent]:
        """
        Subscribe to match events via polling.
        PandaScore doesn't have WebSocket, so we poll for changes.
        """
        queue: asyncio.Queue[GameEvent | None] = asyncio.Queue()
        self._event_queues[match_id] = queue
        
        # Start polling task
        task = asyncio.create_task(self._poll_match_events(match_id, queue))
        self._polling_tasks[match_id] = task
        
        try:
            while True:
                event = await queue.get()
                if event is None:  # Sentinel to stop
                    break
                yield event
        finally:
            task.cancel()
            del self._event_queues[match_id]
            del self._polling_tasks[match_id]
    
    async def _poll_match_events(
        self, 
        match_id: str, 
        queue: asyncio.Queue
    ) -> None:
        """Poll for match state changes and emit events."""
        last_state: Optional[GameState] = None
        poll_interval = 0.25  # 250ms polling - faster for latency edge
        
        try:
            while True:
                try:
                    current_state = await self.get_match_state(match_id)
                    
                    if current_state is None:
                        # Match might have ended
                        await queue.put(None)
                        break
                    
                    if last_state is not None:
                        events = self._detect_state_changes(last_state, current_state)
                        for event in events:
                            await queue.put(event)
                    
                    last_state = current_state
                    
                except Exception as e:
                    logger.error(f"Error polling match {match_id}", error=str(e))
                
                await asyncio.sleep(poll_interval)
                
        except asyncio.CancelledError:
            pass
    
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
                team_id=new.team1.id,
                value=float(kill_diff_t1),
                details={"team_name": new.team1.name, "kills": kill_diff_t1}
            ))
        
        if kill_diff_t2 > 0:
            events.append(GameEvent(
                event_type="kill",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=float(kill_diff_t2),
                details={"team_name": new.team2.name, "kills": kill_diff_t2}
            ))
        
        # Detect tower/objective changes
        tower_diff_t1 = new.team1_towers - old.team1_towers
        tower_diff_t2 = new.team2_towers - old.team2_towers
        
        if tower_diff_t1 > 0:
            events.append(GameEvent(
                event_type="tower",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team1.id,
                value=float(tower_diff_t1) * 250,  # Gold value estimate
                details={"team_name": new.team1.name, "towers": tower_diff_t1}
            ))
        
        if tower_diff_t2 > 0:
            events.append(GameEvent(
                event_type="tower",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=new.team2.id,
                value=float(tower_diff_t2) * 250,
                details={"team_name": new.team2.name, "towers": tower_diff_t2}
            ))
        
        # Detect large gold swings (potential objectives)
        gold_swing = abs(new.gold_lead - old.gold_lead)
        if gold_swing > 1500:  # Significant gold swing
            winning_team = new.team1 if new.gold_lead > old.gold_lead else new.team2
            events.append(GameEvent(
                event_type="objective",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=winning_team.id,
                value=float(gold_swing),
                details={"team_name": winning_team.name, "gold_swing": gold_swing}
            ))
        
        # Detect potential game-ending situations (exit positions before resolution)
        # Tower lead of 10+ = all towers destroyed (game about to end)
        if abs(new.tower_lead) >= 10 and abs(old.tower_lead) < 10:
            likely_winner = new.team1 if new.tower_lead > 0 else new.team2
            events.append(GameEvent(
                event_type="game_ending",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=likely_winner.id,
                value=1.0,
                details={"team_name": likely_winner.name, "tower_lead": new.tower_lead, "should_exit": True}
            ))
        
        # Detect series score changes (game ended)
        if (new.team1_series_score != old.team1_series_score or 
            new.team2_series_score != old.team2_series_score):
            winner = new.team1 if new.team1_series_score > old.team1_series_score else new.team2
            events.append(GameEvent(
                event_type="game_end",
                timestamp=now,
                game_time_seconds=new.game_time_seconds,
                team_id=winner.id,
                value=1.0,
                details={
                    "winner": winner.name,
                    "series_score": f"{new.team1_series_score}-{new.team2_series_score}"
                }
            ))
        
        return events
    
    async def get_upcoming_matches(
        self, 
        game: Optional[Game] = None,
        hours_ahead: int = 24
    ) -> List[Dict]:
        """Get upcoming scheduled matches."""
        matches = []
        games_to_check = [game] if game else self.supported_games
        
        # Calculate time range
        now = datetime.utcnow()
        end_time = now + timedelta(hours=hours_ahead)
        
        for g in games_to_check:
            slug = self.GAME_SLUGS.get(g)
            if not slug:
                continue
            
            try:
                data = await self._request(
                    "GET",
                    f"/{slug}/matches/upcoming",
                    params={
                        "per_page": 50,
                        "range[begin_at]": f"{now.isoformat()},{end_time.isoformat()}"
                    }
                )
                
                for match in data:
                    match["game"] = g
                    matches.append(match)
                    
            except httpx.HTTPError as e:
                logger.error(f"Error fetching upcoming {slug} matches", error=str(e))
        
        return matches




