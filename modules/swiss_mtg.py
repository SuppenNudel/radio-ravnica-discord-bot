import random, re
import networkx as nx
from modules.serializable import Serializable
import json
from datetime import datetime, timedelta, timezone

# The following tiebreakers are used to determine how a player ranks in a tournament:
# 1. Match points
# 2. Opponents’ match-win percentage
# 3. Game-win percentage
# 4. Opponents’ game-win percentage

def strike_through(text):
    return f"\u001b[9m{text}\u001b[0m"

def visible_length(text):
    """Returns the visible length of a string, ignoring ANSI escape codes."""
    return len(re.sub(r'\x1b\[[0-9;]*m', '', text))

def pad_ansi_text(text, width):
    """Pads text to a fixed width, considering visible length."""
    visible_len = visible_length(text)
    return text + " " * (width - visible_len)


def normalize_percentage(value: float) -> float:
    return round(float(value), 10)

class Deck(Serializable):
    def __init__(self, link:str|None=None, archetype:str|None=None):
        self.link = link
        self.archetype = archetype

    @classmethod
    def deserialize(cls, data) -> "Deck":
        if data is None:
            return None
        deck = Deck(data['link'] if 'link' in data else None, data['archetype'] if 'archetype' in data else None)
        return deck

    def serialize(self):
        return {
            "link": self.link,
            "archetype": self.archetype
        }


class Player(Serializable):
    def __init__(self, player_id):
        self.player_id:int = player_id
        self.match_history:list[Match] = []
        self.dropped = False
        self.ready_to_play = False
        self.ready_until: datetime | None = None
        self.elo_rating = 1000.0
        self.deck:Deck|None = None

    @property
    def ready_to_play(self):
        return self._ready_to_play and (self.ready_until is None or self.ready_until > datetime.now(timezone.utc))

    @ready_to_play.setter
    def ready_to_play(self, value: bool):
        self._ready_to_play = value

    @classmethod
    def deserialize(cls, data) -> "Player":
        player = Player(data['player_id'])
        player.dropped = data['dropped'] if 'dropped' in data else False
        player.ready_to_play = bool(data.get("ready_to_play", False))
        if data.get("ready_until"):
            player.ready_until = datetime.fromisoformat(data["ready_until"])
            if player.ready_until.tzinfo is None:
                player.ready_until = player.ready_until.replace(tzinfo=timezone.utc)
        player.deck = Deck.deserialize(data['deck']) if 'deck' in data else None
        player.elo_rating = float(data.get("elo_rating", 1000.0))
        return player

    def serialize(self):
        my_dict = {
            "player_id": self.player_id,
            "ready_to_play": self.ready_to_play,
            "ready_until": self.ready_until.isoformat() if self.ready_until else None,
            "elo_rating": self.elo_rating,
            "deck": self.deck
        }
        if self.dropped:
            my_dict["dropped"] = self.dropped
        return my_dict

    def had_bye(self):
        return any(match.is_bye() for match in self.match_history)

    def __str__(self):
        return f"Player({self.player_id})"
    
    def __repr__(self):
        return f"Player({self.player_id}) - {self.calculate_match_points()} MP"

    def get_match_results(self):
        # Using a generator expression to sum up wins, draws, and losses
        results = [match.get_winner() for match in self.match_history if match.is_finished()]

        wins = sum(1 for result in results if result == self)
        draws = results.count(None)
        losses = len(results) - wins - draws

        return f"{wins}-{losses}-{draws}"

    def has_played_against(self, opponent):
        return any(match.get_opponent_of(self) == opponent for match in self.match_history)

    def get_finished_matches(self) -> list["Match"]:
        return [match for match in self.match_history if match.is_finished()]
    
    def get_finished_non_bye_matches(self):
        return [match for match in self.match_history if match.is_finished() and not match.is_bye()]

    def calculate_match_points(self, up_to_round:int|None=None):
        match_points = 0
        for match in self.get_finished_matches():
            # Skip matches beyond the specified round number
            if up_to_round is not None and match.round_number > up_to_round:
                continue
            if match.get_winner() == self:
                match_points += 3
            elif match.is_draw():
                match_points += 1
        return match_points
    
    def calculate_game_points(self):
        game_points = 0
        for match in self.get_finished_matches():
            game_points += match.wins[self] * 3 + match.wins["draws"]  # Player's wins + draws
        return game_points
    
    def calculate_match_win_percentage(self):
        match_points = self.calculate_match_points()
        possbile_match_points = 3 * len(self.get_finished_matches())
        mwp = match_points / possbile_match_points
        return normalize_percentage(max(mwp, 0.33))

    def calculate_game_win_percentage(self):
        possible_game_points = 0
        for match in self.get_finished_matches():
            possible_game_points += sum(match.wins.values()) * 3
        gwp = self.calculate_game_points() / possible_game_points if possible_game_points else 0.33
        return normalize_percentage(max(gwp, 0.33))
    
    def calculate_opponent_match_win_percentage(self):
        opponent_mwp = 0
        non_bye_matches = 0
        for match in self.get_finished_non_bye_matches():
            non_bye_matches += 1
            opponent = match.get_opponent_of(self)            
            if opponent is None:
                raise ValueError("Opponent can not be None.")
            omwp = opponent.calculate_match_win_percentage()
            opponent_mwp += omwp
        if non_bye_matches == 0:
            return normalize_percentage(0.33)
        omp = opponent_mwp / non_bye_matches
        return normalize_percentage(max(omp, 0.33))
    
    def calculate_opponent_game_win_percentage(self):
        opponent_gwp = 0
        non_bye_matches = 0
        for match in self.get_finished_non_bye_matches():
            non_bye_matches += 1
            opponent = match.get_opponent_of(self)
            if opponent is None:
                raise ValueError("Opponent can not be None.")
            opponent_gwp += opponent.calculate_game_win_percentage()
        if non_bye_matches == 0:
            return normalize_percentage(0.33)
        ogwp = opponent_gwp / non_bye_matches
        return normalize_percentage(max(ogwp, 0.33))

