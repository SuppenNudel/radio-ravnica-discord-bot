import logging
import os

import aiohttp
import discord
import ezcord
import ezcord.components
import ezcord.logs


def _get_error_notify_user_id() -> int:
    value = os.getenv("ERROR_NOTIFY_USER_ID", "")
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("ERROR_NOTIFY_USER_ID must be set to a positive Discord user ID")
    return int(value)


async def _send_webhook(webhook_url, *, notify, **kwargs):
    user_id = _get_error_notify_user_id() if notify else None
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        content = kwargs.pop("content", "") or ""
        if notify:
            content = f"<@{user_id}>\n{content}".rstrip()
        allowed_mentions = discord.AllowedMentions(
            users=[discord.Object(id=user_id)] if user_id is not None else False,
            roles=False,
            everyone=False,
            replied_user=False,
        )
        try:
            # Keep the mention outside log code blocks and within Discord's content limit.
            for offset in range(0, max(len(content), 1), 2000):
                await webhook.send(
                    content=content[offset:offset + 2000] or None,
                    allowed_mentions=allowed_mentions,
                    silent=not notify or offset > 0,
                    **kwargs,
                )
        except discord.HTTPException:
            ezcord.log.exception(
                "Failed to send Discord log/error report webhook",
                extra={"webhook_sent": True},
            )
            return False
    return True


async def _send_discord_log(webhook_url, record, msg):
    if getattr(record, "skip_discord", False):
        return

    name = "EzCord" if record.name == ezcord.logs.DEFAULT_LOG else record.name
    await _send_webhook(
        webhook_url,
        notify=record.levelno >= logging.ERROR,
        content=msg,
        username=f"{name} Log",
    )


async def _send_error_report(webhook_url, user, description):
    return await _send_webhook(
        webhook_url,
        notify=True,
        embed=discord.Embed(
            title="Error Report",
            description=description,
            color=discord.Color.red(),
        ),
        username=f"{user.name} Error Report",
        avatar_url=user.display_avatar.url,
    )


async def _send_component_error(interaction, description):
    webhook_url = os.getenv("ERROR_WEBHOOK_URL")
    if not webhook_url:
        return False
    return await _send_error_report(webhook_url, interaction.client.user, description)


class NotificationBot(ezcord.Bot):
    async def _send_error_webhook(self, description):
        return await _send_error_report(self.error_webhook_url, self.user, description)


def install_discord_log_notifications():
    _get_error_notify_user_id()
    # EzCord has no public send hook for log or component error webhook mentions.
    ezcord.logs._send_discord_log = _send_discord_log
    ezcord.components._send_error_webhook = _send_component_error
