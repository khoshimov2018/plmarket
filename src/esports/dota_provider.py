"""
Dota 2 specific data provider.
Extends PandaScore with Dota-specific game state analysis.
"""

from datetime import datetime
from typing import Optional, List, Dict, Tuple

from src.models import Game, GameState, GameEvent
from src.esports.pandascore import PandaScoreProvider
from src.logger import get_logger


logger = get_logger("dota_provider")


class DotaConstants:
    """Game-specific constants for Dota 2 analysis."""
    
    # Gold values for objectives
    TOWER_GOLD = 200  # Base tower gold
    BARRACKS_GOLD = 225
    ROSHAN_GOLD = 400  # Approximate team gold value
    
    # Aegis value (prevents death)
    AEGIS_VALUE = 2000  # Estimated tactical value
    
    # Kill value (average)
    KILL_BASE_GOLD = 200
    STREAK_BONUS = 100  # Per kill streak level
    
    # Game phase thresholds (seconds)
    LANING_END = 600     # 10 minutes
    MID_GAME_END = 1800  # 30 minutes
    
    # Roshan respawn timer
    ROSHAN_MIN_RESPAWN = 480  # 8 minutes
    ROSHAN_MAX_RESPAWN = 660  # 11 minutes
    
    # Buyback considerations
    BUYBACK_THRESHOLD = 40 * 60  # 40 minutes