class Match(Serializable):
    def __init__(self, player1:Player, player2:Player|None, round_number:int, allow_rematch=False):
        self.round_number = round_number
        if player1 is None:
            raise ValueError("player1 can not be None.")
        self.wins:dict[Player|None|str, int] = {player1: 0, player2: 0, "draws": 0}
        self.player1:Player = player1
        self.player2 = player2

        if player2 is None: # is bye
            self.wins[player1] = 2
            player1.match_history.append(self)
        elif allow_rematch or (not player1.has_played_against(player2) and not player2.has_played_against(player1)):
            player1.match_history.append(self)
            player2.match_history.append(self)
        else:
            raise ValueError("Players can not play against each other twice.")
        
    def serialize(self):
        wins_by_id = {player.player_id if player and player != 'draws' else player: count for player, count in self.wins.items()}
        return {
            "wins": wins_by_id
        }
    
    @classmethod
    def deserialize(cls, match_data, players:dict[int, Player], round_number:int, allow_rematch=False):#
        wins:dict[str, int] = dict(match_data['wins'])
        draws:int = wins.pop('draws', 0)
        player_items:list[tuple[str, int]] = list(wins.items())

        if len(player_items) != 2:
            raise ValueError(
                f"Datenkorrektur erforderlich: Ungültige Match-Daten in Runde {round_number} "
                f"(erwartet 2 Spieler-Eintraege, erhalten: {player_items})."
            )

        p1 = player_items[0]
        p2 = player_items[1]

        if p1[0] == 'null':
            p2, p1 = p1, p2
        if p1[0] == 'null':
            raise ValueError(
                f"Datenkorrektur erforderlich: Ungültige Match-Daten in Runde {round_number} "
                f"(kein primaerer Spieler in {player_items})."
            )
        if p2[0] == 'null':
            p2 = None

        player1 = players[int(p1[0])]
        player2 = players[int(p2[0])] if p2 else None
        try:
            match = Match(player1, player2, round_number, allow_rematch=allow_rematch)
        except ValueError as exc:
            raise ValueError(
                f"Datenkorrektur erforderlich: Ungültige Paarung in Runde {round_number} "
                f"({player1.player_id} vs {player2.player_id if player2 else 'BYE'}): {exc}"
            ) from exc
        if p2 and (p1[1] or p2[1] or draws):
            match.set_result(p1[1], p2[1], draws)
        return match
    
    def is_finished(self):
        player1_wins = self.wins[self.player1]
        player2_wins = self.wins[self.player2]
        if player1_wins or player2_wins or self.wins['draws']:
            return True
        else:
            return False

    def get_opponent_of(self, player:Player):
        if player == self.player1:
            return self.player2
        elif player == self.player2:
            return self.player1
        else:
            raise ValueError(f"{player} didn't participate in this match")

    def get_winner(self) -> Player|bool|None:
        if not self.is_finished():
            return False
        if self.wins[self.player1] > self.wins[self.player2]:
            return self.player1
        elif self.wins[self.player1] < self.wins[self.player2]:
            return self.player2
        else:
            return None # draw

    def set_result(self, player1_wins, player2_wins, draws):
        if self.player2 is None: # is bye
            raise ValueError("Ergebnis eines Bye-Matches kann nicht manuell gesetzt werden.")
        self.wins[self.player1] = player1_wins
        self.wins[self.player2] = player2_wins
        self.wins['draws'] = draws

    def is_bye(self):
        return self.player2 is None

    def is_draw(self):
        if not self.is_finished():
            return None
        return self.wins[self.player1] == self.wins[self.player2]
    
    # def __str__(self) -> str:
    #     return self.__repr__()

    def __repr__(self):
        if self.player2 is None:
            return f"Bye for Player({self.player1.player_id}) ({self.player1.calculate_match_points()})"
        text = f"Match between Player({self.player1.player_id}) ({self.player1.calculate_match_points()}) and Player({self.player2.player_id}) ({self.player2.calculate_match_points()})"
        if self.is_finished():
            win_string = "-".join(map(str, self.wins.values()))
            winner = self.get_winner()
            winner_text = f"Player({winner.player_id}) won" if winner else "Draw"
            return f"{text}: {win_string} ({winner_text})"
        else:
            return f"{text} is not finished yet."

