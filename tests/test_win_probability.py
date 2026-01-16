"""
Tests for win probability calculation models.
"""

import pytest
from datetime import datetime

from src.models import Game, GameState, Team
from src.esports.lol_provider import LoLDataProvider, LoLConstants
from src.esports.dota_provider import DotaDataProvider, DotaConstants


@pytest.fixture
def lol_provider():
    """Create LoL provider for testing."""
    return LoLDataProvider("")


@pytest.fixture
def dota_provider():
    """Create Dota provider for testing."""
    return DotaDataProvider("")


@pytest.fixture
def base_game_state():
    """Create a base game state for testing."""
    return GameState(
        match_id="test_match",
        game=Game.LOL,
        team1=Team(id="t1", name="Team 1", short_name="T1"),
        team2=Team(id="t2", name="Team 2", short_name="T2"),
        game_number=1,
        game_time_seconds=0,
    )


class TestLoLWinProbability:
    """Tests for LoL win probability calculation."""
    
    def test_even_game_returns_fifty_fifty(self, lol_provider, base_game_state):
        """Test that an even game returns ~50% probability."""
        base_game_state.team1_gold = 20000
        base_game_state.team2_gold = 20000
        base_game_state.team1_kills = 5
        base_game_state.team2_kills = 5
        
        p1, p2 = lol_provider._calculate_win_probability(base_game_state)
        
        assert abs(p1 - 0.5) < 0.05
        assert abs(p2 - 0.5) < 0.05
        assert abs(p1 + p2 - 1.0) < 0.001
    
    def test_gold_lead_increases_probability(self, lol_provider, base_game_state):
        """Test that gold lead increases win probability."""
        base_game_state.game_time_seconds = 1200  # 20 minutes
        base_game_state.team1_gold = 35000
        base_game_state.team2_gold = 28000  # 7k gold lead
        
        p1, p2 = lol_provider._calculate_win_probability(base_game_state)
        
        assert p1 > 0.5
        assert p1 < 0.95  # Shouldn't be too extreme
    
    def test_kill_lead_increases_probability(self, lol_provider, base_game_state):
        """Test that kill lead increases win probability."""
        base_game_state.game_time_seconds = 1200
        base_game_state.team1_gold = 30000
        base_game_state.team2_gold = 30000
        base_game_state.team1_kills = 15
        base_game_state.team2_kills = 5
        
        p1, p2 = lol_provider._calculate_win_probability(base_game_state)
        
        assert p1 > 0.5
    
    def test_tower_lead_increases_probability(self, lol_provider, base_game_state):
        """Test that tower lead increases win probability."""
        base_game_state.game_time_seconds = 1200
        base_game_state.team1_gold = 30000
        base_game_state.team2_gold = 30000
        base_game_state.team1_towers = 5
        base_game_state.team2_towers = 1
        
        p1, p2 = lol_provider._calculate_win_probability(base_game_state)
        
        assert p1 > 0.5
    
    def test_late_game_gold_matters_more(self, lol_provider, base_game_state):
        """Test that gold lead matters more in late game."""
        # Same gold lead at different game phases
        base_game_state.team1_gold = 30000
        base_game_state.team2_gold = 25000
        
        # Early game
        base_game_state.game_time_seconds = 600
        p1_early, _ = lol_provider._calculate_win_probability(base_game_state)
        
        # Late game
        base_game_state.game_time_seconds = 2400
        p1_late, _ = lol_provider._calculate_win_probability(base_game_state)
        
        # Late game probability should be higher
        assert p1_late > p1_early
    
    def test_probability_clamped(self, lol_provider, base_game_state):
        """Test that probability is clamped to reasonable range."""
        # Extreme lead
        base_game_state.team1_gold = 100000
        base_game_state.team2_gold = 20000
        base_game_state.team1_kills = 50
        base_game_state.team2_kills = 5
        base_game_state.team1_towers = 11
        base_game_state.team2_towers = 0
        
        p1, p2 = lol_provider._calculate_win_probability(base_game_state)
        
        assert p1 <= 0.95
        assert p2 >= 0.05


