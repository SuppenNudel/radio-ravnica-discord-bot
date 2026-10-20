import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

with patch.dict(os.environ, {"DEBUG": "false"}):
    from cogs.spelltable import spelltable_tournament as cog


class HistoricalPairingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image = Path(self.directory.name) / "pairings.png"
        self.image.write_bytes(b"current image")
        self.rounds = [
            SimpleNamespace(round_number=number, message_id_pairings=number, matches=[])
            for number in (1, 2, 3)
        ]
        self.messages = {
            number: SimpleNamespace(
                attachments=[SimpleNamespace(read=AsyncMock(return_value=b"current image"))],
                components=[],
                channel=SimpleNamespace(),
                jump_url=f"https://discord.com/channels/1/2/{number}",
                edit=AsyncMock(),
            )
            for number in (1, 2, 3)
        }
        self.manager = Mock(spec=cog.SpelltableTournamentManager)
        self.tournament = Mock(
            spec=cog.SpelltableTournament,
            title="Test tournament",
            swiss_tournament=SimpleNamespace(rounds=self.rounds),
            get_message=AsyncMock(side_effect=lambda message_id: self.messages.get(message_id)),
            pairings_to_image=AsyncMock(return_value=str(self.image)),
            get_member=AsyncMock(),
        )

    async def refresh(self, current_round: int | None = 3, include_current_round=False):
        await cog.SpelltableTournamentManager._refresh_historical_pairings_messages(
            self.manager, self.tournament, current_round, include_current_round
        )

    def http_error(self, status, code):
        return discord.HTTPException(
            SimpleNamespace(status=status, reason="Test error"),
            {"code": code, "message": "Test error"},
        )

    async def test_unchanged_history_does_not_edit(self):
        await self.refresh()
        for message in self.messages.values():
            message.edit.assert_not_awaited()
        self.assertEqual(self.tournament.get_message.await_count, 2)

    async def test_changed_image_is_replaced_and_upload_closed(self):
        self.messages[1].attachments[0].read.return_value = b"old image"

        async def check_upload(**kwargs):
            self.assertEqual(kwargs["file"].fp.read(), b"current image")

        self.messages[1].edit.side_effect = check_upload
        await self.refresh()
        kwargs = self.messages[1].edit.await_args.kwargs
        self.assertEqual(kwargs["attachments"], [])
        self.assertIsNone(kwargs["view"])
        self.assertTrue(kwargs["file"].fp.closed)
        self.messages[2].edit.assert_not_awaited()

    async def test_unchanged_image_only_removes_stale_buttons(self):
        self.messages[1].components = [object()]
        await self.refresh()
        self.messages[1].edit.assert_awaited_once_with(view=None)

    async def test_missing_or_extra_attachments_are_repaired(self):
        self.messages[1].attachments = []
        self.messages[2].attachments.append(SimpleNamespace(read=AsyncMock()))
        await self.refresh()
        self.messages[1].edit.assert_awaited_once()
        self.messages[2].edit.assert_awaited_once()

    async def test_edit_limit_stops_remaining_history_and_closes_upload(self):
        self.messages[1].attachments = []
        self.messages[1].edit.side_effect = self.http_error(429, 30046)
        with patch.object(cog.log, "warning") as warning:
            await self.refresh()
        self.assertEqual(self.tournament.get_message.await_count, 1)
        self.messages[2].edit.assert_not_awaited()
        warning.assert_called_once()
        self.assertIn("30046", warning.call_args.args[0])
        self.assertTrue(self.messages[1].edit.await_args.kwargs["file"].fp.closed)

    async def test_other_http_errors_are_logged_and_do_not_stop_history(self):
        self.messages[1].attachments = []
        self.messages[2].attachments = []
        self.messages[1].edit.side_effect = self.http_error(500, 0)
        with patch.object(cog.log, "warning") as warning:
            await self.refresh()
        warning.assert_called_once()
        self.messages[2].edit.assert_awaited_once()

    async def test_attachment_download_errors_are_logged(self):
        self.messages[1].attachments[0].read.side_effect = self.http_error(404, 10008)
        with patch.object(cog.log, "warning") as warning:
            await self.refresh()
        warning.assert_called_once()
        self.messages[1].edit.assert_not_awaited()
        self.assertEqual(self.tournament.get_message.await_count, 2)

    async def test_missing_message_does_not_stop_history(self):
        del self.messages[1]
        await self.refresh()
        self.assertEqual(self.tournament.get_message.await_count, 2)

    async def test_concluded_tournament_includes_current_round(self):
        self.messages[3].attachments = []
        await self.refresh(include_current_round=True)
        self.messages[3].edit.assert_awaited_once()

    async def test_no_current_round_checks_all_history(self):
        await self.refresh(current_round=None)
        self.assertEqual(self.tournament.get_message.await_count, 3)

    async def test_archived_unchanged_message_does_not_unarchive_thread(self):
        thread = Mock(spec=discord.Thread)
        thread.archived = True
        thread.edit = AsyncMock()
        self.messages[1].channel = thread
        await self.refresh()
        thread.edit.assert_not_awaited()

    async def test_archived_changed_message_unarchives_before_edit(self):
        thread = Mock(spec=discord.Thread)
        thread.archived = True
        thread.edit = AsyncMock()
        self.messages[1].channel = thread
        self.messages[1].attachments = []
        await self.refresh()
        thread.edit.assert_awaited_once_with(archived=False)
        self.messages[1].edit.assert_awaited_once()

    async def test_unstarted_tournament_is_ignored(self):
        self.tournament.swiss_tournament = None
        await self.refresh()
        self.tournament.get_message.assert_not_awaited()

    async def test_permissions_error_is_logged_and_remaining_rounds_continue(self):
        self.messages[1].attachments = []
        self.messages[1].edit.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing permissions"
        )
        with patch.object(cog.log, "warning") as warning:
            await self.refresh()
        warning.assert_called_once()
        self.assertIn("missing permissions", warning.call_args.args[0])
        self.assertEqual(self.tournament.get_message.await_count, 2)

    async def test_player_cache_is_populated_once_per_player(self):
        player1 = SimpleNamespace(player_id=10)
        player2 = SimpleNamespace(player_id=20)
        for round_obj in self.rounds:
            round_obj.matches = [
                SimpleNamespace(player1=player1, player2=player2),
                SimpleNamespace(player1=player1, player2=None),
            ]
        await self.refresh()
        self.assertEqual(self.tournament.get_member.await_count, 2)
        self.assertEqual(
            {call.args[0] for call in self.tournament.get_member.await_args_list},
            {10, 20},
        )


if __name__ == "__main__":
    unittest.main()
