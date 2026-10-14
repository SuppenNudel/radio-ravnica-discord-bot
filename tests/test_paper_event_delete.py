import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

with patch.dict(os.environ, {"GUILD": "1"}):
    from cogs.paper_events import paper_events_status_monitor as monitor
    from modules import paper_events_common as common, paper_events_db


class PaperEventDeleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "events.db"
        self.connect = paper_events_db.connect
        with closing(self.connect(self.db_path)) as conn:
            paper_events_db.init_db(conn)
            for thread_id in (123, 456):
                paper_events_db.upsert_event(conn, {
                    "server_id": "1",
                    "thread_id": str(thread_id),
                    "title": "Weekly event",
                    "start_at": "2099-01-01T18:00:00",
                    "is_recurring": 1,
                    "recurrence_rule": "FREQ=WEEKLY;INTERVAL=1",
                })
        self.author = SimpleNamespace(id=42, roles=[])
        self.thread = SimpleNamespace(id=123, delete=AsyncMock())
        self.event = common.PaperEvent(SimpleNamespace(id=1), self.author)
        self.event.thread = self.thread
        self.interaction = SimpleNamespace(
            user=self.author,
            response=SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()
            ),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
            client=SimpleNamespace(),
        )
        self.db_patch = patch.object(paper_events_db, "connect", side_effect=lambda: self.connect(self.db_path))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.calendar_patch = patch.object(common, "update_paper_events_calendar", new_callable=AsyncMock)
        self.calendar = self.calendar_patch.start()
        self.addCleanup(self.calendar_patch.stop)
        self.addCleanup(monitor.cancel_event_timers, 123)

    def remaining_threads(self):
        with closing(self.connect(self.db_path)) as conn:
            return [row["thread_id"] for row in conn.execute("SELECT thread_id FROM paper_events ORDER BY thread_id")]

    async def confirm(self):
        view = common.DeleteEventView(self.event)
        await view.confirm_callback.callback(self.interaction)
        return view

    async def test_delete_button_only_for_published_events(self):
        view = common.EditTourneyView(self.event)
        self.assertIn("Event löschen", [button.label for button in view.children])
        self.event.thread = None
        draft_view = common.EditTourneyView(self.event)
        self.assertNotIn("Event löschen", [button.label for button in draft_view.children])

    async def test_delete_requires_confirmation(self):
        view = common.EditTourneyView(self.event)
        await view.delete_callback(self.interaction)
        confirmation = self.interaction.response.edit_message.await_args.kwargs["view"]
        self.assertIsInstance(confirmation, common.DeleteEventView)
        self.assertEqual(self.remaining_threads(), ["123", "456"])
        self.thread.delete.assert_not_awaited()

    async def test_confirm_removes_row_cancels_timers_and_updates_calendar(self):
        pending = Mock()
        pending.done.return_value = False
        monitor._active_timers[123] = {"archive": pending, "delete": pending}

        async def check_removed_before_discord_delete():
            self.assertEqual(self.remaining_threads(), ["456"])
            self.assertNotIn(123, monitor._active_timers)

        self.thread.delete.side_effect = check_removed_before_discord_delete
        view = await self.confirm()
        self.thread.delete.assert_awaited_once()
        self.assertTrue(pending.cancel.called)
        self.assertTrue(view.is_finished())
        self.calendar.assert_awaited_once_with(self.interaction.client, self.event.guild)
        self.interaction.edit_original_response.assert_not_awaited()
        self.interaction.followup.send.assert_awaited_once_with(
            "Event gelöscht. Es werden keine weiteren Wiederholungen mehr erstellt.", ephemeral=True
        )

        cog = monitor.PaperEventsStatusMonitor(self.interaction.client)
        cog.guild = self.event.guild
        with patch.object(common.PaperEvent, "create_and_send_thread", new_callable=AsyncMock) as create:
            await cog._create_next_recurring_event(123)
        create.assert_not_awaited()

    async def test_other_users_cannot_delete(self):
        self.interaction.user = SimpleNamespace(id=99, roles=[])
        await self.confirm()
        self.assertEqual(self.remaining_threads(), ["123", "456"])
        self.thread.delete.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()

    async def test_moderators_can_delete(self):
        self.interaction.user = SimpleNamespace(id=99, roles=[SimpleNamespace(name="Moderator")])
        await self.confirm()
        self.assertEqual(self.remaining_threads(), ["456"])

    async def test_cancel_returns_to_edit_without_deleting(self):
        view = common.DeleteEventView(self.event)
        with patch.object(self.event, "construct_event_embed", return_value=discord.Embed()), \
             patch.object(self.event, "construct_gmaps_embed", return_value=None), \
             patch.object(self.event, "construct_content", return_value="Preview"), \
             patch.object(self.event, "get_files", return_value=[]):
            await view.cancel_callback.callback(self.interaction)
        self.assertEqual(self.remaining_threads(), ["123", "456"])
        self.thread.delete.assert_not_awaited()
        self.assertIsInstance(self.interaction.response.edit_message.await_args.kwargs["view"], common.EditTourneyView)

    async def test_missing_thread_still_deletes_saved_event(self):
        self.thread.delete.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Gone")
        await self.confirm()
        self.assertEqual(self.remaining_threads(), ["456"])
        self.interaction.edit_original_response.assert_not_awaited()
        self.interaction.followup.send.assert_awaited_once_with(
            "Event gelöscht. Es werden keine weiteren Wiederholungen mehr erstellt.", ephemeral=True
        )
        self.calendar.assert_awaited_once()

    async def test_discord_failure_still_stops_recurrence(self):
        self.thread.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Denied")
        await self.confirm()
        self.assertEqual(self.remaining_threads(), ["456"])
        self.assertIn("manuell", self.interaction.edit_original_response.await_args.kwargs["content"])
        self.calendar.assert_awaited_once()

    async def test_calendar_failure_does_not_undo_deletion(self):
        self.calendar.side_effect = RuntimeError("Calendar unavailable")
        await self.confirm()
        self.assertEqual(self.remaining_threads(), ["456"])
        self.thread.delete.assert_awaited_once()
        self.assertEqual(self.interaction.followup.send.await_count, 2)
        self.assertIn("Event gelöscht", self.interaction.followup.send.await_args_list[0].args[0])
        self.assertIn("Kalender", self.interaction.followup.send.await_args_list[1].args[0])

    async def test_archived_predecessor_cannot_recreate_deleted_occurrence(self):
        cog = monitor.PaperEventsStatusMonitor(self.interaction.client)
        thread = Mock(spec=discord.Thread)
        thread.archived = True
        cog.guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=thread))
        with patch.object(cog, "_create_next_recurring_event", new_callable=AsyncMock) as create:
            await cog._archive_thread(123)
        create.assert_not_awaited()

    async def test_missing_post_cannot_generate_next_occurrence(self):
        cog = monitor.PaperEventsStatusMonitor(self.interaction.client)
        thread = Mock(spec=discord.Thread)
        thread.id = 123
        thread.archived = False
        thread.fetch_message = AsyncMock(side_effect=discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Gone"
        ))
        cog.guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=thread))
        with patch.object(cog, "_create_next_recurring_event", new_callable=AsyncMock) as create:
            await cog._archive_thread(123)
        create.assert_not_awaited()
        self.assertEqual(self.remaining_threads(), ["456"])

    async def test_live_post_still_generates_next_occurrence_before_archiving(self):
        cog = monitor.PaperEventsStatusMonitor(self.interaction.client)
        thread = Mock(spec=discord.Thread)
        thread.id = 123
        thread.archived = False
        thread.fetch_message = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
        thread.edit = AsyncMock()
        cog.guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=thread))
        with patch.object(cog, "_create_next_recurring_event", new_callable=AsyncMock) as create:
            await cog._archive_thread(123)
        create.assert_awaited_once_with(123)
        thread.edit.assert_awaited_once_with(archived=True)


if __name__ == "__main__":
    unittest.main()