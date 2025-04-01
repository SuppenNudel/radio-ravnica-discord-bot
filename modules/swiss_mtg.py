import random, re
import networkx as nx
import discord

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

class Player():
    def __init__(self, name, player_id):
        self.name = name
        self.player_id:int = player_id
        self.match_history:list[Match] = []
        self.dropped = False
        self.user = None
        # dont receive bye twice

    @classmethod
    def deserialize(cls, data) -> "Player":
        return Player(data['name'], data['player_id'])

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

    def get_finished_matches(self):
        return [match for match in self.match_history if match.is_finished()]
    
    def get_finished_non_bye_matches(self):
        return [match for match in self.match_history if match.is_finished() and not match.is_bye()]

    def calculate_match_points(self):
        match_points = 0
        for match in self.get_finished_matches():
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
        gwp = self.calculate_game_points() / possible_game_points
        return max(gwp, 0.33)
    
    def calculate_opponent_match_win_percentage(self):
        opponent_mwp = 0
        non_bye_matches = 0
        for match in self.get_finished_non_bye_matches():
            non_bye_matches += 1
            opponent = match.get_opponent_of(self)            
            if opponent is None:
                raise ValueError("Opponent can not be None.")
            opponent_mwp += opponent.calculate_match_win_percentage()
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

class Match:
    def __init__(self, player1:Player, player2:Player|None):
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
    def deserialize(cls, match_data, players:dict[int, Player]):#
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
        match = Match(player1, player2)
        if p2 and (p1[1] or p2[1] or draws):
            match.set_result(p1[1], p2[1], draws)
        return match
    
    def is_finished(self, best_of=3):
        player1_wins = self.wins[self.player1]
        player2_wins = self.wins[self.player2]
        if player1_wins + player2_wins > best_of:
            # too many games
            return False
        if player1_wins > best_of / 2 or player2_wins > best_of / 2 or sum(self.wins.values()) >= best_of:
            return True
        # too few games
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
        if not self.is_finished():
            raise ValueError("Inkorrekte Anzahl an Games")

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
            return f"Bye for {self.player1.name}"
        text = f"Match between {self.player1.name} and {self.player2.name}"
        if self.is_finished():
            win_string = "-".join(map(str, self.wins.values()))
            winner = self.get_winner()
            winner_text = f"{winner.name} won" if winner else "Draw"
            return f"{text}: {win_string} ({winner_text})"
        else:
            return f"{text} is not finished yet."

class Round:
    def __init__(self, round_number: int):
        self.round_number = round_number
        self.matches: list[Match] = []
        self.message_pairings:discord.message.Message = None
        self.message_standings:discord.message.Message = None

    def serialize(self):
        obj = {
            "round_number": self.round_number,
            "matches": self.matches,
            "message_pairings": self.message_pairings.id,
        }
        if self.message_standings:
            obj["message_standings"] = self.message_standings.id
        return obj
    
    @classmethod
    async def deserialize(cls, round_data, players:dict[int, Player], channel:discord.TextChannel):
        round = Round(round_data['round_number'])
        round.matches = [Match.deserialize(match, players) for match in round_data['matches']]
        if 'message_pairings' in round_data:
            message = await channel.fetch_message(round_data['message_pairings'])
            round.message_pairings = message
        if 'message_standings' in round_data:
            message = await channel.fetch_message(round_data['message_standings'])
            round.message_standings = message
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