class Round(Serializable):
    def __init__(self, round_number: int):
        self.round_number = round_number
        self.matches: list[Match] = []
        self.message_id_pairings:int = None
        self.message_id_standings:int = None

    def serialize(self):
        obj = {
            "round_number": self.round_number,
            "matches": self.matches,
            "message_pairings": self.message_id_pairings,
        }
        if self.message_id_standings:
            obj["message_standings"] = self.message_id_standings
        return obj
    
    @classmethod
    def deserialize(cls, round_data, players:dict[int, Player], allow_rematch=False):
        round = Round(round_data['round_number'])
        round.matches = [Match.deserialize(match, players, round.round_number, allow_rematch=allow_rematch) for match in round_data['matches']]
        if 'message_pairings' in round_data:
            message_id_str = round_data['message_pairings']
            if message_id_str is not None:
                message_id = int(message_id_str)
                round.message_id_pairings = message_id
        if 'message_standings' in round_data:
            message_id_str = round_data['message_standings']
            if message_id_str is not None:
                message_id = int(message_id_str)
                round.message_id_standings = message_id
        return round
    
    def is_concluded(self):
        return all(match.is_finished() for match in self.matches)

    def add_match(self, match: Match):
        self.matches.append(match)

    def __repr__(self):
        return f"Round {self.round_number}: {self.matches}"
        
def sort_players_by_standings(players:list[Player]):
    players.sort(key=lambda player: (
        player.calculate_match_points(),
        player.calculate_opponent_match_win_percentage(),
        player.calculate_game_win_percentage(),
        player.calculate_opponent_game_win_percentage()
        ), reverse=True)

double_bye_count = 0

def recommended_rounds(num_players):
    if num_players <= 8: return 3
    if num_players <= 16: return 4
    if num_players <= 32: return 5
    if num_players <= 64: return 6
    if num_players <= 128: return 7
    if num_players <= 226: return 8
    if num_players <= 409: return 9
    return 10

