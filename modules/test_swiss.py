import unittest
from modules.swiss_mtg import Player, Match

class TestSwiss(unittest.TestCase):

    def test_match_points(self):
        test_cases = [
            ((2, 1, 0), (2, 1, 0), (2, 1, 0), (2, 0, 0), (2, 0, 0), (2, 0, 0), (0, 2, 0), (1, 2, 0), 18),
            ((2, 1, 0), (2, 1, 0), (2, 0, 0), (2, 0, 0), (1, 2, 0), (0, 2, 0), (1, 1, 1), (0, 0, 3), 14),
        ]

        for i, (*match_results, expected_points) in enumerate(test_cases):
            with self.subTest(test_case=i):
                player = Player("Player", 1)
                for result in match_results:
                    match = Match(player, Player("Opponent", 2))
                    match.set_result(*result)
                self.assertEqual(player.calculate_match_points(), expected_points)
    
    def test_game_points(self):
        test_cases = [
            ((2, 0, 0), (6, 0)),
            ((2, 1, 0), (6, 3)),
            ((2, 0, 1), (7, 1))
        ]

        for result, (expected_p1, expected_p2) in test_cases:
            with self.subTest(result=result):
                player1 = Player("Player 1", 1)
                player2 = Player("Player 2", 2)
                match = Match(player1, player2)
                match.set_result(*result)

                self.assertEqual(player1.calculate_game_points(), expected_p1)
                self.assertEqual(player2.calculate_game_points(), expected_p2)
    
    def test_match_win_percentage(self):
        test_cases = [
            ((2, 1, 0), (2, 1, 0), (2, 0, 0), (2, 0, 0), (2, 0, 0), (0, 2, 0), (1, 2, 0), (1, 1, 1), 16/(8*3)), #0.667
            ((2, 1, 0), (1, 2, 0), (0, 2, 0), (0, 2, 0), 0.33), # (3/4*3)=0.25
            ("bye", (2, 0, 0), (2, 0, 0), (1, 2, 0), (0, 2, 0), 9/(5*3)), # 0.60
        ]

        for i, (*match_results, expected_percentage) in enumerate(test_cases):
            with self.subTest(test_case=i):
                player = Player("Player", 1)
                i = 0
                for result in match_results:
                    i += 1
                    if result == "bye":
                        match = Match(player, None)
                    else:
                        match = Match(player, Player(f"Opponent {i}", i+1))
                        match.set_result(*result)
                calculated = player.calculate_match_win_percentage()
                self.assertAlmostEqual(calculated, expected_percentage, places=4)

    def test_game_win_percentage(self):
        test_cases = [
            ((2, 0, 0), (2, 1, 0), (1, 2, 0), (2, 0, 0), 21/(3*10)), # 0.70
            ((1, 2, 0), (1, 2, 0), (0, 2, 0), (1, 2, 0), 0.33), # 9/(3*11)=0.27
        ]

        for i, (*match_results, expected_percentage) in enumerate(test_cases):
            with self.subTest(test_case=i):
                player = Player("Player", 1)
                i = 0
                for result in match_results:
                    i += 1
                    if result == "bye":
                        match = Match(player, None)
                    else:
                        match = Match(player, Player(f"Opponent {i}", i+1))
                        match.set_result(*result)
                calculated = player.calculate_game_win_percentage()
                self.assertAlmostEqual(calculated, expected_percentage, places=4)

    def test_omw(self):
        # match records
        test_cases = [
            # (
            #     (6, 2, 0),
            #     [
            #         (4, 4, 0),
            #         (7, 1, 0),
            #         (1, 3, 1),
            #         (3, 3, 1),
            #         (6, 2, 0),
            #         (5, 2, 1),
            #         (4, 3, 1),
            #         (6, 1, 1)
            #     ],
            #     (12/24 + 21/24 + max(4/15, 0.33) + 10/21 + 18/24 + 16/24 + 13/24 + 19/24) / 8
            # ),
            # 12/25 + 21/24 + 4/15 + 10/21 + 18/24 + 16/24 + 13/24 + 19/24 / 8
            # = 0.5 0.88 + 0.33 (raised from 0.27) + 0.48 + 0.75 + 0.67 + 0.54 + 0.78 / 8
            # = 4.94 / 8
            # ~= 6.62
            
            (
                (6, 2, 0),
                [
                    "bye",
                    (7, 1, 0),
                    (1, 3, 1),
                    (3, 3, 1),
                    (6, 2, 0),
                    (5, 2, 1),
                    (4, 3, 1),
                    (6, 1, 1)
                ],
                (21/24 + max(4/15, 0.33) + 10/21 + 18/24 + 16/24 + 13/24 + 19/24) / 7
            ),
            # 21/24 + 4/15 + 10/21 + 18/24 + 16/24 + 13/24 + 19/24 / 7
            # = 0.88 + 0.33 (raised from 0.27) + 0.48 + 0.75 + 0.67 + 0.54 + 0.78 / 7
            # = 4.44 / 7
            # ~= 6.63
        ]

        for i, (player_result, opponent_results, expected_omwp) in enumerate(test_cases):
            with self.subTest(test_case=i):
                player = Player("Player1", 1)

                (player_wins, player_losses, player_draws) = player_result

                generic_player_id = 0

                for idx, match_results in enumerate(opponent_results):
                    if match_results == "bye":
                        match = Match(player, None)
                        continue
                    (wins, losses, draws) = match_results
                    opponent = Player(f"Opponent {idx+1}", idx+2)

                    match = Match(player, opponent)
                    if player_wins > 0:
                        player_wins -= 1
                        losses -= 1
                        match.set_result(2, 0, 0)
                    elif player_losses > 0:
                        player_losses -= 1
                        wins -= 1
                        match.set_result(0, 2, 0)
                    elif player_draws > 0:
                        player_draws -= 1
                        draws -= 1
                        match.set_result(1, 1, 1)
                    else:
                        raise Exception("No more results to set")
                    

                    for _ in range(wins):
                        generic_player_id += 1
                        opponent_match = Match(opponent, Player("Generic", generic_player_id))
                        opponent_match.set_result(2, 0, 0)  # Opponent wins

                    for _ in range(losses):
                        generic_player_id += 1
                        opponent_match = Match(opponent, Player("Generic", generic_player_id))
                        opponent_match.set_result(0, 2, 0)  # Opponent loses

                    for _ in range(draws):
                        generic_player_id += 1
                        opponent_match = Match(opponent, Player("Generic", generic_player_id))
                        opponent_match.set_result(1, 1, 1)  # Opponent draws

                # Verify OMWP calculation
                self.assertAlmostEqual(player.calculate_opponent_match_win_percentage(), expected_omwp, places=4)
    
    def test_same_player_matched_twice(self):
        player1 = Player("Player1", 1)
        player2 = Player("Player2", 2)
        match1 = Match(player1, player2)
        match1.set_result(1, 1, 1)
        with self.assertRaises(ValueError):
            match2 = Match(player1, player2)

if __name__ == "__main__":
    unittest.main()