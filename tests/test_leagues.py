import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import discord
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from modules import swiss_mtg

with patch.dict(os.environ, {"DEBUG": "false"}):
    from modules import db
    from modules.spelltable import tournament_model as model
    from modules.spelltable import common_views
    from cogs.spelltable import spelltable_tournament as cog
    from modules import hall_of_fame


class LeagueScoringTests(unittest.TestCase):
    def setUp(self):
        self.league = swiss_mtg.LeagueTournament([swiss_mtg.Player(player_id) for player_id in (1, 2, 3)])

    def test_independent_matches_and_rematches(self):
        self.league.report_match(1, 2, 2, 0)
        self.league.report_match(1, 3, 1, 1)
        self.league.report_match(2, 3, 1, 1)
        self.league.report_match(2, 1, 2, 1)
        self.assertEqual([player.calculate_match_points() for player in self.league.players], [4, 4, 2])
        self.assertEqual(len(self.league.rounds), 4)

    def test_ready_state_expires_at_saved_deadline(self):
        player = self.league.players[0]
        player.ready_to_play = True
        player.ready_until = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertTrue(player.ready_to_play)
        restored = swiss_mtg.Player.deserialize(player.serialize())
        self.assertEqual(restored.ready_until, player.ready_until)
        self.assertTrue(restored.ready_to_play)
        restored.ready_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertFalse(restored.ready_to_play)

    def test_legacy_readiness_without_deadline_still_loads(self):
        player = swiss_mtg.Player.deserialize({"player_id": 1, "ready_to_play": True})
        self.assertTrue(player.ready_to_play)
        self.assertIsNone(player.ready_until)

    def test_rematch_requires_both_players_to_play_other_active_opponents(self):
        league = swiss_mtg.LeagueTournament([swiss_mtg.Player(player_id) for player_id in (1, 2, 3, 4)])
        league.report_match(1, 2, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (1, 1))
        with self.assertRaisesRegex(ValueError, "Rematch-Cooldown"):
            league.report_match(2, 1, 2, 0)
        self.assertEqual(len(league.rounds), 1)
        league.report_match(1, 3, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (0, 1))
        league.report_match(1, 4, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (0, 1))
        with self.assertRaises(ValueError):
            league.report_match(1, 2, 2, 0)
        league.report_match(2, 3, 2, 0)
        league.report_match(2, 4, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (0, 0))
        league.report_match(1, 2, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (1, 1))

    def test_cooldown_requires_half_of_other_players_rounded_up(self):
        for player_count, required in ((2, 0), (3, 1), (4, 1), (5, 2), (6, 2), (7, 3), (8, 3)):
            with self.subTest(player_count=player_count):
                league = swiss_mtg.LeagueTournament([swiss_mtg.Player(player_id) for player_id in range(1, player_count + 1)])
                league.report_match(1, 2, 2, 0)
                self.assertEqual(league.rematch_cooldown(1, 2), (required, required))

    def test_custom_cooldown_percentage(self):
        for percentage, required in ((0, 0), (25, 1), (50, 2), (75, 3), (100, 4)):
            with self.subTest(percentage=percentage):
                league = swiss_mtg.LeagueTournament(
                    [swiss_mtg.Player(player_id) for player_id in range(1, 7)], cooldown_percentage=percentage
                )
                league.report_match(1, 2, 2, 0)
                self.assertEqual(league.rematch_cooldown(1, 2), (required, required))

    def test_configurable_match_blocks_before_cooldown(self):
        league = swiss_mtg.LeagueTournament(
            [swiss_mtg.Player(player_id) for player_id in range(1, 5)], matches_before_cooldown=2
        )
        league.report_match(1, 2, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (0, 0))
        league.report_match(2, 1, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (1, 1))
        with self.assertRaises(ValueError):
            league.report_match(1, 2, 2, 0)
        league.report_match(1, 3, 2, 0)
        league.report_match(2, 4, 2, 0)
        league.report_match(1, 2, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (0, 0))
        league.report_match(1, 2, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (1, 1))

    def test_matches_before_cooldown_survive_reload(self):
        league = swiss_mtg.LeagueTournament(self.league.players, matches_before_cooldown=2)
        league.report_match(1, 2, 2, 0)
        data = json.loads(json.dumps(league, default=lambda value: value.serialize()))
        restored = swiss_mtg.LeagueTournament.deserialize(data)
        self.assertEqual(restored.matches_before_cooldown, 2)
        restored.report_match(1, 2, 2, 0)
        self.assertEqual(restored.rematch_cooldown(1, 2), (1, 1))

    def test_invalid_cooldown_settings_are_rejected(self):
        invalid_percentages: list[Any] = [-1, 101, 12.5, True]
        for percentage in invalid_percentages:
            with self.subTest(percentage=percentage), self.assertRaises(ValueError):
                swiss_mtg.LeagueTournament([], cooldown_percentage=percentage)
        with self.assertRaises(ValueError):
            invalid_counting: Any = "yes"
            swiss_mtg.LeagueTournament([], count_repeat_opponents=invalid_counting)

    def test_repeated_other_opponent_matches_count_separately_in_saved_history(self):
        data = {
            "rounds_count": 0,
            "players": [{"player_id": player_id} for player_id in range(1, 7)],
            "rounds": [
                {"round_number": number, "matches": [{"wins": {"1": 2, str(opponent): 0, "draws": 0}}]}
                for number, opponent in enumerate((2, 3, 3), start=1)
            ],
        }
        league = swiss_mtg.LeagueTournament.deserialize(data)
        self.assertEqual(league.rematch_cooldown(1, 2), (0, 2))
        data["count_repeat_opponents"] = False
        distinct_league = swiss_mtg.LeagueTournament.deserialize(data)
        self.assertEqual(distinct_league.rematch_cooldown(1, 2), (1, 2))

    def test_custom_cooldown_configuration_survives_serialization(self):
        league = swiss_mtg.LeagueTournament(self.league.players, cooldown_percentage=75, count_repeat_opponents=False)
        data = json.loads(json.dumps(league, default=lambda value: value.serialize()))
        restored = swiss_mtg.LeagueTournament.deserialize(data)
        self.assertEqual(restored.cooldown_percentage, 75)
        self.assertFalse(restored.count_repeat_opponents)

    def test_cooldown_adapts_when_players_drop(self):
        self.league.report_match(1, 2, 2, 0)
        self.assertEqual(self.league.rematch_cooldown(1, 2), (1, 1))
        self.league.players[2].dropped = True
        self.assertEqual(self.league.rematch_cooldown(1, 2), (0, 0))
        self.league.report_match(1, 2, 0, 2)

    def test_unrelated_matches_do_not_reduce_pair_cooldown(self):
        league = swiss_mtg.LeagueTournament([swiss_mtg.Player(player_id) for player_id in (1, 2, 3, 4)])
        league.report_match(1, 2, 2, 0)
        league.report_match(3, 4, 2, 0)
        self.assertEqual(league.rematch_cooldown(1, 2), (1, 1))

    def test_invalid_reports_do_not_mutate_history(self):
        for arguments in ((1, 1, 2, 0), (1, 4, 2, 0), (1, 2, -1, 2), (1, 2, 0, 0)):
            with self.assertRaises(ValueError):
                self.league.report_match(*arguments)
        self.assertEqual(self.league.rounds, [])
        self.assertTrue(all(not player.match_history for player in self.league.players))

    def test_dropped_players_cannot_report(self):
        self.league.players[1].dropped = True
        with self.assertRaises(ValueError):
            self.league.report_match(1, 2, 2, 0)

    def test_swiss_still_rejects_rematches(self):
        player1, player2 = self.league.players[:2]
        swiss_mtg.Match(player1, player2, 1)
        with self.assertRaises(ValueError):
            swiss_mtg.Match(player1, player2, 2)

    def test_league_does_not_generate_pairings(self):
        with self.assertRaises(ValueError):
            self.league.pair_players()

    def test_reload_preserves_rematches_and_points(self):
        self.league.players[2].dropped = True
        self.league.report_match(1, 2, 2, 0)
        self.league.report_match(1, 2, 0, 2)
        data = json.loads(json.dumps(self.league, default=lambda value: value.serialize()))
        restored = swiss_mtg.LeagueTournament.deserialize(data)
        self.assertIsInstance(restored, swiss_mtg.LeagueTournament)
        self.assertEqual(len(restored.rounds), 2)
        self.assertEqual([player.calculate_match_points() for player in restored.players], [3, 3, 0])

    def test_cooldown_survives_reload(self):
        self.league.report_match(1, 2, 2, 0)
        self.league.report_match(1, 3, 2, 0)
        data = json.loads(json.dumps(self.league, default=lambda value: value.serialize()))
        restored = swiss_mtg.LeagueTournament.deserialize(data)
        self.assertEqual(restored.rematch_cooldown(1, 2), (0, 1))
        with self.assertRaises(ValueError):
            restored.report_match(1, 2, 2, 0)


class LeagueTournamentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'test.db'}")
        self.addCleanup(self.engine.dispose)
        db.Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.addCleanup(patch.stopall)
        patch.object(model, "SessionLocal", self.session_factory).start()
        self.guild = Mock(spec=discord.Guild, id=123)
        self.guild.get_member.return_value = None
        self.bot = Mock(spec=discord.Bot)
        self.bot.get_guild.return_value = self.guild
        self.bot.get_user.return_value = None
        self.tournament: Any = model.SpelltableTournament(self.guild, "League", 1, self.bot)
        self.tournament.suppress_overview_update = True
        self.tournament.tournament_style = model.TournamentStyle.LEAGUE
        self.tournament.time = datetime.now(timezone.utc) - timedelta(days=1)
        self.tournament.end_time = datetime.now(timezone.utc) + timedelta(days=1)
        self.tournament.message_id = 456
        self.tournament.channel_id = 789
        self.tournament.users = {player_id: model.ParticipationState.PARTICIPATE for player_id in (1, 2, 3)}
        self.tournament.swiss_tournament = swiss_mtg.LeagueTournament([swiss_mtg.Player(player_id) for player_id in (1, 2, 3)])
        self.tournament.get_member = AsyncMock(return_value=None)

    async def test_database_roundtrip(self):
        self.tournament.swiss_tournament.player_by_id(3).dropped = True
        self.tournament.swiss_tournament.player_by_id(1).ready_to_play = True
        ready_until = datetime.now(timezone.utc) + timedelta(hours=1)
        self.tournament.swiss_tournament.player_by_id(1).ready_until = ready_until
        self.tournament.report_league_match(1, 2, 2, 0)
        self.tournament.report_league_match(1, 2, 0, 2)
        await self.tournament.save_tournament()
        loaded = await model.load_tournaments(self.bot)
        restored = loaded["123/789/456"]
        self.assertTrue(restored.is_league)
        self.assertIsInstance(restored.swiss_tournament, swiss_mtg.LeagueTournament)
        self.assertEqual(len(restored.swiss_tournament.rounds), 2)
        self.assertEqual(restored.end_time, self.tournament.end_time)
        self.assertEqual(restored.calc_end(), restored.end_time)
        self.assertTrue(restored.swiss_tournament.player_by_id(1).ready_to_play)
        self.assertFalse(restored.swiss_tournament.player_by_id(2).ready_to_play)
        self.assertEqual(restored.swiss_tournament.players[0].ready_until, ready_until)

    async def test_expired_creation_logs_traceback_and_acknowledgement_timing(self):
        error = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), {"code": 10062, "message": "Unknown interaction"}
        )
        context = Mock(spec=discord.ApplicationContext)
        context.defer = AsyncMock(side_effect=error)
        context.interaction = SimpleNamespace(
            id=999, created_at=datetime.now(timezone.utc) - timedelta(seconds=5)
        )
        context.guild = SimpleNamespace(id=123)
        context.channel = SimpleNamespace(id=789)
        context.author = SimpleNamespace(id=1)
        context.followup = SimpleNamespace(send=AsyncMock())
        manager = Mock(spec=cog.SpelltableTournamentManager)
        with self.assertLogs(cog.log, level="ERROR") as captured:
            await cog.SpelltableTournamentManager.turnier_erstellen.callback(manager, context, "Test", "Liga")
        record = captured.records[0]
        self.assertIsNotNone(record.exc_info)
        self.assertIs(record.exc_info[1], error)
        self.assertIn("age_before_defer=", record.getMessage())
        self.assertIn("defer_duration=", record.getMessage())
        self.assertIn("discord_code=10062", record.getMessage())
        self.assertIn("interaction_id=999", record.getMessage())
        self.assertGreaterEqual(record.args[4], 5)
        context.followup.send.assert_not_awaited()

    async def test_readiness_migration_defaults_existing_players_to_unready(self):
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE swiss_players"))
            connection.execute(text("CREATE TABLE swiss_players (id INTEGER PRIMARY KEY, player_id VARCHAR)"))
            connection.execute(text("INSERT INTO swiss_players (player_id) VALUES ('1')"))
        with patch.object(db, "engine", self.engine):
            db._migrate_add_player_readiness_column()
            db._migrate_add_player_readiness_column()
        with self.engine.connect() as connection:
            ready = connection.execute(text("SELECT ready_to_play FROM swiss_players")).scalar_one()
        self.assertEqual(ready, 0)

    async def test_custom_league_rules_survive_database_roundtrip(self):
        self.tournament.league_cooldown_percentage = 75
        self.tournament.league_count_repeat_opponents = False
        self.tournament.league_matches_before_cooldown = 2
        self.tournament.swiss_tournament = swiss_mtg.LeagueTournament(
            [swiss_mtg.Player(player_id) for player_id in range(1, 7)],
            cooldown_percentage=75, count_repeat_opponents=False, matches_before_cooldown=2,
        )
        self.tournament.report_league_match(1, 2, 2, 0)
        self.tournament.report_league_match(1, 2, 2, 0)
        await self.tournament.save_tournament()
        loaded = await model.load_tournaments(self.bot)
        restored = loaded["123/789/456"]
        self.assertEqual(restored.league_cooldown_percentage, 75)
        self.assertFalse(restored.league_count_repeat_opponents)
        competition = restored.swiss_tournament
        assert isinstance(competition, swiss_mtg.LeagueTournament)
        self.assertEqual(competition.cooldown_percentage, 75)
        self.assertFalse(competition.count_repeat_opponents)
        self.assertEqual(restored.league_matches_before_cooldown, 2)
        self.assertEqual(competition.matches_before_cooldown, 2)
        self.assertEqual(competition.rematch_cooldown(1, 2), (3, 3))

    async def test_unstarted_league_rules_survive_database_roundtrip(self):
        self.tournament.swiss_tournament = None
        self.tournament.league_cooldown_percentage = 0
        self.tournament.league_count_repeat_opponents = False
        await self.tournament.save_tournament()
        loaded = await model.load_tournaments(self.bot)
        self.assertEqual(loaded["123/789/456"].league_cooldown_percentage, 0)
        self.assertFalse(loaded["123/789/456"].league_count_repeat_opponents)

    async def test_player_can_join_started_league_and_reload(self):
        await self.tournament.save_tournament()
        self.tournament._message = SimpleNamespace(jump_url="https://discord.com/channels/123/789/456")
        with patch.object(common_views, "update_league_message", AsyncMock()) as refresh:
            result = await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
        self.assertIsNone(result)
        self.assertIsNotNone(self.tournament.swiss_tournament.player_by_id(4))
        self.assertEqual(self.tournament.users[4], model.ParticipationState.PARTICIPATE)
        refresh.assert_awaited_once_with(self.tournament)
        loaded = await model.load_tournaments(self.bot)
        self.assertIsNotNone(loaded["123/789/456"].swiss_tournament.player_by_id(4))
        self.assertEqual({option.value for option in common_views._league_opponent_options(self.tournament, 1)}, {"2", "3", "4"})

    async def test_joining_twice_does_not_duplicate_player(self):
        await self.tournament.save_tournament()
        with patch.object(common_views, "update_league_message", AsyncMock()):
            result = await self.tournament.user_state(1, model.ParticipationState.PARTICIPATE)
        self.assertIn("bereits", result)
        self.assertEqual(len(self.tournament.swiss_tournament.players), 3)

    async def test_late_join_respects_capacity_and_waitlist(self):
        self.tournament.max_participants = 3
        await self.tournament.save_tournament()
        self.tournament._message = SimpleNamespace(jump_url="https://discord.com/channels/123/789/456")
        with patch.object(common_views, "update_league_message", AsyncMock()):
            result = await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
        self.assertIn("voll", result)
        self.assertIn(4, self.tournament.waitlist)
        self.assertIsNone(self.tournament.swiss_tournament.player_by_id(4))

    async def test_late_join_rejects_banned_player(self):
        await self.tournament.save_tournament()
        with self.session_factory() as session:
            session.add(db.BannedUser(user_id="4", reason="Test ban"))
            session.commit()
        result = await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
        self.assertIn("gesperrt", result)
        self.assertIsNone(self.tournament.swiss_tournament.player_by_id(4))

    async def test_late_join_rejects_ended_or_cancelled_league(self):
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        result = await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
        self.assertIn("beendet", result)
        self.assertTrue(common_views.LeagueView(self.tournament).join_button.disabled)
        self.tournament.end_time = datetime.now(timezone.utc) + timedelta(days=1)
        self.tournament.cancelled = True
        result = await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
        self.assertIn("beendet", result)
        self.assertIsNone(self.tournament.swiss_tournament.player_by_id(4))

    async def test_late_join_failed_save_restores_roster(self):
        self.tournament.save_tournament = AsyncMock(side_effect=RuntimeError("Database unavailable"))
        with self.assertRaises(RuntimeError):
            await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
        self.assertNotIn(4, self.tournament.users)
        self.assertIsNone(self.tournament.swiss_tournament.player_by_id(4))

    async def test_join_button_acknowledges_and_joins_league(self):
        _, interaction = self.make_report(reporter_id=4)
        self.tournament.user_state = AsyncMock(return_value=None)
        view = common_views.LeagueView(self.tournament)
        await view.join_button.callback(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.tournament.user_state.assert_awaited_once_with(4, model.ParticipationState.PARTICIPATE)

    async def test_late_waitlist_promotion_adds_player_to_started_league(self):
        self.tournament.max_participants = 3
        await self.tournament.save_tournament()
        self.tournament._message = SimpleNamespace(jump_url="https://discord.com/channels/123/789/456")
        self.tournament._notify_waitlist_promotion = AsyncMock()
        with patch.object(common_views, "update_league_message", AsyncMock()):
            await self.tournament.user_state(4, model.ParticipationState.PARTICIPATE)
            await self.tournament.user_state(2, model.ParticipationState.DECLINE)
        self.assertNotIn(4, self.tournament.waitlist)
        self.assertEqual(self.tournament.users[4], model.ParticipationState.PARTICIPATE)
        self.assertIsNotNone(self.tournament.swiss_tournament.player_by_id(4))
        self.assertTrue(self.tournament.swiss_tournament.player_by_id(2).dropped)
        self.tournament._notify_waitlist_promotion.assert_awaited_once_with(4)

    async def test_rejoining_league_preserves_existing_match_history(self):
        self.tournament.report_league_match(3, 1, 2, 0)
        original_player = self.tournament.swiss_tournament.player_by_id(3)
        await self.tournament.save_tournament()
        self.tournament._message = SimpleNamespace(jump_url="https://discord.com/channels/123/789/456")
        with patch.object(common_views, "update_league_message", AsyncMock()):
            await self.tournament.user_state(3, model.ParticipationState.DECLINE)
            await self.tournament.user_state(3, model.ParticipationState.PARTICIPATE)
        self.assertIs(self.tournament.swiss_tournament.player_by_id(3), original_player)
        self.assertFalse(original_player.dropped)
        self.assertFalse(original_player.ready_to_play)
        self.assertEqual(len(original_player.match_history), 1)
        self.assertEqual(len(self.tournament.swiss_tournament.players), 3)

    async def test_rules_modal_rejects_nonpositive_match_allowance(self):
        self.tournament.swiss_tournament = None
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        modal = cog.LeagueRulesModal(self.tournament, view)
        modal.matches_input = Mock(spec=discord.ui.InputText, value="0")
        modal.counting_select = Mock(spec=discord.ui.StringSelect, values=["matches"])
        interaction = Mock(spec=discord.Interaction)
        interaction.response = SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock())
        await modal.callback(interaction)
        self.assertEqual(self.tournament.league_matches_before_cooldown, 1)
        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()

    async def test_deadline_and_start_are_enforced(self):
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.assertRaises(ValueError):
            self.tournament.report_league_match(1, 2, 2, 0)
        self.tournament.end_time = datetime.now(timezone.utc) + timedelta(days=3)
        self.tournament.time = datetime.now(timezone.utc) + timedelta(days=1)
        with self.assertRaises(ValueError):
            self.tournament.report_league_match(1, 2, 2, 0)
        self.assertEqual(self.tournament.swiss_tournament.rounds, [])

    async def test_end_time_is_mandatory_and_after_start(self):
        for end_time in (None, self.tournament.time, self.tournament.time - timedelta(seconds=1)):
            self.tournament.end_time = end_time
            with self.assertRaises(ValueError):
                self.tournament.validate_configuration()

    async def test_existing_swiss_configuration_needs_no_end_time(self):
        self.tournament.tournament_style = model.TournamentStyle.SWISS
        self.tournament.end_time = None
        self.tournament.validate_configuration()

    async def test_migration_defaults_existing_records_to_swiss(self):
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE tournaments"))
            connection.execute(text("CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name VARCHAR)"))
            connection.execute(text("INSERT INTO tournaments (name) VALUES ('Existing Swiss')"))
        with patch.object(db, "engine", self.engine), patch.object(db, "DB_PATH", Path(self.directory.name) / "test.db"):
            db._migrate_add_league_columns()
            db._migrate_add_league_columns()
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT tournament_style, end_time FROM tournaments")).one()
            rules = connection.execute(text("SELECT league_cooldown_percentage, league_count_repeat_opponents, league_matches_before_cooldown FROM tournaments")).one()
        self.assertEqual(tuple(row), ("swiss", None))
        self.assertEqual(tuple(rules), (50, 1, 1))

    async def test_creation_requires_league_end_time(self):
        self.tournament.end_time = None
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        self.assertTrue(view.submit_button.disabled)
        self.assertFalse(view.end_time_callback.disabled)
        self.assertTrue(view.round_count_callback.disabled)
        self.assertTrue(view.days_per_match_callback.disabled)
        self.tournament.end_time = self.tournament.time + timedelta(days=10)
        view._refresh_mandatory_field_labels()
        self.assertFalse(view.submit_button.disabled)

    def make_start_interaction(self):
        member = object.__new__(discord.Member)
        member._user = Mock(spec=discord.User, id=self.tournament.organizer_id)
        interaction = Mock(spec=discord.Interaction, user=member)
        interaction.response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        interaction.edit_original_response = AsyncMock()
        return interaction

    async def test_creator_can_open_and_change_rematch_rules_in_modal(self):
        self.tournament.swiss_tournament = None
        self.tournament.to_embed = AsyncMock(return_value=discord.Embed(title="League"))
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        self.assertFalse(view.league_rules_callback.disabled)
        interaction = Mock(spec=discord.Interaction)
        interaction.response = SimpleNamespace(send_modal=AsyncMock(), edit_message=AsyncMock(), send_message=AsyncMock())
        await view.league_rules_callback.callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, cog.LeagueRulesModal)
        self.assertEqual(modal.percentage_input.value, "50")
        self.assertEqual([option.value for option in modal.counting_select.options if option.default], ["matches"])
        modal.percentage_input = Mock(spec=discord.ui.InputText, value="75")
        self.assertEqual(modal.matches_input.value, "1")
        modal.matches_input = Mock(spec=discord.ui.InputText, value="2")
        modal.counting_select = Mock(spec=discord.ui.StringSelect, values=["distinct"])
        await modal.callback(interaction)
        self.assertEqual(self.tournament.league_cooldown_percentage, 75)
        self.assertEqual(self.tournament.league_matches_before_cooldown, 2)
        self.assertFalse(self.tournament.league_count_repeat_opponents)
        interaction.response.edit_message.assert_awaited_once_with(embed=self.tournament.to_embed.return_value, view=view)

    async def test_rules_modal_rejects_invalid_percentage_without_changing_rules(self):
        self.tournament.swiss_tournament = None
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        for value in ("-1", "101", "abc"):
            with self.subTest(value=value):
                modal = cog.LeagueRulesModal(self.tournament, view)
                modal.percentage_input = Mock(spec=discord.ui.InputText, value=value)
                modal.counting_select = Mock(spec=discord.ui.StringSelect, values=["distinct"])
                interaction = Mock(spec=discord.Interaction)
                interaction.response = SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock())
                await modal.callback(interaction)
                self.assertEqual(self.tournament.league_cooldown_percentage, 50)
                self.assertTrue(self.tournament.league_count_repeat_opponents)
                interaction.response.send_message.assert_awaited_once()
                interaction.response.edit_message.assert_not_awaited()

    async def test_rules_modal_cannot_change_started_league(self):
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        self.assertTrue(view.league_rules_callback.disabled)
        modal = cog.LeagueRulesModal(self.tournament, view)
        interaction = Mock(spec=discord.Interaction)
        interaction.response = SimpleNamespace(send_message=AsyncMock())
        await modal.callback(interaction)
        interaction.response.send_message.assert_awaited_once()

    async def test_league_details_display_creator_rules(self):
        self.tournament._organizer = SimpleNamespace(display_name="Organizer", avatar=None)
        self.tournament.league_cooldown_percentage = 75
        self.tournament.league_count_repeat_opponents = False
        embed = await self.tournament.to_embed()
        rules = next(field.value for field in embed.fields if field.name == "Rematch-Cooldown")
        self.assertIn("75%", rules)
        self.assertIn("unterschiedliche Gegner", rules)

    async def test_rules_button_is_disabled_for_swiss(self):
        self.tournament.tournament_style = model.TournamentStyle.SWISS
        self.tournament.swiss_tournament = None
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        self.assertTrue(view.league_rules_callback.disabled)

    async def test_league_start_uses_creator_rules(self):
        self.tournament.swiss_tournament = None
        self.tournament.league_cooldown_percentage = 100
        self.tournament.league_count_repeat_opponents = False
        self.tournament.save_tournament_swiss = AsyncMock()
        interaction = self.make_start_interaction()
        with patch.object(common_views, "update_league_message", AsyncMock()):
            view = await cog.ParticipationView.create(self.tournament)
            await view.start_button.callback(interaction)
        competition = getattr(self.tournament, "swiss_tournament")
        assert isinstance(competition, swiss_mtg.LeagueTournament)
        self.assertEqual(competition.cooldown_percentage, 100)
        self.assertFalse(competition.count_repeat_opponents)

    async def test_league_start_acknowledges_before_saving_and_rendering(self):
        self.tournament.swiss_tournament = None
        interaction = self.make_start_interaction()

        async def check_acknowledged():
            interaction.response.send_message.assert_awaited_once_with("Starte Liga...", ephemeral=True)
            interaction.edit_original_response.assert_not_awaited()

        self.tournament.save_tournament_swiss = AsyncMock(side_effect=check_acknowledged)
        with patch.object(common_views, "update_league_message", AsyncMock()) as refresh:
            view = await cog.ParticipationView.create(self.tournament)
            await view.start_button.callback(interaction)
        refresh.assert_awaited_once_with(self.tournament)
        interaction.edit_original_response.assert_awaited_once_with(content="Die Liga wurde gestartet.")

    async def test_swiss_start_acknowledges_before_preparing_round(self):
        self.tournament.tournament_style = model.TournamentStyle.SWISS
        self.tournament.swiss_tournament = None
        self.tournament._message = SimpleNamespace(edit=AsyncMock())
        self.tournament.get_member = AsyncMock()
        interaction = self.make_start_interaction()

        async def check_acknowledged(*arguments):
            interaction.response.send_message.assert_awaited_once_with("Starte Turnier...", ephemeral=True)
            interaction.edit_original_response.assert_not_awaited()

        with patch.object(cog, "next_round", AsyncMock(side_effect=check_acknowledged)):
            view = await cog.ParticipationView.create(self.tournament)
            await view.start_button.callback(interaction)
        interaction.edit_original_response.assert_awaited_once_with(content="Das Turnier wurde gestartet.")

    async def test_failed_league_start_replaces_progress_with_error(self):
        self.tournament.swiss_tournament = None
        self.tournament.save_tournament_swiss = AsyncMock(side_effect=RuntimeError("Database unavailable"))
        interaction = self.make_start_interaction()
        view = await cog.ParticipationView.create(self.tournament)
        with self.assertRaises(RuntimeError):
            await view.start_button.callback(interaction)
        self.assertIsNone(self.tournament.swiss_tournament)
        interaction.edit_original_response.assert_awaited_once_with(content="Die Liga konnte nicht gestartet werden.")

    async def test_league_start_validation_replaces_progress_message(self):
        self.tournament.swiss_tournament = None
        self.tournament.end_time = None
        interaction = self.make_start_interaction()
        view = await cog.ParticipationView.create(self.tournament)
        await view.start_button.callback(interaction)
        interaction.edit_original_response.assert_awaited_once()
        self.assertIn("Endzeit", interaction.edit_original_response.await_args.kwargs["content"])

    async def test_creation_requires_style_selection(self):
        option = next(option for option in cog.SpelltableTournamentManager.turnier_erstellen.options if option.name == "turnierart")
        self.assertTrue(option.required)
        self.assertEqual([choice.value for choice in option.choices], ["Swiss", "Liga"])

    async def test_format_button_opens_modal_without_sending_message(self):
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        interaction = Mock(spec=discord.Interaction)
        interaction.response = SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock())
        await view.format_callback.callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, cog.SelectFormatModal)
        self.assertIs(modal.view, view)
        interaction.response.send_message.assert_not_awaited()

    async def test_format_modal_updates_existing_creation_message(self):
        self.tournament.format_name = "Modern"
        self.tournament.to_embed = AsyncMock(return_value=discord.Embed(title="Tournament"))
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        modal = cog.SelectFormatModal(self.tournament, view)
        self.assertEqual([option.value for option in modal.format_select.options if option.default], ["Modern"])
        modal.format_select = Mock(spec=discord.ui.StringSelect, values=["Pauper"])
        interaction = Mock(spec=discord.Interaction)
        interaction.response = SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock())
        await modal.callback(interaction)
        self.assertEqual(self.tournament.format_name, "Pauper")
        interaction.response.edit_message.assert_awaited_once_with(
            embed=self.tournament.to_embed.return_value, view=view
        )
        interaction.response.send_message.assert_not_awaited()

    async def test_format_modal_allows_clearing_selection(self):
        self.tournament.format_name = "Modern"
        self.tournament.to_embed = AsyncMock(return_value=discord.Embed(title="Tournament"))
        view = await cog.EditTournamentView.create(self.tournament, Mock(spec=discord.TextChannel))
        modal = cog.SelectFormatModal(self.tournament, view)
        self.assertFalse(modal.format_select.required)
        modal.format_select = Mock(spec=discord.ui.StringSelect, values=[])
        interaction = Mock(spec=discord.Interaction)
        interaction.response = SimpleNamespace(edit_message=AsyncMock())
        await modal.callback(interaction)
        self.assertIsNone(self.tournament.format_name)
        interaction.response.edit_message.assert_awaited_once()

    def make_report(self, opponent_id=2, reporter_id=1):
        modal = common_views.LeagueReportModal(self.tournament, 1)
        modal.opponent_select = Mock(spec=discord.ui.StringSelect, values=[str(opponent_id)])
        modal.score_selects = [SimpleNamespace(values=[score]) for score in ("2", "0", "0")]
        interaction = Mock(
            spec=discord.Interaction,
            user=SimpleNamespace(id=reporter_id),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.tournament._message = SimpleNamespace(channel=SimpleNamespace(send=AsyncMock()))
        self.tournament.save_tournament_swiss = AsyncMock()
        return modal, interaction

    async def test_report_is_saved_once_and_refreshes_standings(self):
        modal, interaction = self.make_report()
        with patch.object(common_views, "update_league_message", new_callable=AsyncMock) as refresh:
            await modal.callback(interaction)
            await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_awaited_once()
        refresh.assert_awaited_once_with(self.tournament)
        self.assertEqual(len(self.tournament.swiss_tournament.rounds), 1)
        self.assertIn("bereits", interaction.followup.send.await_args.args[0])

    async def test_opponent_dropdown_contains_only_other_active_participants(self):
        self.tournament.swiss_tournament.player_by_id(3).dropped = True
        self.tournament.members[2] = SimpleNamespace(display_name="Opponent Name")
        modal = common_views.LeagueReportModal(self.tournament, 1)
        self.assertIsInstance(modal.opponent_select, discord.ui.Select)
        self.assertEqual([(option.label, option.value) for option in modal.opponent_select.options], [("Opponent Name", "2")])

    async def test_dropdown_displays_remaining_cooldown_for_both_players(self):
        self.tournament.report_league_match(1, 2, 2, 0)
        options = {option.value: option for option in common_views._league_opponent_options(self.tournament, 1)}
        self.assertIn("du 1, Gegner 1", options["2"].description)
        self.assertEqual(str(options["2"].emoji), "⏳")
        self.assertIn("Spielbar", options["3"].description)
        self.assertEqual(str(options["3"].emoji), "✅")
        self.tournament.report_league_match(1, 3, 2, 0)
        options = {option.value: option for option in common_views._league_opponent_options(self.tournament, 1)}
        self.assertIn("du 0, Gegner 1", options["2"].description)
        self.tournament.report_league_match(2, 3, 2, 0)
        options = {option.value: option for option in common_views._league_opponent_options(self.tournament, 1)}
        self.assertIn("Spielbar", options["2"].description)

    async def test_stale_report_modal_cannot_bypass_cooldown(self):
        modal, interaction = self.make_report()
        self.tournament.report_league_match(1, 2, 2, 0)
        await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()
        self.assertIn("Rematch-Cooldown", interaction.followup.send.await_args.args[0])
        self.assertEqual(len(self.tournament.swiss_tournament.rounds), 1)

    async def test_report_button_handles_no_other_active_players(self):
        for player in self.tournament.swiss_tournament.players[1:]:
            player.dropped = True
        _, interaction = self.make_report()
        interaction.response.send_modal = AsyncMock()
        await common_views.LeagueView(self.tournament).report_button.callback(interaction)
        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_large_opponent_dropdown_is_paginated_without_losing_players(self):
        self.tournament.swiss_tournament = swiss_mtg.LeagueTournament([swiss_mtg.Player(number) for number in range(1, 63)])
        self.tournament.swiss_tournament.player_by_id(62).dropped = True
        _, interaction = self.make_report()
        interaction.response.send_modal = AsyncMock()
        await common_views.LeagueView(self.tournament).report_button.callback(interaction)
        first_view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertIsInstance(first_view, common_views.LeagueOpponentView)
        self.assertEqual(len(first_view.opponent_select.options), 25)
        self.assertTrue(first_view.previous_button.disabled)
        ids = set()
        options = common_views._league_opponent_options(self.tournament, 1)
        for page in range(3):
            view = common_views.LeagueOpponentView(self.tournament, 1, options, page)
            self.assertLessEqual(len(view.opponent_select.options), 25)
            ids.update(option.value for option in view.opponent_select.options)
        self.assertEqual(ids, {str(number) for number in range(2, 62)})

    async def test_opponent_dropped_after_opening_modal_cannot_be_reported(self):
        modal, interaction = self.make_report()
        self.tournament.swiss_tournament.player_by_id(2).dropped = True
        await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()

    async def test_paged_opponent_selection_opens_result_modal_with_selection(self):
        self.tournament.swiss_tournament = swiss_mtg.LeagueTournament([swiss_mtg.Player(number) for number in range(1, 32)])
        options = common_views._league_opponent_options(self.tournament, 1)
        view = common_views.LeagueOpponentView(self.tournament, 1, options, page=1)
        _, interaction = self.make_report()
        interaction.response.send_modal = AsyncMock()
        selected_id = view.opponent_select.options[0].value
        await common_views.LeagueOpponentView.opponent_select(view, Mock(values=[selected_id]), interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, common_views.LeagueReportModal)
        self.assertEqual([option.value for option in modal.opponent_select.options if option.default], [selected_id])
        self.assertLessEqual(len(modal.opponent_select.options), 25)
        self.assertEqual(modal.opponent_select.options[0].description, view.opponent_select.options[0].description)
        self.assertEqual(str(modal.opponent_select.options[0].emoji), str(view.opponent_select.options[0].emoji))

    async def test_opponent_picker_next_page_edits_existing_picker(self):
        self.tournament.swiss_tournament = swiss_mtg.LeagueTournament([swiss_mtg.Player(number) for number in range(1, 32)])
        options = common_views._league_opponent_options(self.tournament, 1)
        view = common_views.LeagueOpponentView(self.tournament, 1, options)
        _, interaction = self.make_report()
        interaction.response.edit_message = AsyncMock()
        await view.next_button.callback(interaction)
        next_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(next_view.page, 1)
        self.assertTrue(next_view.next_button.disabled)

    async def test_report_cannot_impersonate_another_player(self):
        modal, interaction = self.make_report(reporter_id=3)
        await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()
        self.assertEqual(self.tournament.swiss_tournament.rounds, [])

    async def test_report_rejects_nonparticipant(self):
        modal, interaction = self.make_report(opponent_id=999)
        await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()
        self.assertEqual(self.tournament.swiss_tournament.rounds, [])

    async def test_failed_save_rolls_back_in_memory_result(self):
        modal, interaction = self.make_report()
        self.tournament.save_tournament_swiss.side_effect = RuntimeError("Database unavailable")
        with patch.object(common_views.link_log, "exception"):
            await modal.callback(interaction)
        self.assertFalse(modal.submitted)
        self.assertEqual(self.tournament.swiss_tournament.rounds, [])
        self.assertTrue(all(not player.match_history for player in self.tournament.swiss_tournament.players))

    async def test_expired_modal_cannot_submit(self):
        modal, interaction = self.make_report()
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()

    async def test_expired_league_without_matches_refreshes_and_disables_reporting(self):
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        image = Path(self.directory.name) / "standings.png"
        image.write_bytes(b"test image")
        self.tournament._message = SimpleNamespace(channel=SimpleNamespace(), edit=AsyncMock())
        self.tournament.standings_to_image = AsyncMock(return_value=str(image))
        self.tournament.to_embed = AsyncMock(return_value=discord.Embed(title="League"))
        self.tournament.save_tournament_swiss = AsyncMock()
        await common_views.update_league_message(self.tournament)
        kwargs = self.tournament._message.edit.await_args.kwargs
        self.assertTrue(kwargs["view"].report_button.disabled)
        self.assertTrue(kwargs["file"].fp.closed)
        self.assertTrue(self.tournament._league_end_announced)
        self.assertIsNone(self.tournament.swiss_tournament.winner)

    async def test_deadline_selects_winner_and_saves_final_standings(self):
        self.tournament.report_league_match(1, 2, 2, 0)
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        image = Path(self.directory.name) / "standings.png"
        image.write_bytes(b"test image")
        self.tournament._message = SimpleNamespace(channel=SimpleNamespace(), edit=AsyncMock())
        self.tournament.standings_to_image = AsyncMock(return_value=str(image))
        self.tournament.to_embed = AsyncMock(return_value=discord.Embed(title="League"))
        self.tournament.save_tournament_swiss = AsyncMock()
        await common_views.update_league_message(self.tournament)
        self.assertEqual(self.tournament.swiss_tournament.winner.player_id, 1)
        self.tournament.save_tournament_swiss.assert_awaited_once()

    async def test_started_league_restores_without_swiss_round_messages(self):
        manager = Mock(spec=cog.SpelltableTournamentManager, bot=self.bot)
        manager._refresh_historical_pairings_messages = AsyncMock()
        self.tournament._message = SimpleNamespace(channel=SimpleNamespace())
        with (
            patch.object(cog, "load_tournaments", AsyncMock(return_value={"123/789/456": self.tournament})),
            patch.object(cog, "active_tournaments", {}),
            patch.object(cog, "update_tournament_message", AsyncMock()),
            patch.object(common_views, "update_league_message", AsyncMock()) as refresh,
            patch.object(common_views, "update_hall_of_fame_batch", AsyncMock()),
        ):
            await cog.SpelltableTournamentManager.on_ready(manager)
        refresh.assert_awaited_once_with(self.tournament)
        manager._refresh_historical_pairings_messages.assert_not_awaited()
        self.assertTrue(manager._tournaments_loaded)

    async def test_deadline_task_closes_once(self):
        manager = Mock(spec=cog.SpelltableTournamentManager, bot=self.bot, _tournaments_loaded=True)
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)

        async def refresh(tournament):
            tournament._league_end_announced = True

        with (
            patch.object(cog, "active_tournaments", {"123/789/456": self.tournament}),
            patch.object(common_views, "update_league_message", AsyncMock(side_effect=refresh)) as refresh_mock,
            patch.object(cog, "update_tournament_message", AsyncMock()) as overview,
        ):
            await cog.SpelltableTournamentManager.schedule_league_end.coro(manager)
            await cog.SpelltableTournamentManager.schedule_league_end.coro(manager)
        refresh_mock.assert_awaited_once_with(self.tournament)
        overview.assert_awaited_once()

    async def test_hall_of_fame_handles_league_rematches(self):
        self.tournament.swiss_tournament.player_by_id(3).dropped = True
        self.tournament.report_league_match(1, 2, 2, 0)
        self.tournament.report_league_match(1, 2, 2, 1)
        self.tournament.swiss_tournament.winner = self.tournament.swiss_tournament.player_by_id(1)
        await self.tournament.save_tournament()
        with patch.object(hall_of_fame, "SessionLocal", self.session_factory):
            results = hall_of_fame.HallOfFame()
        self.assertEqual(len(results.tournaments), 1)
        record = results.tournaments[0]
        self.assertEqual(record.tournament_id, "123/789/456")
        self.assertEqual(record.standings_url, "https://discord.com/channels/123/789/456")
        self.assertEqual(record.results[0]["match_record"], "2-0-0")

    async def test_standings_before_first_league_match(self):
        self.tournament.get_id = AsyncMock(return_value="123/789/456")
        self.tournament.get_member = AsyncMock(return_value=None)
        with patch.object(model.table_to_image, "generate_image") as generate:
            filename = await self.tournament.standings_to_image()
        self.assertIn("round_0", filename)
        self.assertEqual(len(generate.call_args.args[0]["rows"]), 3)

    async def test_standings_mentions_every_player_with_readiness(self):
        self.tournament.swiss_tournament.player_by_id(1).ready_to_play = True
        self.tournament.swiss_tournament.player_by_id(3).dropped = True
        self.tournament._message = SimpleNamespace(channel=SimpleNamespace(), edit=AsyncMock())
        self.tournament.to_embed = AsyncMock(return_value=discord.Embed(title="League"))
        self.tournament.standings_to_image = AsyncMock()
        self.tournament.get_member = AsyncMock(side_effect=lambda user_id: SimpleNamespace(display_name=f"Player {user_id}"))
        await common_views.update_league_message(self.tournament, refresh_standings=False)
        kwargs = self.tournament._message.edit.await_args.kwargs
        roster = kwargs["content"]
        legend = "🟢 Bereit · 🔴 Nicht bereit · ⛔ Ausgestiegen · ⚪ Liga beendet"
        self.assertIn(legend, roster)
        self.assertLess(roster.index(legend), roster.index("<@1>"))
        self.assertIn("🟢 <@1> [Player 1]", roster)
        self.assertIn("🔴 <@2> [Player 2]", roster)
        self.assertIn("⛔ <@3> [Player 3]", roster)
        self.assertFalse(any("<@" in field.value for field in kwargs["embed"].fields))
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertNotIn("attachments", kwargs)
        self.tournament.standings_to_image.assert_not_awaited()

    async def test_readiness_buttons_update_only_the_clicking_participant(self):
        _, interaction = self.make_report()
        view = common_views.LeagueView(self.tournament)
        with patch.object(common_views, "update_league_message", AsyncMock()) as refresh:
            await view.ready_button.callback(interaction)
            self.assertFalse(self.tournament.swiss_tournament.player_by_id(1).ready_to_play)
            modal = interaction.response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, common_views.ReadyDurationModal)
            self.assertEqual(modal.duration_input.value, "60")
            modal.duration_input = Mock(spec=discord.ui.InputText, value="30")
            await modal.callback(interaction)
            self.assertTrue(self.tournament.swiss_tournament.player_by_id(1).ready_to_play)
            self.assertIsNotNone(self.tournament.swiss_tournament.player_by_id(1).ready_until)
            self.assertFalse(self.tournament.swiss_tournament.player_by_id(2).ready_to_play)
            await view.unready_button.callback(interaction)
            self.assertFalse(self.tournament.swiss_tournament.player_by_id(1).ready_to_play)
            self.assertIsNone(self.tournament.swiss_tournament.player_by_id(1).ready_until)
        self.assertEqual(self.tournament.save_tournament_swiss.await_count, 2)
        self.assertFalse(refresh.await_args.kwargs["refresh_standings"])

    async def test_readiness_rejects_nonparticipants_and_dropped_players(self):
        for user_id in (3, 999):
            with self.subTest(user_id=user_id):
                self.tournament.swiss_tournament.player_by_id(3).dropped = True
                _, interaction = self.make_report(reporter_id=user_id)
                await common_views.LeagueView(self.tournament).ready_button.callback(interaction)
                self.tournament.save_tournament_swiss.assert_not_awaited()

    async def test_readiness_rejects_expired_and_cancelled_leagues(self):
        _, interaction = self.make_report()
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        view = common_views.LeagueView(self.tournament)
        self.assertTrue(view.ready_button.disabled)
        self.assertTrue(view.unready_button.disabled)
        await view.ready_button.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()
        self.tournament.end_time = datetime.now(timezone.utc) + timedelta(days=1)
        self.tournament.cancelled = True
        await common_views.LeagueView(self.tournament).ready_button.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()

    async def test_readiness_save_failure_rolls_back_state(self):
        _, interaction = self.make_report()
        self.tournament.save_tournament_swiss.side_effect = RuntimeError("Database unavailable")
        with patch.object(common_views.link_log, "exception"):
            await common_views.LeagueView(self.tournament)._set_ready(
                interaction, True, ready_until=datetime.now(timezone.utc) + timedelta(hours=1)
            )
        self.assertFalse(self.tournament.swiss_tournament.player_by_id(1).ready_to_play)
        self.assertIsNone(self.tournament.swiss_tournament.player_by_id(1).ready_until)

    async def test_readiness_duration_rejects_invalid_values(self):
        view = common_views.LeagueView(self.tournament)
        for value in ("0", "-1", "abc"):
            with self.subTest(value=value):
                _, interaction = self.make_report()
                modal = common_views.ReadyDurationModal(view, 1)
                modal.duration_input = Mock(spec=discord.ui.InputText, value=value)
                await modal.callback(interaction)
                self.tournament.save_tournament_swiss.assert_not_awaited()
                interaction.response.send_message.assert_awaited_once()

    async def test_renewing_readiness_replaces_expired_deadline(self):
        player = self.tournament.swiss_tournament.player_by_id(1)
        player.ready_to_play = True
        player.ready_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        _, interaction = self.make_report()
        view = common_views.LeagueView(self.tournament)
        modal = common_views.ReadyDurationModal(view, 1)
        modal.duration_input = Mock(spec=discord.ui.InputText, value="120")
        with patch.object(common_views, "update_league_message", AsyncMock()) as refresh:
            await modal.callback(interaction)
            await common_views.expire_league_readiness(self.tournament)
        self.assertTrue(player.ready_to_play)
        self.assertGreater(player.ready_until, datetime.now(timezone.utc) + timedelta(minutes=119))
        self.tournament.save_tournament_swiss.assert_awaited_once()
        refresh.assert_awaited_once()

    async def test_readiness_duration_cannot_change_another_players_status(self):
        _, interaction = self.make_report(reporter_id=2)
        modal = common_views.ReadyDurationModal(common_views.LeagueView(self.tournament), 1)
        await modal.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_readiness_duration_is_capped_at_league_end(self):
        self.tournament.end_time = datetime.now(timezone.utc) + timedelta(minutes=10)
        _, interaction = self.make_report()
        modal = common_views.ReadyDurationModal(common_views.LeagueView(self.tournament), 1)
        modal.duration_input = Mock(spec=discord.ui.InputText, value="120")
        with patch.object(common_views, "update_league_message", AsyncMock()):
            await modal.callback(interaction)
        self.assertEqual(self.tournament.swiss_tournament.player_by_id(1).ready_until, self.tournament.end_time)

    async def test_readiness_rejects_deadline_that_expired_while_waiting(self):
        _, interaction = self.make_report()
        await common_views.LeagueView(self.tournament)._set_ready(
            interaction, True, ready_until=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        self.tournament.save_tournament_swiss.assert_not_awaited()
        self.assertIn("abgelaufen", interaction.followup.send.await_args.args[0])

    async def test_readiness_expiry_task_unreadies_player_and_refreshes_message(self):
        player = self.tournament.swiss_tournament.player_by_id(1)
        player.ready_to_play = True
        player.ready_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.tournament.save_tournament_swiss = AsyncMock()
        manager = Mock(spec=cog.SpelltableTournamentManager, bot=self.bot, _tournaments_loaded=True)
        with (
            patch.object(cog, "active_tournaments", {"league": self.tournament}),
            patch.object(common_views, "update_league_message", AsyncMock()) as refresh,
        ):
            await cog.SpelltableTournamentManager.schedule_league_end.coro(manager)
        self.assertFalse(player.ready_to_play)
        self.assertIsNone(player.ready_until)
        self.tournament.save_tournament_swiss.assert_awaited_once()
        refresh.assert_awaited_once_with(self.tournament, refresh_standings=False)

    async def test_readiness_expiry_retries_failed_display_refresh(self):
        player = self.tournament.swiss_tournament.player_by_id(1)
        player.ready_to_play = True
        player.ready_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.tournament.save_tournament_swiss = AsyncMock()
        with patch.object(common_views, "update_league_message", AsyncMock(side_effect=[RuntimeError("Discord unavailable"), None])) as refresh:
            with self.assertRaises(RuntimeError):
                await common_views.expire_league_readiness(self.tournament)
            self.assertTrue(self.tournament._readiness_refresh_pending)
            await common_views.expire_league_readiness(self.tournament)
        self.assertEqual(refresh.await_count, 2)
        self.assertFalse(self.tournament._readiness_refresh_pending)

    async def test_readiness_handles_missing_interaction_user(self):
        _, interaction = self.make_report()
        interaction.user = None
        await common_views.LeagueView(self.tournament).ready_button.callback(interaction)
        self.tournament.save_tournament_swiss.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_large_participant_roster_fits_discord_limits_without_losing_players(self):
        self.tournament.swiss_tournament = swiss_mtg.LeagueTournament(
            [swiss_mtg.Player(100000000000000000 + number) for number in range(409)]
        )
        self.tournament.get_member = AsyncMock(return_value=SimpleNamespace(display_name="Player " + "😀" * 25))
        header = "## Liga abgeschlossen\nGewonnen: <@100000000000000000>"
        pages = await common_views._league_participant_pages(self.tournament, header)
        self.assertGreater(len(pages), 1)
        roster = "\n".join(pages)
        for player in self.tournament.swiss_tournament.players:
            self.assertEqual(roster.count(f"<@{player.player_id}>"), 1)
        for page in pages:
            self.assertLessEqual(len((header + page).encode("utf-16-le")) // 2 + 80, 2000)

    async def test_participant_navigation_preserves_standings_attachment(self):
        _, interaction = self.make_report()
        view = common_views.LeagueView(self.tournament, participant_page=0, participant_page_count=2)
        self.assertTrue(view.previous_participants_button.disabled)
        self.assertFalse(view.next_participants_button.disabled)
        with patch.object(common_views, "update_league_message", AsyncMock()) as refresh:
            await view.next_participants_button.callback(interaction)
        refresh.assert_awaited_once_with(self.tournament, participant_page=1, refresh_standings=False)

    async def test_closed_league_does_not_display_stale_ready_status(self):
        self.tournament.swiss_tournament.player_by_id(1).ready_to_play = True
        self.tournament.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        pages = await common_views._league_participant_pages(self.tournament, "## Liga abgeschlossen")
        self.assertIn("⚪ <@1> [User(1)]", pages[0])

    async def test_roster_names_are_plain_single_line_text(self):
        self.tournament.get_member = AsyncMock(return_value=SimpleNamespace(display_name="**Name**\n@everyone"))
        pages = await common_views._league_participant_pages(self.tournament, "## Liga")
        self.assertEqual(len(pages[0].splitlines()), 3)
        self.assertIn("\\*\\*Name\\*\\*", pages[0])
        self.assertNotIn("@everyone", pages[0])