class SwissTournament(Serializable):
    allow_rematches = False

    def __init__(self, players:list[Player], max_rounds:int|None=None):
        if max_rounds is None:
            rounds_count = recommended_rounds(len(players))
        else:
            rounds_count = min(max_rounds, recommended_rounds(len(players)))
        self.rounds_count = rounds_count
        self.players:list[Player] = players
        self.rounds:list[Round] = []
        self.winner:Player|None = None

    def get_active_players(self):
        return [player for player in self.players if not player.dropped]
    
    def serialize(self):
        return {
            "rounds_count": self.rounds_count,
            "players": self.players,
            "rounds": self.rounds,
            "winner": self.winner
        }
    
    def current_round(self) -> Round:
        return self.rounds[-1] if self.rounds else None

    @classmethod
    def deserialize(cls, data):
        players = data['players']
        players:list[Player] = [Player.deserialize(player) for player in players]
        tournament = cls(players)
        tournament.rounds_count = data['rounds_count']
        if 'rounds' in data:
            player_map = {player.player_id: player for player in players}
            tournament.rounds = [Round.deserialize(round, player_map, allow_rematch=cls.allow_rematches) for round in data['rounds']]
        if 'winner' in data and isinstance(data['winner'], dict):
            tournament.winner = Player.deserialize(data['winner'])
        return tournament

    def player_by_id(self, id):
        for player in self.players:
            if player.player_id == id:
                return player
        return None

    def get_opponent(self, player:Player, opponents:list[Player]) -> Player|None:
        for possible_opponent in opponents:
            if player.has_played_against(possible_opponent):
                continue
            return possible_opponent
        return None

    def random_pairing(self, round_number):
        active_players = self.get_active_players()
        random.shuffle(active_players)

        round = Round(round_number)
        for i in range(0, len(active_players), 2):
            if i + 1 < len(active_players):  # Check if there is a next player to form a pair
                round.matches.append(Match(active_players[i], active_players[i + 1], round_number))
            else:
                # Handle odd number of players, unpaired player gets a bye
                if active_players[i].had_bye():
                    raise ValueError("Player already had a bye.")
                round.matches.append(Match(active_players[i], None, round_number))
        return round

    def swiss_pairing(self, round_number, players:list[tuple[Player, int]]):
        """
        Pair players using min-weight maximum matching while prioritizing fair top-table matchups.
        
        :param round_number: The current round number.
        :return: Round object with the matches.
        """
        round = Round(round_number)

        # Retrieve all previous matches to prevent rematches
        past_matches: list[Match] = [match for round in self.rounds for match in round.matches]
        previous_matches = {frozenset({match.player1, match.player2}) for match in past_matches}
        
        unpaired_players = players[:]

        # Handle odd number of players: assign a bye to the lowest-ranked player who hasn't had one yet
        if len(unpaired_players) % 2 != 0:
            # Iterate through the unpaired players in reverse order (lowest-ranked first)
            bye_player = None
            for p, points in reversed(unpaired_players):
                if not p.had_bye():
                    bye_player = p
                    break

            if bye_player:
                # Remove the bye player from the unpaired players list
                unpaired_players = [(p, points) for p, points in unpaired_players if p != bye_player]
                round.matches.append(Match(bye_player, None, round_number))
            else:
                raise ValueError("No eligible player for a bye.")

        # Create a graph to hold the possible pairings and weights
        G = nx.Graph()

        # Build the graph with weighted edges based on score differences, avoiding rematches
        for i in range(len(unpaired_players)):
            for j in range(i + 1, len(unpaired_players)):
                p1, score1 = unpaired_players[i]
                p2, score2 = unpaired_players[j]
                
                # Prevent rematches
                if frozenset({p1, p2}) in previous_matches:
                    weight = float('inf')  # Disallow this pairing
                else:
                    score_diff = abs(score1 - score2)
                    if score_diff == 0:
                        weight = 1  # Ideal same-score pairing
                    else:
                        weight = score_diff * 5  # Linear weight based on score difference

                G.add_edge(p1, p2, weight=-weight)  # Negate weight since networkx maximizes weight


        # Find optimal pairings using the max weight matching
        matching = nx.max_weight_matching(G, maxcardinality=True)

        # Remove paired players from unpaired_players
        for p1, p2 in matching:
            if p1 and p2:
                unpaired_players = [(p, points) for p, points in unpaired_players if p != p1 and p != p2]
            pass

        # Create Match objects for the pairings
        match_objects = [Match(p1, p2, round_number) for p1, p2 in matching]
        round.matches.extend(match_objects)

        # Check if any players are left unpaired
        if unpaired_players:
            raise ValueError(f"Some players are left unpaired: {unpaired_players}")
        return round
    
    def pair_players(self) -> Round:
        next_round_no = self.current_round().round_number + 1 if self.current_round() else 1
        if next_round_no > self.rounds_count:
            return None
        new_round = None
        if next_round_no == 1:
            new_round = self.random_pairing(next_round_no)
        elif next_round_no == self.rounds_count:  # Last round      
            sort_players_by_standings(self.players)
            # List of players who haven't dropped, along with their match points
            players: list[tuple[Player, int]] = [(player, rank) for rank, player in enumerate(self.players) if not player.dropped]

            new_round = self.swiss_pairing(next_round_no, players)
        else:
            # List of players who haven't dropped, along with their match points
            players: list[tuple[Player, int]] = [(player, player.calculate_match_points()) for player in self.players if not player.dropped]
            # shuffle the players around and sort them afterwards ONLY by match points
            random.shuffle(players)
            # Sort players by match points in descending order (top players first)
            players.sort(key=lambda toup: -toup[1])

            new_round = self.swiss_pairing(next_round_no, players)
        self.rounds.append(new_round)
        return new_round

    def print_standings(self):
        sort_players_by_standings(self.players)

        # Print the headers
        print("Standings for Round: ", self.current_round().round_number)
        print(f"{'Rank':<5}{'Player ID':<15}{'Points':<8}{'Results':<10}{'OMW':<12}{'GW':<12}{'OGW':<12}")

        # Print each player's data
        for rank, player in enumerate(self.players):
            player_id_str = str(player.player_id)
            formatted_name = pad_ansi_text(player_id_str, 15)
            print(f"{rank+1:<5}{formatted_name}{player.calculate_match_points():<8}{player.get_match_results():<10}{player.calculate_opponent_match_win_percentage():<12.4%}{player.calculate_game_win_percentage():<12.4%}{player.calculate_opponent_game_win_percentage():<12.4%}")

    def print_round_pairings(self, round:Round):
        print(f"Parings for Round {round.round_number}:")
        for match in round.matches:
            print(f"{match.player1} vs {match.player2}")

