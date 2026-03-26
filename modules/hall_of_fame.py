"""
Hall of Fame module for tracking tournament winners and participant match wins.
Stores persistent data in JSON format.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from ezcord import log

HOF_FILE = "hall_of_fame.json"


def _parse_record_component(value: str) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return 0


def _record_tuple(match_record: str) -> tuple[int, int, int]:
    if not isinstance(match_record, str):
        return 0, 0, 0
    parts = match_record.split("-")
    if len(parts) < 3:
        return 0, 0, 0
    return (
        _parse_record_component(parts[0]),
        _parse_record_component(parts[1]),
        _parse_record_component(parts[2]),
    )


def _safe_datetime_sort_key(value: str):
    if not isinstance(value, str):
        return datetime.min
    normalized = value.strip()
    if not normalized:
        return datetime.min
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min

class ParticipantStats:
    """Represents statistics for a tournament participant."""
    
    def __init__(self, user_id: int, username: str):
        self.user_id = user_id
        self.username = username
        self.total_match_wins = 0
        self.total_match_losses = 0
        self.total_draws = 0
        self.tournament_participations = 0
        self.tournament_winners = 0  # Number of tournaments won
        self.tournament_top3s = 0    # Number of top-3 finishes
        self.tournament_top8s = 0    # Number of top-8 finishes
        self.last_updated = datetime.now().isoformat()
        self.match_history = []  # List of matches played
    
    def add_match(self, result: str):
        """Add a match result: 'win', 'loss', or 'draw'."""
        if result == "win":
            self.total_match_wins += 1
        elif result == "loss":
            self.total_match_losses += 1
        elif result == "draw":
            self.total_draws += 1
        self.last_updated = datetime.now().isoformat()
    
    def add_tournament(self, placement: int, placement_text: str = ""):
        """Record tournament participation."""
        self.tournament_participations += 1
        if placement == 1:
            self.tournament_winners += 1
        elif placement <= 3:
            self.tournament_top3s += 1
        elif placement <= 8:
            self.tournament_top8s += 1
        self.last_updated = datetime.now().isoformat()
    
    def get_match_record(self) -> str:
        """Get the match record as a formatted string (W-L-D)."""
        return f"{self.total_match_wins}-{self.total_match_losses}-{self.total_draws}"
    
    def get_match_win_percentage(self) -> float:
        """Calculate match win percentage."""
        total_matches = self.total_match_wins + self.total_match_losses + self.total_draws
        if total_matches == 0:
            return 0.0
        return (self.total_match_wins + self.total_draws * 0.5) / total_matches
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "total_match_wins": self.total_match_wins,
            "total_match_losses": self.total_match_losses,
            "total_draws": self.total_draws,
            "tournament_participations": self.tournament_participations,
            "tournament_winners": self.tournament_winners,
            "tournament_top3s": self.tournament_top3s,
            "tournament_top8s": self.tournament_top8s,
            "last_updated": self.last_updated,
            "match_history": self.match_history,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ParticipantStats":
        """Create a ParticipantStats from a dictionary."""
        stats = cls(data["user_id"], data["username"])
        stats.total_match_wins = data.get("total_match_wins", 0)
        stats.total_match_losses = data.get("total_match_losses", 0)
        stats.total_draws = data.get("total_draws", 0)
        stats.tournament_participations = data.get("tournament_participations", 0)
        stats.tournament_winners = data.get("tournament_winners", 0)
        stats.tournament_top3s = data.get("tournament_top3s", 0)
        stats.tournament_top8s = data.get("tournament_top8s", 0)
        stats.last_updated = data.get("last_updated", datetime.now().isoformat())
        stats.match_history = data.get("match_history", [])
        return stats


class TournamentRecord:
    """Represents a single tournament entry in the hall of fame."""
    
    def __init__(self, tournament_id: str, tournament_title: str, 
                 guild_id: int, start_date: str, standings_url: str = ""):
        self.tournament_id = tournament_id
        self.tournament_title = tournament_title
        self.guild_id = guild_id
        self.start_date = start_date  # ISO format
        self.standings_url = standings_url
        self.results: List[Dict] = []  # List of {user_id, placement, match_record}
    
    def add_result(self, user_id: int, username: str, placement: int, 
                   match_record: str):
        """Add a tournament result."""
        self.results.append({
            "user_id": user_id,
            "username": username,
            "placement": placement,
            "match_record": match_record
        })
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "tournament_id": self.tournament_id,
            "tournament_title": self.tournament_title,
            "guild_id": self.guild_id,
            "start_date": self.start_date,
            "standings_url": self.standings_url,
            "results": self.results
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "TournamentRecord":
        """Create a TournamentRecord from a dictionary."""
        record = cls(
            data["tournament_id"],
            data["tournament_title"],
            data["guild_id"],
            data.get("start_date") or data.get("completed_date", ""),
            data.get("standings_url", ""),
        )
        record.results = data.get("results", [])
        return record


class HallOfFame:
    """Manages the hall of fame for tournament statistics."""
    
    def __init__(self):
        self.participants: Dict[int, ParticipantStats] = {}
        self.tournaments: List[TournamentRecord] = []
        self.load_from_file()
    
    def load_from_file(self):
        """Load hall of fame data from JSON file."""
        if os.path.exists(HOF_FILE):
            try:
                with open(HOF_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Load participants
                for user_id_str, stats_dict in data.get("participants", {}).items():
                    user_id = int(user_id_str)
                    self.participants[user_id] = ParticipantStats.from_dict(stats_dict)
                
                # Load tournament records
                for tourney_dict in data.get("tournaments", []):
                    self.tournaments.append(TournamentRecord.from_dict(tourney_dict))
                
                log.debug(f"Hall of Fame geladen: {len(self.participants)} Teilnehmer, {len(self.tournaments)} Turniere")
            except Exception as e:
                log.error(f"Failed to load hall of fame: {e}")
    
    def save_to_file(self):
        """Save hall of fame data to JSON file."""
        try:
            data = {
                "participants": {str(uid): stats.to_dict() for uid, stats in self.participants.items()},
                "tournaments": [t.to_dict() for t in self.tournaments]
            }
            with open(HOF_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.debug("Hall of Fame gespeichert")
        except Exception as e:
            log.error(f"Failed to save hall of fame: {e}")
    
    def add_participant(self, user_id: int, username: str) -> ParticipantStats:
        """Add or get a participant."""
        if user_id not in self.participants:
            self.participants[user_id] = ParticipantStats(user_id, username)
        else:
            # Update username if it changed
            self.participants[user_id].username = username
        return self.participants[user_id]
    
    def get_participant(self, user_id: int) -> Optional[ParticipantStats]:
        """Get a participant by user ID."""
        return self.participants.get(user_id)
    
    def add_tournament_result(self, tournament_id: str, tournament_title: str,
                             guild_id: int, start_date: str,
                             results: List[Dict], *, standings_url: str = "", save: bool = True):
        """
        Add a tournament result to the hall of fame.
        
        Args:
            tournament_id: Unique tournament identifier
            tournament_title: Display name of the tournament
            guild_id: Discord guild ID
            start_date: ISO format date string
            standings_url: Discord jump URL to the final standings message
            results: List of dicts with keys:
                - user_id: Discord user ID
                - username: Discord username
                - placement: Tournament placement (1, 2, 3, etc.)
                - match_record: Match record string (e.g., "5-1-0")
                - matches: List of match results (optional)
        """
        if self.get_tournament_results(tournament_id):
            log.debug(f"Hall of fame already contains tournament {tournament_id}, skipping")
            return

        # Create tournament record
        tournament = TournamentRecord(tournament_id, tournament_title,
                                     guild_id, start_date, standings_url)
        
        # Update participant stats and tournament record
        for result in sorted(results, key=lambda x: x["placement"]):
            user_id = result["user_id"]
            username = result["username"]
            placement = result["placement"]
            match_record = result["match_record"]
            
            # Add to tournament record
            tournament.add_result(user_id, username, placement, match_record)
            
            # Update participant stats
            participant = self.add_participant(user_id, username)
            participant.add_tournament(placement)
            
            # Add individual match results if provided
            if "matches" in result and result["matches"]:
                for match_result in result["matches"]:
                    participant.add_match(match_result)
            else:
                # Parse match record if matches not provided
                wins, losses, draws = _record_tuple(match_record)
                for _ in range(wins):
                    participant.add_match("win")
                for _ in range(losses):
                    participant.add_match("loss")
                for _ in range(draws):
                    participant.add_match("draw")
        
        self.tournaments.append(tournament)
        if save:
            self.save_to_file()
    
    def get_top_participants(self, limit: int = 10, 
                            sort_by: str = "match_wins") -> List[ParticipantStats]:
        """
        Get top participants sorted by a specific criteria.
        
        Args:
            limit: Maximum number of results
            sort_by: Sort criteria - 'match_wins', 'match_winrate', 'tournaments', 'top3s', 'top8s'
        
        Returns:
            Sorted list of top participants
        """
        participants_list = list(self.participants.values())
        
        if sort_by == "match_wins":
            participants_list.sort(key=lambda x: x.total_match_wins, reverse=True)
        elif sort_by == "match_winrate":
            participants_list.sort(key=lambda x: (x.get_match_win_percentage(), x.total_match_wins), reverse=True)
        elif sort_by == "tournaments":
            participants_list.sort(key=lambda x: x.tournament_participations, reverse=True)
        elif sort_by == "top3s":
            participants_list.sort(key=lambda x: x.tournament_top3s, reverse=True)
        elif sort_by == "top8s":
            participants_list.sort(key=lambda x: x.tournament_top8s, reverse=True)
        elif sort_by == "winners":
            participants_list.sort(key=lambda x: x.tournament_winners, reverse=True)
        else:
            # Default: tournament winners, then top 3s, then match wins
            participants_list.sort(
                key=lambda x: (x.tournament_winners, x.tournament_top3s, x.total_match_wins),
                reverse=True
            )
        
        return participants_list[:limit]
    
    def get_user_stats(self, user_id: int) -> Optional[Dict]:
        """Get detailed stats for a user."""
        participant = self.get_participant(user_id)
        if not participant:
            return None
        
        # Get tournament placements for this user
        tournament_placements = []
        for tournament in self.tournaments:
            for result in tournament.results:
                if result["user_id"] == user_id:
                    tournament_placements.append({
                        "tournament": tournament.tournament_title,
                        "placement": result["placement"],
                        "match_record": result["match_record"],
                        "date": tournament.start_date,
                        "standings_url": tournament.standings_url,
                    })

        tournament_placements.sort(
            key=lambda placement: _safe_datetime_sort_key(placement.get("date", "")),
            reverse=True,
        )
        
        return {
            "user_id": user_id,
            "username": participant.username,
            "match_record": participant.get_match_record(),
            "match_winrate": f"{participant.get_match_win_percentage()*100:.1f}%",
            "tournament_participations": participant.tournament_participations,
            "tournament_winners": participant.tournament_winners,
            "tournament_top3s": participant.tournament_top3s,
            "tournament_top8s": participant.tournament_top8s,
            "tournament_placements": tournament_placements
        }
    
    def get_tournament_results(self, tournament_id: str) -> Optional[TournamentRecord]:
        """Get results for a specific tournament."""
        for tournament in self.tournaments:
            if tournament.tournament_id == tournament_id:
                return tournament
        return None
    
    def get_guild_tournaments(self, guild_id: int) -> List[TournamentRecord]:
        """Get all tournament records for a guild."""
        return [t for t in self.tournaments if t.guild_id == guild_id]


# Global instance
_hall_of_fame: Optional[HallOfFame] = None

def get_hall_of_fame() -> HallOfFame:
    """Get or create the global hall of fame instance."""
    global _hall_of_fame
    if _hall_of_fame is None:
        _hall_of_fame = HallOfFame()
    return _hall_of_fame
