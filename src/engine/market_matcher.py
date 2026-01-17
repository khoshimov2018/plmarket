"""
Matches esports matches to Polymarket markets.
This is crucial for connecting live game data to the right trading opportunities.
"""

import re
from typing import Optional, Dict, List, Tuple
from difflib import SequenceMatcher

from src.models import Game, GameState, MarketInfo
from src.logger import get_logger


logger = get_logger("market_matcher")


class MarketMatcher:
    """
    Matches live esports matches to Polymarket markets.
    
    Uses fuzzy string matching and game context to find the right market
    for each live match we're tracking.
    """
    
    # Common team name variations
    TEAM_ALIASES = {
        # LoL teams
        "t1": ["skt", "sk telecom", "skt t1", "t1"],
        "geng": ["gen.g", "gen g", "geng", "samsung galaxy"],
        "dwg": ["damwon", "dwg kia", "dk", "damwon gaming"],
        "fnatic": ["fnc", "fnatic"],
        "g2": ["g2 esports", "g2"],
        "cloud9": ["c9", "cloud 9", "cloud9"],
        "team liquid": ["tl", "liquid", "team liquid"],
        "jdg": ["jd gaming", "jdg", "jd"],
        "weibo": ["weibo gaming", "wbg"],
        "bilibili": ["bilibili gaming", "blg", "bilibili"],
        
        # Dota teams
        "og": ["og", "og esports"],
        "team spirit": ["spirit", "team spirit", "ts"],
        "lgd": ["lgd gaming", "psg.lgd", "lgd"],
        "evil geniuses": ["eg", "evil geniuses"],
        "team secret": ["secret", "team secret"],
        "nigma": ["nigma galaxy", "nigma", "team nigma"],
        "tundra": ["tundra esports", "tundra"],
        "gaimin gladiators": ["gg", "gaimin", "gladiators"],
    }
    
    def __init__(self):
        # Build reverse lookup for aliases
        self._alias_lookup: Dict[str, str] = {}
        for canonical, aliases in self.TEAM_ALIASES.items():
            for alias in aliases:
                self._alias_lookup[alias.lower()] = canonical
    
    def match_market_to_game_state(
        self,
        markets: List[MarketInfo],
        game_state: GameState
    ) -> Optional[MarketInfo]:
        """
        Find the Polymarket market that corresponds to a live game state.
        
        Args:
            markets: List of available markets
            game_state: Current game state
            
        Returns:
            Matching market or None
        """
        team1_name = game_state.team1.name.lower()
        team2_name = game_state.team2.name.lower()
        
        best_match: Optional[MarketInfo] = None
        best_score = 0.0
        
        for market in markets:
            # Skip inactive markets
            if not market.is_active:
                continue
            
            # Check game type matches
            if market.game != game_state.game:
                continue
            
            # Score this market
            score = self._calculate_match_score(
                market.question,
                team1_name,
                team2_name,
            )
            
            if score > best_score and score >= 0.6:  # Minimum threshold
                best_score = score
                best_match = market
        
        if best_match:
            logger.debug(
                "Market matched",
                match_id=game_state.match_id,
                market_id=best_match.market_id,
                score=f"{best_score:.2f}",
            )
        
        return best_match
    
    def _calculate_match_score(
        self,
        question: str,
        team1: str,
        team2: str,
    ) -> float:
        """
        Calculate how well a market question matches team names.
        
        Args:
            question: Market question text
            team1: First team name
            team2: Second team name
            
        Returns:
            Match score from 0.0 to 1.0
        """
        question_lower = question.lower()
        
        # Normalize team names
        team1_canonical = self._normalize_team_name(team1)
        team2_canonical = self._normalize_team_name(team2)
        
        # Check for exact matches
        team1_found = self._find_team_in_text(team1_canonical, question_lower)
        team2_found = self._find_team_in_text(team2_canonical, question_lower)
        
        if team1_found and team2_found:
            return 1.0
        
        if team1_found or team2_found:
            return 0.7
        
        # Fall back to fuzzy matching
        words = re.findall(r'\b\w+\b', question_lower)
        
        team1_sim = max(
            SequenceMatcher(None, team1_canonical, word).ratio()
            for word in words
        ) if words else 0.0
        
        team2_sim = max(
            SequenceMatcher(None, team2_canonical, word).ratio()
            for word in words
        ) if words else 0.0
        
        return (team1_sim + team2_sim) / 2
    
    def _normalize_team_name(self, name: str) -> str:
        """Normalize team name to canonical form."""
        name_lower = name.lower().strip()
        
        # Check alias lookup
        if name_lower in self._alias_lookup:
            return self._alias_lookup[name_lower]
        
        # Check partial matches
        for alias, canonical in self._alias_lookup.items():
            if alias in name_lower or name_lower in alias:
                return canonical
        
        return name_lower
    
    def _find_team_in_text(self, team: str, text: str) -> bool:
        """Check if team name appears in text."""
        # Direct match
        if team in text:
            return True
        
        # Check all aliases
        if team in self.TEAM_ALIASES:
            for alias in self.TEAM_ALIASES[team]:
                if alias in text:
                    return True
        
        return False
    
    def extract_teams_from_question(self, question: str) -> Tuple[str, str]:
        """
        Try to extract team names from a market question.
        
        Args:
            question: Market question text
            
        Returns:
            Tuple of (team1, team2) or ("", "") if not found
        """
        # Common patterns:
        # "Will Team A beat Team B?"
        # "Team A vs Team B - who will win?"
        # "Team A to win against Team B"
        
        patterns = [
            r"will\s+(.+?)\s+(?:beat|defeat|win against)\s+(.+?)[\?\.]",
            r"(.+?)\s+vs\.?\s+(.+?)(?:\s*[-–—]\s*|\s+)",
            r"(.+?)\s+to\s+win\s+(?:against|vs\.?)\s+(.+?)[\?\.]",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, question.lower())
            if match:
                team1 = match.group(1).strip()
                team2 = match.group(2).strip()
                
                # Clean up common suffixes
                for suffix in ["?", ".", "to win", "winner"]:
                    team1 = team1.replace(suffix, "").strip()
                    team2 = team2.replace(suffix, "").strip()
                
                if team1 and team2:
                    return team1, team2
        
        return "", ""




