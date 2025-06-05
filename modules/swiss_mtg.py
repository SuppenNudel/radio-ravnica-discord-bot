import random, re
import networkx as nx
from modules.serializable import Serializable
import json

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

class Player(Serializable):
    def __init__(self, name, player_id):
        self.name = name
        self.player_id:int = player_id
        self.match_history:list[Match] = []
        self.dropped = False

    @classmethod
    def deserialize(cls, data) -> "Player":
        player = Player(data['name'], data['player_id'])
        player.dropped = data['dropped'] if 'dropped' in data else False
        return player

    def serialize(self):
        my_dict = {
            "name": self.name,
            "player_id": self.player_id,
        }
        if self.dropped:
            my_dict["dropped"] = self.dropped
        return my_dict

    def had_bye(self):
        return any(match.is_bye() for match in self.match_history)

    def __str__(self):
        return self.name
    
    def __repr__(self):
        return f"{self.name} - {self.calculate_match_points()}"

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
        return max(mwp, 0.33)

    def calculate_game_win_percentage(self):
        possible_game_points = 0
        for match in self.get_finished_matches():
            possible_game_points += sum(match.wins.values()) * 3
        gwp = self.calculate_game_points() / possible_game_points if possible_game_points else 0.33
        return max(gwp, 0.33)
    
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
            return 0.33
        omp = opponent_mwp / non_bye_matches
        return max(omp, 0.33)
    
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
            return 0.33
        ogwp = opponent_gwp / non_bye_matches
        return max(ogwp, 0.33)

class Match(Serializable):
    def __init__(self, player1:Player, player2:Player|None, round_number:int):
        self.round_number = round_number
        if player1 is None:
            raise ValueError("player1 can not be None.")
        self.wins:dict[Player|None|str, int] = {player1: 0, player2: 0, "draws": 0}
        self.player1:Player = player1
        self.player2 = player2

        if player2 is None: # is bye
            self.wins[player1] = 2
            player1.match_history.append(self)
        elif not player1.has_played_against(player2) and not player2.has_played_against(player1):
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
    def deserialize(cls, match_data, players:dict[int, Player], round_number:int):#
        wins:dict[str, int] = match_data['wins']
        draws:int = wins.pop('draws')
        p1:tuple[str, int] = wins.popitem()
        p2:tuple[str, int]|None = wins.popitem()
        if p1[0] == 'null':
            p2, p1 = p1, p2
        if p2[0] == 'null':
            p2 = None

        player1 = players[int(p1[0])]
        player2 = players[int(p2[0])] if p2 else None
        match = Match(player1, player2, round_number)
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

    def get_winner(self):
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
            return f"Bye for {self.player1.name} ({self.player1.calculate_match_points()})"
        text = f"Match between {self.player1.name} ({self.player1.calculate_match_points()}) and {self.player2.name} ({self.player2.calculate_match_points()})"
        if self.is_finished():
            win_string = "-".join(map(str, self.wins.values()))
            winner = self.get_winner()
            winner_text = f"{winner.name} won" if winner else "Draw"
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
    def deserialize(cls, round_data, players:dict[int, Player]):
        round = Round(round_data['round_number'])
        round.matches = [Match.deserialize(match, players, round.round_number) for match in round_data['matches']]
        if 'message_pairings' in round_data:
            message = int(round_data['message_pairings'])
            round.message_id_pairings = message
        if 'message_standings' in round_data:
            message = int(round_data['message_standings'])
            round.message_id_standings = message
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
        tournament = SwissTournament(players)
        tournament.rounds_count = data['rounds_count']
        if 'rounds' in data:
            player_map = {player.player_id: player for player in players}
            tournament.rounds = [Round.deserialize(round, player_map) for round in data['rounds']]
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
        print(f"{'Rank':<5}{'Name':<15}{'Points':<8}{'Results':<10}{'OMW':<12}{'GW':<12}{'OGW':<12}")

        # Print each player's data
        for rank, player in enumerate(self.players):
            player_name = strike_through(player.name) if player.dropped else player.name
            formatted_name = pad_ansi_text(player_name, 15)
            print(f"{rank+1:<5}{formatted_name}{player.calculate_match_points():<8}{player.get_match_results():<10}{player.calculate_opponent_match_win_percentage():<12.4%}{player.calculate_game_win_percentage():<12.4%}{player.calculate_opponent_game_win_percentage():<12.4%}")

    def print_round_pairings(self, round:Round):
        print(f"Parings for Round {round.round_number}:")
        for match in round.matches:
            print(f"{match.player1} vs {match.player2}")

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
    players = [Player(f"Player {player_id+1}", player_id) for player_id in range(PLAYER_COUNT)]

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