class LeagueTournament(SwissTournament):
    allow_rematches = True
    ELO_K_FACTOR = 32
    ELO_BASELINE = 1000.0

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def __init__(
        self,
        players: list[Player],
        cooldown_percentage=50,
        count_repeat_opponents=True,
        matches_before_cooldown=1,
        scoring_mode="points",
        elo_reset_days=30,
        elo_last_reset_at: datetime | None = None,
    ):
        super().__init__(players, max_rounds=0)
        self.validate_cooldown_settings(cooldown_percentage, count_repeat_opponents, matches_before_cooldown)
        self.validate_elo_settings(scoring_mode, elo_reset_days)
        self.cooldown_percentage = cooldown_percentage
        self.count_repeat_opponents = count_repeat_opponents
        self.matches_before_cooldown = matches_before_cooldown
        self.scoring_mode = scoring_mode
        self.elo_reset_days = elo_reset_days
        self.elo_last_reset_at = self._utc(elo_last_reset_at) if elo_last_reset_at else datetime.now(timezone.utc)

    def sort_players_by_standings(self):
        if self.scoring_mode == "elo":
            self.players.sort(
                key=lambda player: (player.elo_rating, player.calculate_match_points()), reverse=True
            )
        else:
            sort_players_by_standings(self.players)

    @staticmethod
    def validate_elo_settings(scoring_mode, elo_reset_days):
        if scoring_mode not in ("points", "elo"):
            raise ValueError("Bitte ein gültiges Wertungssystem auswählen.")
        if type(elo_reset_days) is not int or not 0 <= elo_reset_days <= 36500:
            raise ValueError("Das Elo-Reset-Intervall muss 0 (deaktiviert) oder zwischen 1 und 36500 Tagen liegen.")

    @classmethod
    def elo_change(cls, rating: float, opponent_rating: float, score: float) -> float:
        expected = 1 / (1 + 10 ** ((opponent_rating - rating) / 400))
        return cls.ELO_K_FACTOR * (score - expected)

    def apply_soft_reset(self, now: datetime | None = None) -> int:
        if self.scoring_mode != "elo" or self.elo_reset_days == 0:
            return 0
        now = self._utc(now or datetime.now(timezone.utc))
        if self.elo_last_reset_at is None:
            self.elo_last_reset_at = now
            return 0
        interval = timedelta(days=self.elo_reset_days)
        reset_count = 0
        while now - self.elo_last_reset_at >= interval:
            for player in self.players:
                player.elo_rating = self.ELO_BASELINE + (player.elo_rating - self.ELO_BASELINE) * 0.5
            self.elo_last_reset_at += interval
            reset_count += 1
        return reset_count

    @staticmethod
    def validate_cooldown_settings(cooldown_percentage, count_repeat_opponents, matches_before_cooldown=1):
        if type(cooldown_percentage) is not int or not 0 <= cooldown_percentage <= 100:
            raise ValueError("Der Rematch-Cooldown muss zwischen 0 und 100 Prozent liegen.")
        if type(count_repeat_opponents) is not bool:
            raise ValueError("Bitte eine gültige Zählweise für den Rematch-Cooldown auswählen.")
        if type(matches_before_cooldown) is not int or matches_before_cooldown < 1:
            raise ValueError("Vor dem Cooldown muss mindestens ein Match erlaubt sein.")

    def serialize(self):
        return dict(
            super().serialize(), cooldown_percentage=self.cooldown_percentage,
            count_repeat_opponents=self.count_repeat_opponents,
            matches_before_cooldown=self.matches_before_cooldown,
            scoring_mode=self.scoring_mode, elo_reset_days=self.elo_reset_days,
            elo_last_reset_at=self.elo_last_reset_at.isoformat() if self.elo_last_reset_at else None,
        )

    @classmethod
    def deserialize(cls, data):
        players = [Player.deserialize(player) for player in data["players"]]
        tournament = cls(
            players,
            cooldown_percentage=data.get("cooldown_percentage", 50),
            count_repeat_opponents=data.get("count_repeat_opponents", True),
            matches_before_cooldown=data.get("matches_before_cooldown", 1),
            scoring_mode=data.get("scoring_mode", "points"),
            elo_reset_days=data.get("elo_reset_days", 30),
            elo_last_reset_at=datetime.fromisoformat(data["elo_last_reset_at"]) if data.get("elo_last_reset_at") else None,
        )
        if data.get("rounds"):
            player_map = {player.player_id: player for player in players}
            tournament.rounds = [
                Round.deserialize(round_data, player_map, allow_rematch=True)
                for round_data in data["rounds"]
            ]
        winner_data = data.get("winner")
        if isinstance(winner_data, dict):
            tournament.winner = tournament.player_by_id(int(winner_data["player_id"]))
        return tournament

    def pair_players(self):
        raise ValueError("League opponents are chosen by the players.")

    def rematch_cooldown(self, player1_id, player2_id) -> tuple[int, int]:
        player1 = self.player_by_id(player1_id)
        player2 = self.player_by_id(player2_id)
        if player1 is None or player2 is None or player1 is player2:
            raise ValueError("Bitte zwei unterschiedliche Teilnehmer der Liga auswählen.")
        previous_matches = [
            match for match in player1.get_finished_non_bye_matches()
            if match.get_opponent_of(player1) is player2
        ]
        if not previous_matches:
            return 0, 0
        if len(previous_matches) % self.matches_before_cooldown:
            return 0, 0
        last_meeting = max(match.round_number for match in previous_matches)
        other_players = {
            player.player_id for player in self.get_active_players()
            if player not in (player1, player2)
        }

        required_matches = (len(other_players) * self.cooldown_percentage + 99) // 100

        def remaining_matches(player):
            opponents_since = [
                match.get_opponent_of(player).player_id
                for match in player.get_finished_non_bye_matches()
                if match.round_number > last_meeting
                and match.get_opponent_of(player).player_id in other_players
            ]
            played_since = len(opponents_since) if self.count_repeat_opponents else len(set(opponents_since))
            return max(0, required_matches - played_since)

        return remaining_matches(player1), remaining_matches(player2)

    def report_match(self, player1_id, player2_id, player1_wins, player2_wins, draws=0):
        if self.winner:
            raise ValueError("Die Liga ist bereits beendet.")
        player1 = self.player_by_id(player1_id)
        player2 = self.player_by_id(player2_id)
        if player1 is None or player2 is None or player1.dropped or player2.dropped:
            raise ValueError("Beide Spieler müssen aktive Teilnehmer der Liga sein.")
        if player1 is player2:
            raise ValueError("Du kannst nicht gegen dich selbst spielen.")
        scores = (player1_wins, player2_wins, draws)
        if any(type(score) is not int or score < 0 for score in scores) or not any(scores):
            raise ValueError("Bitte ein gültiges, nicht leeres Ergebnis eingeben.")
        remaining1, remaining2 = self.rematch_cooldown(player1_id, player2_id)
        if remaining1 or remaining2:
            counting_unit = "Matches gegen andere aktive Teilnehmer" if self.count_repeat_opponents else "unterschiedliche andere aktive Gegner"
            raise ValueError(
                f"Rematch-Cooldown: <@{player1_id}> braucht noch {remaining1}, "
                f"<@{player2_id}> noch {remaining2} {counting_unit}."
            )
        if self.scoring_mode == "elo":
            self.apply_soft_reset()
        match_round = Round(len(self.rounds) + 1)
        match = Match(player1, player2, match_round.round_number, allow_rematch=True)
        match.set_result(player1_wins, player2_wins, draws)
        match_round.add_match(match)
        self.rounds.append(match_round)
        if self.scoring_mode == "elo":
            winner = match.get_winner()
            score1 = 0.5 if winner is None else (1.0 if winner is player1 else 0.0)
            rating1, rating2 = player1.elo_rating, player2.elo_rating
            player1.elo_rating = rating1 + self.elo_change(rating1, rating2, score1)
            player2.elo_rating = rating2 + self.elo_change(rating2, rating1, 1 - score1)
        return match


