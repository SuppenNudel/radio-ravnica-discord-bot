import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

with patch.dict(os.environ, {"DEBUG": "false"}):
    from modules.spelltable import tournament_model as model


class TournamentOverviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.image = Path(directory.name) / "calendar.png"
        self.image.write_bytes(b"calendar image")
        self.message = SimpleNamespace(
            id=3,
            content="overview",
            attachments=[SimpleNamespace(read=AsyncMock(return_value=b"calendar image"))],
            jump_url="https://discord.com/channels/1/2/3",
            edit=AsyncMock(),
        )
        self.channel = SimpleNamespace(
            fetch_message=AsyncMock(return_value=self.message),
            send=AsyncMock(return_value=self.message),
        )
        self.guild = SimpleNamespace(id=1, get_channel=Mock(return_value=self.channel))
        self.bot = SimpleNamespace(
            get_guild=Mock(return_value=self.guild), change_presence=AsyncMock()
        )
        for name, value in (
            ("GUILD_ID", 1),
            ("SPELLTABLE_CALENDAR_CHANNEL_ID", 2),
            ("SPELLTABLE_CALENDAR_MESSAGE_ID", 3),
        ):
            self.enterContext(patch.object(model.env, name, value))
        self.save_env = self.enterContext(patch.object(model.env, "save_to_env"))
        self.enterContext(patch.object(model, "active_tournaments", {}))
        self.enterContext(
            patch.object(model, "generate_tournament_message", AsyncMock(return_value="overview"))
        )
        self.generate_calendar = self.enterContext(
            patch.object(model, "generate_calendar", return_value=str(self.image))
        )
        self.formats = self.enterContext(
            patch.object(model, "update_format_overviews", AsyncMock())
        )

    def http_error(self, code, status=429):
        return discord.HTTPException(
            SimpleNamespace(status=status, reason="Test error"),
            {"code": code, "message": "Test error"},
        )

    async def test_unchanged_calendar_does_not_edit(self):
        await model.update_tournament_message(self.bot)
        self.message.edit.assert_not_awaited()
        self.bot.change_presence.assert_awaited_once()
        self.formats.assert_awaited_once()

    async def test_content_only_change_preserves_attachment(self):
        self.message.content = "old overview"
        await model.update_tournament_message(self.bot)
        kwargs = self.message.edit.await_args.kwargs
        self.assertEqual(kwargs["content"], "overview")
        self.assertNotIn("file", kwargs)
        self.assertNotIn("attachments", kwargs)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_changed_image_is_uploaded_and_closed(self):
        self.message.attachments[0].read.return_value = b"old image"

        async def check_upload(**kwargs):
            self.assertEqual(kwargs["file"].fp.read(), b"calendar image")

        self.message.edit.side_effect = check_upload
        await model.update_tournament_message(self.bot)
        kwargs = self.message.edit.await_args.kwargs
        self.assertEqual(kwargs["attachments"], [])
        self.assertTrue(kwargs["file"].fp.closed)

    async def test_missing_or_extra_attachments_are_repaired(self):
        for attachments in ([], self.message.attachments * 2):
            with self.subTest(attachment_count=len(attachments)):
                self.message.attachments = attachments
                self.message.edit.reset_mock()
                await model.update_tournament_message(self.bot)
                self.message.edit.assert_awaited_once()
                self.assertEqual(self.message.edit.await_args.kwargs["attachments"], [])

    async def test_edit_limit_is_logged_without_interrupting_other_updates(self):
        self.message.attachments = []
        self.message.edit.side_effect = self.http_error(30046)
        with patch.object(model.log, "warning") as warning:
            await model.update_tournament_message(self.bot)
        self.assertIn("30046", warning.call_args.args[0])
        self.assertTrue(self.message.edit.await_args.kwargs["file"].fp.closed)
        self.formats.assert_awaited_once()
        self.bot.change_presence.assert_awaited_once()
        self.channel.send.assert_not_awaited()
        self.save_env.assert_not_called()
        self.message.edit.side_effect = None
        await model.update_tournament_message(self.bot)
        self.assertEqual(self.message.edit.await_count, 2)

    async def test_other_http_errors_propagate_and_upload_is_closed(self):
        self.message.attachments = []
        self.message.edit.side_effect = self.http_error(0, status=500)
        with self.assertRaises(discord.HTTPException):
            await model.update_tournament_message(self.bot)
        self.assertTrue(self.message.edit.await_args.kwargs["file"].fp.closed)

    async def test_obsolete_attachment_is_removed(self):
        self.generate_calendar.return_value = None
        await model.update_tournament_message(self.bot)
        self.assertEqual(self.message.edit.await_args.kwargs["attachments"], [])

    async def test_unchanged_calendar_without_image_does_not_edit(self):
        self.generate_calendar.return_value = None
        self.message.attachments = []
        await model.update_tournament_message(self.bot)
        self.message.edit.assert_not_awaited()

    async def test_missing_message_is_created_and_id_persisted(self):
        self.channel.fetch_message.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10008, "message": "Unknown Message"},
        )
        await model.update_tournament_message(self.bot)
        self.channel.send.assert_awaited_once()
        self.assertTrue(self.channel.send.await_args.kwargs["file"].fp.closed)
        self.save_env.assert_called_once_with("SPELLTABLE_CALENDAR_MESSAGE_ID", 3)

    async def test_other_guild_does_not_update_calendar(self):
        await model.update_tournament_message(self.bot, SimpleNamespace(id=9))
        self.channel.fetch_message.assert_not_awaited()
        self.formats.assert_not_awaited()


class FormatOverviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = SimpleNamespace(id=3, content="overview", edit=AsyncMock())
        self.channel = SimpleNamespace(fetch_message=AsyncMock(return_value=self.message))
        guild = SimpleNamespace(id=1, get_channel=Mock(return_value=self.channel))
        self.bot = SimpleNamespace(get_guild=Mock(return_value=guild))
        self.enterContext(patch.object(model.env, "GUILD_ID", 1))
        self.enterContext(
            patch.object(
                model, "active_tournaments",
                {"test": SimpleNamespace(guild=guild, format_name="Modern")},
            )
        )
        self.enterContext(
            patch.object(model, "_load_format_message_ids", return_value={"messages": {"Modern": ["3"]}})
        )
        self.enterContext(patch.object(model, "_save_format_message_ids"))
        self.enterContext(
            patch.object(model, "generate_format_overview_messages", AsyncMock(return_value=["overview"]))
        )

    async def test_unchanged_format_overview_does_not_edit(self):
        await model.update_format_overviews(self.bot)
        self.message.edit.assert_not_awaited()

    async def test_changed_format_overview_is_edited(self):
        self.message.content = "old overview"
        await model.update_format_overviews(self.bot)
        self.message.edit.assert_awaited_once()
        self.assertEqual(self.message.edit.await_args.kwargs["content"], "overview")


if __name__ == "__main__":
    unittest.main()