class TestDotaWinProbability:
    """Tests for Dota 2 win probability calculation."""
    
    def test_even_game_returns_fifty_fifty(self, dota_provider, base_game_state):
        """Test that an even game returns ~50% probability."""
        base_game_state.game = Game.DOTA2
        base_game_state.team1_gold = 20000
        base_game_state.team2_gold = 20000
        
        p1, p2 = dota_provider._calculate_win_probability(base_game_state)
        
        assert abs(p1 - 0.5) < 0.05
        assert abs(p2 - 0.5) < 0.05
    
    def test_dota_has_more_comeback_potential(self, dota_provider, lol_provider, base_game_state):
        """Test that Dota has more comeback potential than LoL."""
        # Same gold lead
        base_game_state.team1_gold = 40000
        base_game_state.team2_gold = 30000
        base_game_state.game_time_seconds = 1800
        
        # LoL probability
        base_game_state.game = Game.LOL
        p1_lol, _ = lol_provider._calculate_win_probability(base_game_state)
        
        # Dota probability (should be less extreme due to comeback mechanics)
        base_game_state.game = Game.DOTA2
        p1_dota, _ = dota_provider._calculate_win_probability(base_game_state)
        
        # Dota should give less advantage for same lead
        assert p1_dota < p1_lol
    
    def test_probability_clamped(self, dota_provider, base_game_state):
        """Test that probability is clamped for Dota."""
        base_game_state.game = Game.DOTA2
        base_game_state.team1_gold = 100000
        base_game_state.team2_gold = 20000
        
        p1, p2 = dota_provider._calculate_win_probability(base_game_state)
        
        assert p1 <= 0.92  # Dota clamps lower
        assert p2 >= 0.08


class TestEventImpact:
    """Tests for game event impact analysis."""
    
    def test_kill_event_impact(self, lol_provider, base_game_state):
        """Test impact of kill events."""
        from src.models import GameEvent
        
        event = GameEvent(
            event_type="kill",
            timestamp=datetime.utcnow(),
            game_time_seconds=1200,
            team_id="t1",
            value=300,
            details={"kills": 1},
        )
        
        impact = lol_provider.analyze_event_impact(event, base_game_state)
        
        assert impact > 0  # Positive impact for killing team
        assert impact < 0.10  # But not too extreme for single kill
    
    def test_multi_kill_higher_impact(self, lol_provider, base_game_state):
        """Test that multi-kills have higher impact."""
        from src.models import GameEvent
        
        single_kill = GameEvent(
            event_type="kill",
            timestamp=datetime.utcnow(),
            game_time_seconds=1200,
            team_id="t1",
            value=300,
            details={"kills": 1},
        )
        
        triple_kill = GameEvent(
            event_type="kill",
            timestamp=datetime.utcnow(),
            game_time_seconds=1200,
            team_id="t1",
            value=900,
            details={"kills": 3},
        )
        
        single_impact = lol_provider.analyze_event_impact(single_kill, base_game_state)
        triple_impact = lol_provider.analyze_event_impact(triple_kill, base_game_state)
        
        assert triple_impact > single_impact
    
    def test_objective_event_impact(self, lol_provider, base_game_state):
        """Test impact of objective events."""
        from src.models import GameEvent
        
        baron_event = GameEvent(
            event_type="objective",
            timestamp=datetime.utcnow(),
            game_time_seconds=1800,
            team_id="t1",
            value=3000,  # Baron-level gold swing
            details={},
        )
        
        impact = lol_provider.analyze_event_impact(baron_event, base_game_state)
        
        assert impact >= 0.05  # Baron should have significant impact
    
    def test_late_game_events_matter_more(self, lol_provider, base_game_state):
        """Test that events matter more in late game."""
        from src.models import GameEvent
        
        event = GameEvent(
            event_type="kill",
            timestamp=datetime.utcnow(),
            game_time_seconds=600,  # Early
            team_id="t1",
            value=300,
            details={"kills": 1},
        )
        
        # Early game
        base_game_state.game_time_seconds = 600
        early_impact = lol_provider.analyze_event_impact(event, base_game_state)
        
        # Late game
        base_game_state.game_time_seconds = 2400
        event.game_time_seconds = 2400
        late_impact = lol_provider.analyze_event_impact(event, base_game_state)
        
        assert late_impact > early_impact


class TestCriticalMoments:
    """Tests for critical moment detection."""
    
    def test_baron_spawn_detected(self, lol_provider, base_game_state):
        """Test detection of Baron spawn timing."""
        base_game_state.game_time_seconds = 1200  # 20 minutes
        
        moment = lol_provider.detect_critical_moment(base_game_state)
        
        assert moment == "baron_spawn_soon"
    
    def test_close_late_game_detected(self, lol_provider, base_game_state):
        """Test detection of close late game."""
        base_game_state.game_time_seconds = 2500
        base_game_state.team1_gold = 60000
        base_game_state.team2_gold = 58000  # Close
        
        moment = lol_provider.detect_critical_moment(base_game_state)
        
        assert moment == "close_late_game"
    
    def test_dominant_position_detected(self, lol_provider, base_game_state):
        """Test detection of dominant position."""
        base_game_state.team1_gold = 50000
        base_game_state.team2_gold = 35000  # 15k lead
        base_game_state.team1_towers = 8
        base_game_state.team2_towers = 2
        
        moment = lol_provider.detect_critical_moment(base_game_state)
        
        assert moment == "dominant_position"