def simulate_remaining_matches(tournament:SwissTournament):
    outcomes = [
        (2, 0, 0),  # Player 1 wins, Player 2 loses
        (2, 1, 0),  # Player 1 wins, Player 2 wins 1 game
        (1, 1, 1),  # Draw, both players win 1 game each
        (1, 2, 0),  # Player 1 wins 1 game, Player 2 wins 2 games
        (0, 2, 0)   # Player 2 wins, Player 1 loses
    ]
    for match in tournament.current_round().matches:
        if match.is_bye() or match.is_finished():
            continue
        result = random.choice(outcomes)
        match.set_result(*result)

def play_round(tournament:SwissTournament, random_drop_rate):
    """Simulate playing a round (actual match logic not included)."""
    tournament.pair_players()
    tournament.print_round_pairings(tournament.current_round())
    print()
    simulate_remaining_matches(tournament)
    print()
    print("\n".join(str(match) for match in tournament.current_round().matches))
    print()
    if random.random() < random_drop_rate:
        # randomly drop a player
        random_player = random.choice(tournament.players)
        random_player.dropped = True
    tournament.print_standings()
    print("\n")

def main():
    PLAYER_COUNT = 17
    # value between 0 and 1, chance of a player dropping out after a round
    # 0 means no player will drop out
    # 1 means a random player will drop each round
    # 0.5 means a random player will drop every other round
    RANDOM_DROP_RATE = 0

    # Step 1: Initialize players
    players = [Player(player_id) for player_id in range(PLAYER_COUNT)]

    # Step 2: Create tournament
    tournament = SwissTournament(players)

    # Step 3: Play multiple rounds (simulate rounds in a Swiss system)
    for round_num in range(tournament.rounds_count):
        play_round(tournament, RANDOM_DROP_RATE)

# Load the JSON file
def load_tournament(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data

# Deserialize the tournament
def deserialize_tournament(data):
    tournament_data = data["tournament"]  # Extract the tournament data
    tournament = SwissTournament.deserialize(tournament_data)
    return tournament

if __name__ == "__main__":
    file_path = "g:\\Meine Ablage\\Programmieren\\mtg\\radio-ravnica-discord-bot\\tournaments\\test_tournament.json"
    data = load_tournament(file_path)
    tournament = deserialize_tournament(data)
    print(tournament.players[0])
    print(tournament.players[0].calculate_opponent_match_win_percentage())
    print(tournament.players[0].calculate_opponent_match_win_percentage())

    # SwissTournament.deserialize()
    # for _ in range(1000):
    #     print("----- NEW TOURNAMENT -----")
    #     main()
    # print(f"Double Bye count: {double_bye_count}")
