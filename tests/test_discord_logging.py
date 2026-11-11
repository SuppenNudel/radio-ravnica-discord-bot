import asyncio
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import ezcord.components
import ezcord.logs

from modules import discord_logging


class DiscordLoggingTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"ERROR_NOTIFY_USER_ID": "356120044754698252"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.webhook = SimpleNamespace(send=AsyncMock())
        self.session_patch = patch.object(discord_logging.aiohttp, "ClientSession")
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.webhook_patch = patch.object(
            discord_logging.discord.Webhook, "from_url", return_value=self.webhook
        )
        self.webhook_patch.start()
        self.addCleanup(self.webhook_patch.stop)
        self.user = SimpleNamespace(
            name="Bot", display_avatar=SimpleNamespace(url="https://example.com/avatar.png")
        )

    async def test_only_error_and_critical_logs_ping(self):
        for name in ("ezcord", "link_logger"):
            for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
                with self.subTest(name=name, level=level):
                    self.webhook.send.reset_mock()
                    record = logging.LogRecord(name, level, "", 0, "message", (), None)
                    await discord_logging._send_discord_log(
                        "https://example.com/webhook", record, "```ansi\nmessage```"
                    )
                    self.webhook.send.assert_awaited_once()
                    kwargs = self.webhook.send.await_args.kwargs
                    mentions = kwargs["allowed_mentions"]
                    notify = level >= logging.ERROR
                    self.assertEqual(kwargs["silent"], not notify)
                    self.assertFalse(mentions.roles)
                    self.assertFalse(mentions.everyone)
                    self.assertFalse(mentions.replied_user)
                    if notify:
                        self.assertEqual(
                            kwargs["content"],
                            "<@356120044754698252>\n```ansi\nmessage```",
                        )
                        self.assertEqual([user.id for user in mentions.users], [356120044754698252])
                    else:
                        self.assertEqual(kwargs["content"], "```ansi\nmessage```")
                        self.assertFalse(mentions.users)

    async def test_local_only_log_is_not_sent_to_discord(self):
        record = logging.LogRecord("youtube_feed", logging.WARNING, "", 0, "message", (), None)
        record.skip_discord = True

        await discord_logging._send_discord_log(
            "https://example.com/webhook", record, "message"
        )

        self.webhook.send.assert_not_awaited()

    async def test_notification_user_comes_from_environment(self):
        with patch.dict(os.environ, {"ERROR_NOTIFY_USER_ID": "123456789012345678"}):
            await discord_logging._send_webhook("https://example.com/webhook", notify=True)
        kwargs = self.webhook.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "<@123456789012345678>")
        self.assertEqual(
            [user.id for user in kwargs["allowed_mentions"].users], [123456789012345678]
        )

    def test_invalid_or_missing_notification_user_fails_at_startup(self):
        for value in ("", "invalid", "-1", "0"):
            with self.subTest(value=value), \
                 patch.dict(os.environ, {"ERROR_NOTIFY_USER_ID": value}):
                with self.assertRaisesRegex(ValueError, "ERROR_NOTIFY_USER_ID"):
                    discord_logging.install_discord_log_notifications()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ERROR_NOTIFY_USER_ID"):
                discord_logging.install_discord_log_notifications()

    async def test_long_error_log_pings_once_and_preserves_content(self):
        text = "```" + "x" * 4000 + "```"
        record = logging.LogRecord("ezcord", logging.ERROR, "", 0, "", (), None)
        await discord_logging._send_discord_log("https://example.com/webhook", record, text)
        calls = self.webhook.send.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            "".join(call.kwargs["content"] for call in calls),
            "<@356120044754698252>\n" + text,
        )
        self.assertFalse(calls[0].kwargs["silent"])
        for call in calls[1:]:
            self.assertTrue(call.kwargs["silent"])
        self.assertTrue(all(len(call.kwargs["content"]) <= 2000 for call in calls))

    async def test_ezcord_handler_keeps_mention_outside_codeblock(self):
        logger = logging.Logger("notification-test", logging.DEBUG)
        with patch.object(ezcord.logs.logging, "getLogger", return_value=logger), \
             patch.object(ezcord.logs, "_send_discord_log", discord_logging._send_discord_log):
            ezcord.set_log(
                "notification-test",
                console=False,
                webhook_url="https://example.com/webhook",
                dc_codeblocks=True,
                discord_log_level=logging.INFO,
            )
            logger.error("actual formatted log")
            await asyncio.sleep(0)

        self.webhook.send.assert_awaited_once()
        content = self.webhook.send.await_args.kwargs["content"]
        self.assertTrue(content.startswith("<@356120044754698252>\n```ansi\n"))
        self.assertIn("actual formatted log", content)

    async def test_bot_error_report_pings(self):
        bot = SimpleNamespace(error_webhook_url="https://example.com/webhook", user=self.user)
        result = await discord_logging.NotificationBot._send_error_webhook(bot, "traceback")
        self.assertTrue(result)
        kwargs = self.webhook.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "<@356120044754698252>")
        self.assertEqual(kwargs["embed"].description, "traceback")
        self.assertFalse(kwargs["silent"])

    async def test_component_error_report_pings(self):
        interaction = SimpleNamespace(client=SimpleNamespace(user=self.user))
        with patch.dict(os.environ, {"ERROR_WEBHOOK_URL": "https://example.com/webhook"}):
            result = await discord_logging._send_component_error(interaction, "component error")
        self.assertTrue(result)
        kwargs = self.webhook.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "<@356120044754698252>")
        self.assertEqual(kwargs["embed"].description, "component error")

    async def test_component_without_error_webhook_does_not_send(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await discord_logging._send_component_error(MagicMock(), "error")
        self.assertFalse(result)
        self.webhook.send.assert_not_awaited()

    async def test_failed_webhook_logs_without_recursive_discord_send(self):
        self.webhook.send.side_effect = discord.HTTPException(
            SimpleNamespace(status=500, reason="Internal Server Error"), "failed"
        )
        with patch.object(discord_logging.ezcord, "log") as log:
            result = await discord_logging._send_webhook("https://example.com/webhook", notify=True)
        self.assertFalse(result)
        log.exception.assert_called_once()
        self.assertEqual(log.exception.call_args.kwargs["extra"], {"webhook_sent": True})

    def test_install_wires_both_ezcord_senders(self):
        with patch.object(ezcord.logs, "_send_discord_log"), \
             patch.object(ezcord.components, "_send_error_webhook"):
            discord_logging.install_discord_log_notifications()
            self.assertIs(ezcord.logs._send_discord_log, discord_logging._send_discord_log)
            self.assertIs(ezcord.components._send_error_webhook, discord_logging._send_component_error)


if __name__ == "__main__":
    unittest.main()
