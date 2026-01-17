"""
League of Legends specific data provider.
Extends PandaScore with LoL-specific game state analysis.
"""

from datetime import datetime
from typing import Optional, List, Dict, Tuple

from src.models import Game, GameState, GameEvent
from src.esports.pandascore import PandaScoreProvider
from src.logger import get_logger


logger = get_logger("lol_provider")


# LoL-specific constants for win probability calculation
class LoLConstants:
    """Game-specific constants for League of Legends analysis."""
    
    # Gold values for objectives
    TOWER_GOLD = 250
    DRAGON_GOLD = 200  # Per player average
    BARON_GOLD = 1500  # Team gold
    ELDER_DRAGON_GOLD = 450
    RIFT_HERALD_GOLD = 400
    
    # Kill value (average)
    KILL_BASE_GOLD = 300
    ASSIST_GOLD = 150
    
    # Game phase thresholds (seconds)
    EARLY_GAME_END = 900  # 15 minutes
    MID_GAME_END = 1800   # 30 minutes
    
    # Win probability weights by game phase
    EARLY_GOLD_WEIGHT = 0.15
    MID_GOLD_WEIGHT = 0.25
    LATE_GOLD_WEIGHT = 0.35
    
    # Objective multipliers
    DRAGON_SOUL_MULTIPLIER = 1.3
    BARON_BUFF_MULTIPLIER = 1.2
    ELDER_MULTIPLIER = 1.5


class LoLDataProvider(PandaScoreProvider):
    """
    League of Legends specific data provider.
    Extends PandaScore with LoL-specific analytics.
    """
    
    @property
    def supported_games(self) -> List[Game]:
        return [Game.LOL]
    
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """Get currently live LoL matches."""
        return await super().get_live_matches(Game.LOL)
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """Get current state of a LoL match with enhanced analysis."""
        state = await super().get_match_state(match_id)
        
        if state and state.game == Game.LOL:
            # Calculate win probabilities
            state.team1_win_prob, state.team2_win_prob = self._calculate_win_probability(state)
        
        return state
    
    def _calculate_win_probability(self, state: GameState) -> Tuple[float, float]:
        """
        Calculate win probability based on current game state.
        Uses gold lead, kill lead, objective control, and game time.
        
        Returns:
            Tuple of (team1_win_prob, team2_win_prob)
        """
        # Base probability starts at 50/50
        base_prob = 0.5
        
        # Determine game phase
        game_time = state.game_time_seconds
        if game_time < LoLConstants.EARLY_GAME_END:
            phase = "early"
            gold_weight = LoLConstants.EARLY_GOLD_WEIGHT
        elif game_time < LoLConstants.MID_GAME_END:
            phase = "mid"
            gold_weight = LoLConstants.MID_GOLD_WEIGHT
        else:
            phase = "late"
            gold_weight = LoLConstants.LATE_GOLD_WEIGHT
        
        # Calculate gold lead factor
        # Normalize gold lead - at 10k gold lead, this gives ~0.25 advantage
        total_gold = state.team1_gold + state.team2_gold
        if total_gold > 0:
            gold_factor = state.gold_lead / max(total_gold, 1) * 2
        else:
            gold_factor = 0
        
        # Clamp gold factor
        gold_factor = max(-0.4, min(0.4, gold_factor * gold_weight / 0.25))
        
        # Kill lead factor (smaller impact)
        kill_weight = 0.008  # Per kill difference
        kill_factor = state.kill_lead * kill_weight
        kill_factor = max(-0.15, min(0.15, kill_factor))
        
        # Tower lead factor
        tower_weight = 0.03  # Per tower difference
        tower_factor = state.tower_lead * tower_weight
        tower_factor = max(-0.2, min(0.2, tower_factor))
        
        # Series score factor (for Bo3, Bo5)
        series_factor = 0
        if state.series_format > 1:
            series_diff = state.team1_series_score - state.team2_series_score
            games_remaining = state.series_format // 2 + 1 - max(
                state.team1_series_score, state.team2_series_score
            )
            if games_remaining > 0:
                series_factor = series_diff * 0.05  # Small series momentum factor
        
        # Combine factors
        team1_prob = base_prob + gold_factor + kill_factor + tower_factor + series_factor
        
        # Clamp to valid probability range
        team1_prob = max(0.05, min(0.95, team1_prob))
        team2_prob = 1 - team1_prob
        
        logger.debug(
            "win_probability_calculated",
            match_id=state.match_id,
            phase=phase,
            gold_factor=f"{gold_factor:.3f}",
            kill_factor=f"{kill_factor:.3f}",
            tower_factor=f"{tower_factor:.3f}",
            team1_prob=f"{team1_prob:.2%}",
        )
        
        return team1_prob, team2_prob
    
    def analyze_event_impact(
        self, 
        event: GameEvent, 
        state: GameState
    ) -> float:
        """
        Analyze the impact of a game event on win probability.
        
        Args:
            event: The game event to analyze
            state: Current game state
            
        Returns:
            Estimated change in win probability for the event's team
        """
        base_impact = 0.0
        
        event_type = event.event_type.lower()
        game_time = state.game_time_seconds
        
        # Time multiplier - events matter more in late game
        if game_time < LoLConstants.EARLY_GAME_END:
            time_mult = 0.7
        elif game_time < LoLConstants.MID_GAME_END:
            time_mult = 1.0
        else:
            time_mult = 1.3
        
        if event_type == "kill":
            # Single kill impact
            kills = event.details.get("kills", 1)
            base_impact = 0.01 * kills  # 1% per kill
            
            # Check for potential ace (5 kills)
            if kills >= 5:
                base_impact *= 1.5  # Ace bonus
            
        elif event_type == "tower":
            towers = event.details.get("towers", 1)
            base_impact = 0.015 * towers  # 1.5% per tower
            
            # Inhibitor tower is more valuable
            # (We'd need more detailed data to know which tower)
            
        elif event_type == "objective":
            gold_value = event.value
            
            # Large objectives (Baron, Elder, Soul)
            if gold_value >= 3000:
                base_impact = 0.08  # Major objective
            elif gold_value >= 1500:
                base_impact = 0.05  # Baron/Elder
            elif gold_value >= 800:
                base_impact = 0.025  # Dragon
            else:
                base_impact = 0.015  # Rift Herald or other
        
        elif event_type == "game_end":
            # Game in series ended - major impact
            base_impact = 0.15  # Winning a game is huge
        
        # Apply time multiplier
        final_impact = base_impact * time_mult
        
        # Clamp impact
        return max(-0.25, min(0.25, final_impact))
    
    def detect_critical_moment(self, state: GameState) -> Optional[str]:
        """
        Detect if the game is at a critical moment where odds might shift dramatically.
        
        Returns:
            Description of critical moment or None
        """
        game_time = state.game_time_seconds
        
        # Baron spawn time check (20 minutes)
        if 1180 <= game_time <= 1260:  # ~20 min window
            return "baron_spawn_soon"
        
        # Elder dragon potential (35+ minutes with significant dragon control)
        if game_time >= 2100:
            return "elder_dragon_potential"
        
        # Very late game - one fight could end it
        if game_time >= 2400:  # 40+ minutes
            if abs(state.gold_lead) < 3000:
                return "close_late_game"
        
        # Significant lead that could close soon
        if state.gold_lead >= 10000 and state.tower_lead >= 3:
            return "dominant_position"
        elif state.gold_lead <= -10000 and state.tower_lead <= -3:
            return "desperate_position"
        
        return None