class DotaDataProvider(PandaScoreProvider):
    """
    Dota 2 specific data provider.
    Extends PandaScore with Dota-specific analytics.
    """
    
    @property
    def supported_games(self) -> List[Game]:
        return [Game.DOTA2]
    
    async def get_live_matches(self, game: Optional[Game] = None) -> List[Dict]:
        """Get currently live Dota 2 matches."""
        return await super().get_live_matches(Game.DOTA2)
    
    async def get_match_state(self, match_id: str) -> Optional[GameState]:
        """Get current state of a Dota 2 match with enhanced analysis."""
        state = await super().get_match_state(match_id)
        
        if state and state.game == Game.DOTA2:
            # Calculate win probabilities
            state.team1_win_prob, state.team2_win_prob = self._calculate_win_probability(state)
        
        return state
    
    def _calculate_win_probability(self, state: GameState) -> Tuple[float, float]:
        """
        Calculate win probability based on current Dota 2 game state.
        Dota has more comeback potential than LoL, so we're more conservative.
        
        Returns:
            Tuple of (team1_win_prob, team2_win_prob)
        """
        base_prob = 0.5
        
        # Determine game phase
        game_time = state.game_time_seconds
        if game_time < DotaConstants.LANING_END:
            phase = "laning"
            gold_weight = 0.10  # Early leads less meaningful
            comeback_factor = 0.85  # High comeback potential
        elif game_time < DotaConstants.MID_GAME_END:
            phase = "mid"
            gold_weight = 0.20
            comeback_factor = 0.75
        else:
            phase = "late"
            gold_weight = 0.30
            comeback_factor = 0.60  # Still significant comeback potential
        
        # Net worth (gold) factor
        total_gold = state.team1_gold + state.team2_gold
        if total_gold > 0:
            gold_ratio = state.gold_lead / max(total_gold, 1)
        else:
            gold_ratio = 0
        
        # Dota gold leads are less predictive due to buyback and comebacks
        gold_factor = gold_ratio * gold_weight * comeback_factor
        gold_factor = max(-0.35, min(0.35, gold_factor))
        
        # Kill score factor (Dota kills matter less than gold)
        kill_weight = 0.005  # Per kill difference
        kill_factor = state.kill_lead * kill_weight
        kill_factor = max(-0.10, min(0.10, kill_factor))
        
        # Tower/building factor
        tower_weight = 0.025  # Per tower difference
        tower_factor = state.tower_lead * tower_weight
        tower_factor = max(-0.15, min(0.15, tower_factor))
        
        # Series momentum
        series_factor = 0
        if state.series_format > 1:
            series_diff = state.team1_series_score - state.team2_series_score
            series_factor = series_diff * 0.04
        
        # Combine factors
        team1_prob = base_prob + gold_factor + kill_factor + tower_factor + series_factor
        
        # Apply late-game high ground factor
        # Defending high ground is advantageous in Dota
        if game_time >= DotaConstants.MID_GAME_END:
            if state.gold_lead > 0 and state.tower_lead < 6:
                # Team 1 ahead but hasn't broken high ground
                team1_prob *= 0.95  # Slight reduction
            elif state.gold_lead < 0 and state.tower_lead > -6:
                # Team 2 ahead but hasn't broken high ground
                team1_prob *= 1.05  # Slight increase for team 1
        
        # Clamp to valid range
        team1_prob = max(0.08, min(0.92, team1_prob))
        team2_prob = 1 - team1_prob
        
        logger.debug(
            "win_probability_calculated",
            match_id=state.match_id,
            phase=phase,
            gold_factor=f"{gold_factor:.3f}",
            team1_prob=f"{team1_prob:.2%}",
        )
        
        return team1_prob, team2_prob
    
    def analyze_event_impact(
        self, 
        event: GameEvent, 
        state: GameState
    ) -> float:
        """
        Analyze the impact of a Dota 2 game event on win probability.
        
        Args:
            event: The game event to analyze
            state: Current game state
            
        Returns:
            Estimated change in win probability for the event's team
        """
        base_impact = 0.0
        
        event_type = event.event_type.lower()
        game_time = state.game_time_seconds
        
        # Time multiplier - different curve for Dota
        if game_time < DotaConstants.LANING_END:
            time_mult = 0.6
        elif game_time < DotaConstants.MID_GAME_END:
            time_mult = 1.0
        else:
            time_mult = 1.4
        
        if event_type == "kill":
            kills = event.details.get("kills", 1)
            base_impact = 0.008 * kills  # Lower per-kill impact than LoL
            
            # Team wipe
            if kills >= 5:
                base_impact *= 2.0
                
        elif event_type == "tower":
            towers = event.details.get("towers", 1)
            base_impact = 0.012 * towers
            
            # Barracks are huge in Dota
            if event.details.get("barracks"):
                base_impact = 0.06
                
        elif event_type == "objective":
            gold_value = event.value
            
            # Roshan (Aegis holder)
            if gold_value >= 2000 or event.details.get("roshan"):
                base_impact = 0.06  # Roshan is very important
            elif gold_value >= 1000:
                base_impact = 0.03
            else:
                base_impact = 0.015
        
        elif event_type == "game_end":
            base_impact = 0.15
        
        # Apply time multiplier
        final_impact = base_impact * time_mult
        
        return max(-0.20, min(0.20, final_impact))
    
    def detect_critical_moment(self, state: GameState) -> Optional[str]:
        """
        Detect if the Dota 2 game is at a critical moment.
        
        Returns:
            Description of critical moment or None
        """
        game_time = state.game_time_seconds
        
        # Roshan timing windows (approximate - would need actual Rosh timer)
        roshan_windows = [
            (DotaConstants.ROSHAN_MIN_RESPAWN, DotaConstants.ROSHAN_MAX_RESPAWN),
            (DotaConstants.ROSHAN_MIN_RESPAWN * 2, DotaConstants.ROSHAN_MAX_RESPAWN * 2),
            (DotaConstants.ROSHAN_MIN_RESPAWN * 3, DotaConstants.ROSHAN_MAX_RESPAWN * 3),
        ]
        
        for min_time, max_time in roshan_windows:
            if min_time <= game_time <= max_time:
                return "roshan_window"
        
        # Late game with buybacks available
        if game_time >= DotaConstants.BUYBACK_THRESHOLD:
            return "buyback_territory"
        
        # Mega creeps potential
        if state.tower_lead >= 8 or state.tower_lead <= -8:
            return "mega_creeps_threat"
        
        # High ground siege
        if game_time >= DotaConstants.MID_GAME_END:
            if abs(state.gold_lead) >= 8000:
                return "high_ground_siege"
        
        # Comeback potential
        if state.gold_lead <= -15000 and game_time >= DotaConstants.MID_GAME_END:
            return "comeback_potential"
        
        return None




