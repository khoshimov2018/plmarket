"""
Tests for arbitrage detection logic.
"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.models import (
    Game, GameState, GameEvent, Team, MarketInfo, Side
)
from src.engine.arbitrage_detector import ArbitrageDetector


@pytest.fixture
def detector():
    """Create an arbitrage detector for testing."""
    return ArbitrageDetector()


@pytest.fixture
def sample_game_state():
    """Create a sample game state."""
    return GameState(
        match_id="test_match_1",
        game=Game.LOL,
        team1=Team(id="t1", name="Team Alpha", short_name="TA"),
        team2=Team(id="t2", name="Team Beta", short_name="TB"),
        game_number=1,
        game_time_seconds=1200.0,  # 20 minutes
        team1_kills=10,
        team2_kills=5,
        team1_gold=35000,
        team2_gold=28000,
        team1_towers=3,
        team2_towers=1,
        team1_win_prob=0.65,
        team2_win_prob=0.35,
    )


@pytest.fixture
def sample_market():
    """Create a sample market."""
    return MarketInfo(
        market_id="market_1",
        condition_id="cond_1",
        question="Will Team Alpha beat Team Beta?",
        token_id_yes="yes_token",
        token_id_no="no_token",
        match_id="test_match_1",
        game=Game.LOL,
        team1_name="Team Alpha",
        team2_name="Team Beta",
        yes_price=0.55,  # Market thinks 55%
        no_price=0.45,
    )


class TestArbitrageDetector:
    """Tests for ArbitrageDetector class."""
    
    def test_detect_opportunity_with_edge(self, detector, sample_game_state, sample_market):
        """Test that opportunities are detected when edge exceeds threshold."""
        # Model says 65%, market says 55% -> 10% edge
        opportunity = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert opportunity is not None
        assert opportunity.edge >= 0.02  # Min threshold
        assert opportunity.side == Side.BUY
        assert opportunity.target_token == "yes"
    
    def test_no_opportunity_when_prices_aligned(self, detector, sample_game_state, sample_market):
        """Test that no opportunity is detected when prices match."""
        sample_game_state.team1_win_prob = 0.55
        sample_game_state.team2_win_prob = 0.45
        
        opportunity = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert opportunity is None
    
    def test_opportunity_for_underpriced_team2(self, detector, sample_game_state, sample_market):
        """Test detecting opportunity when team 2 is underpriced."""
        sample_game_state.team1_win_prob = 0.35
        sample_game_state.team2_win_prob = 0.65
        sample_market.yes_price = 0.55
        sample_market.no_price = 0.45
        
        opportunity = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert opportunity is not None
        assert opportunity.target_token == "no"
    
    def test_cooldown_prevents_duplicate_opportunities(self, detector, sample_game_state, sample_market):
        """Test that cooldown prevents detecting same opportunity twice."""
        # First detection
        opp1 = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        # Immediate second detection should be None (cooldown)
        opp2 = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert opp1 is not None
        assert opp2 is None
    
    def test_event_opportunity_detection(self, detector, sample_game_state, sample_market):
        """Test detecting opportunity from game event."""
        event = GameEvent(
            event_type="kill",
            timestamp=datetime.utcnow(),
            game_time_seconds=1200.0,
            team_id="t1",
            value=300.0,
            details={"team_name": "Team Alpha", "kills": 3},
        )
        
        # Clear cooldown
        detector._recent_opportunities.clear()
        
        opportunity = detector.detect_event_opportunity(
            game_state=sample_game_state,
            market=sample_market,
            event=event,
            prob_change=0.05,  # 5% probability change
        )
        
        assert opportunity is not None
        assert opportunity.triggering_event == event
    
    def test_opportunity_expiration(self, detector, sample_game_state, sample_market):
        """Test that opportunities have expiration times."""
        opportunity = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert opportunity is not None
        assert opportunity.expires_at is not None
        assert opportunity.expires_at > datetime.utcnow()


class TestEdgeCalculation:
    """Tests for edge calculation logic."""
    
    def test_edge_calculation_buy_yes(self, detector, sample_game_state, sample_market):
        """Test edge calculation for buying YES token."""
        sample_game_state.team1_win_prob = 0.70
        sample_market.yes_price = 0.60
        
        detector._recent_opportunities.clear()
        
        opportunity = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert opportunity is not None
        assert abs(opportunity.edge - 0.10) < 0.01  # ~10% edge
    
    def test_recommended_size_scales_with_edge(self, detector, sample_game_state, sample_market):
        """Test that recommended size increases with edge."""
        # Small edge
        sample_game_state.team1_win_prob = 0.58
        sample_market.yes_price = 0.55
        detector._recent_opportunities.clear()
        
        opp_small = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        # Large edge
        sample_game_state.team1_win_prob = 0.75
        detector._recent_opportunities.clear()
        
        opp_large = detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        if opp_small and opp_large:
            assert opp_large.recommended_size > opp_small.recommended_size


class TestMetrics:
    """Tests for detector metrics tracking."""
    
    def test_metrics_tracking(self, detector, sample_game_state, sample_market):
        """Test that metrics are properly tracked."""
        initial_count = detector.metrics["opportunities_found"]
        
        detector._recent_opportunities.clear()
        detector.detect_opportunity(
            game_state=sample_game_state,
            market=sample_market,
        )
        
        assert detector.metrics["opportunities_found"] == initial_count + 1
    
    def test_cleanup_old_opportunities(self, detector):
        """Test cleanup of old opportunity cache."""
        # Add old entry
        from datetime import timedelta
        old_time = datetime.utcnow() - timedelta(minutes=10)
        detector._recent_opportunities["old_key"] = old_time
        
        # Add recent entry
        detector._recent_opportunities["new_key"] = datetime.utcnow()
        
        # Cleanup
        detector.cleanup_old_opportunities()
        
        assert "old_key" not in detector._recent_opportunities
        assert "new_key" in detector._recent_opportunities