class SwissTournament:
    def __init__(self, players:list[Player], max_rounds:int|None=None):
        if max_rounds is None:
            max_rounds = self.recommended_rounds(len(players))
        self.max_rounds = max_rounds
        self.players:list[Player] = players
        self.rounds:list[Round] = []

    def serialize(self):
        return {
            "max_rounds": self.max_rounds,
            "players": self.players,
            "rounds": self.rounds,
        }
    
    def current_round(self) -> Round:
        return self.rounds[-1] if self.rounds else None

    @classmethod
    async def deserialize(cls, data, channel):
        players = data['players']
        players:list[Player] = [Player.deserialize(player) for player in players]
        tournament = SwissTournament(players, data['max_rounds'])
        if 'rounds' in data:
            player_map = {player.player_id: player for player in players}
            tournament.rounds = [await Round.deserialize(round, player_map, channel) for round in data['rounds']]
        return tournament

    def player_by_id(self, id):
        for player in self.players:
            if player.player_id == id:
                return player
        raise ValueError("No player with that id")

    def recommended_rounds(self, num_players):
        if num_players <= 8:
            return 3
        elif num_players <= 16:
            return 4
        elif num_players <= 32:
            return 5
        elif num_players <= 64:
            return 6
        elif num_players <= 128:
            return 7
        elif num_players <= 226:
            return 8
        elif num_players <= 409:
            return 9
        else:
            return 10

    def get_opponent(self, player:Player, opponents:list[Player]) -> Player|None:
        for possible_opponent in opponents:
            if player.has_played_against(possible_opponent):
                continue
            return possible_opponent
        return None

    def random_pairing(self, round_number):
        active_players: list[Player] = [p for p in self.players if not p.dropped]
        random.shuffle(active_players)

        round = Round(round_number)
        for i in range(0, len(active_players), 2):
            if i + 1 < len(active_players):  # Check if there is a next player to form a pair
                round.matches.append(Match(active_players[i], active_players[i + 1]))
            else:
                # Handle odd number of players, unpaired player gets a bye
                if active_players[i].had_bye():
                    raise ValueError("Player already had a bye.")
                round.matches.append(Match(active_players[i], None))
        return round

    def intermediate_round_pairing(self, round_number):
        """
        Pair players using min-weight maximum matching while prioritizing fair top-table matchups.
        
        :param round_number: The current round number.
        :return: Round object with the matches.
        """
        # Retrieve all previous matches to prevent rematches
        past_matches: list[Match] = [match for round in self.rounds for match in round.matches]
        previous_matches = {frozenset({match.player1, match.player2}) for match in past_matches}
        
        round = Round(round_number)
        
        # List of players who haven't dropped, along with their match points
        players: list[tuple[Player, int]] = [(player, player.calculate_match_points()) for player in self.players if not player.dropped]
        
        # Create a graph to hold the possible pairings and weights
        G = nx.Graph()

        # Sort players by match points in descending order (top players first)
        players.sort(key=lambda toup: -toup[1])

        # Build match-points brackets and shuffle them to randomize pairings within the same score group
        match_brackets: dict[int, list[Player]] = {}
        for player, points in players:
            match_brackets.setdefault(points, []).append(player)
        for match_bracket in match_brackets.values():
            random.shuffle(match_bracket)

        # Build the graph with weighted edges based on score differences, avoiding rematches
        for i in range(len(players)):
            for j in range(i + 1, len(players)):
                p1, score1 = players[i]
                p2, score2 = players[j]
                
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
        match_objects = [Match(p1, p2) for p1, p2 in matching]
        round.matches.extend(match_objects)

        # Handle odd number of players (assign a bye to the lowest-ranked player who hasn't had one yet)
        unpaired = set(p[0] for p in players) - set(x for pair in matching for x in pair)
        bye_candidates = [p for p in unpaired if not p.had_bye()]
        
        # Find the lowest-ranked player who has not yet received a bye
        if bye_candidates:
            bye = min(bye_candidates, key=lambda p: next(points for pid, points in players if pid == p))  # Assign the lowest-ranked eligible player
            round.matches.append(Match(bye, None))  # Append the bye as a match with 'None'

        return round
    
    def last_round_pairing_gpt(self, round_number):
        """
        Pair players based on ranking while ensuring they haven't played each other before.

        :param players: List of player objects sorted by ranking.
        :return: List of tuples (player1, player2).
        """
        players: list[Player] = [p for p in self.players if not p.dropped]
        sort_players_by_standings(players)
        round = Round(round_number)
        used = set()
    
        i = 0
        while i < len(players):
            if players[i] in used:
                i += 1
                continue  # Skip already paired players
            
            player1 = players[i]
            best_match = None

            # Find the best available opponent
            for j in range(i + 1, len(players)):
                player2 = players[j]

                if player2 not in used and not player1.has_played_against(player2):
                    best_match = player2
                    break  # Pair them immediately

            if best_match:
                round.matches.append(Match(player1, best_match))
                used.add(player1)
                used.add(best_match)
            else:
                if player1.had_bye():
                    print(f"{player1.name} already had a bye.")
                    global double_bye_count
                    double_bye_count += 1
                # If no valid opponent, the player gets a bye
                round.matches.append(Match(player1, None))
                used.add(player1)

            i += 1  # Move to the next player
            
        return round

    def last_round_pairing(self, round_number):
        # 1. Match points
        # 2. Opponents’ match-win percentage
        # 3. Game-win percentage
        # 4. Opponents’ game-win percentage
        active_players: list[Player] = [p for p in self.players if not p.dropped]
        sort_players_by_standings(active_players)
        round = Round(round_number)
        bye_candidates = []
        while len(active_players) >= 2:
            player1 = active_players.pop(0)
            player2 = self.get_opponent(player1, active_players)
            if player2 is None:
                bye_candidates.append(player1)
                continue
            active_players.remove(player2)
            round.matches.append(Match(player1, player2))
        if active_players or bye_candidates:
            if len(active_players) + len(bye_candidates) > 1:
                raise ValueError("More than one players would receive a bye.")
            active_players.extend(bye_candidates)
            last_player = active_players.pop()
            if last_player.had_bye():
                print(f"{player1.name} already had a bye.")
                global double_bye_count
                double_bye_count += 1
            round.matches.append(Match(last_player, None))
        if active_players:
            raise ValueError("There should be no players left in the group.")
        return round

    def pair_players(self) -> Round:
        round = self.current_round()
        next_round_no = self.current_round().round_number + 1 if round else 1
        if next_round_no > self.max_rounds:
            return None
        round = None
        if next_round_no == 1:
            round = self.random_pairing(next_round_no)
        elif next_round_no == self.max_rounds:  # Last round
            # round = self.last_round_pairing(self.current_round)
            round = self.last_round_pairing_gpt(next_round_no)
        else:
            round = self.intermediate_round_pairing(next_round_no)
        self.rounds.append(round)
        return round

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
    RANDOM_DROP_RATE = 0.1

    # Step 1: Initialize players
    players = [Player(f"Player {player_id+1}", player_id) for player_id in range(PLAYER_COUNT)]

    # Step 2: Create tournament
    tournament = SwissTournament(players)

    # Step 3: Play multiple rounds (simulate rounds in a Swiss system)
    for round_num in range(tournament.max_rounds):
        play_round(tournament, RANDOM_DROP_RATE)

if __name__ == "__main__":
    for _ in range(1000):
        print("----- NEW TOURNAMENT -----")
        main()
    print(f"Double Bye count: {double_bye_count}")