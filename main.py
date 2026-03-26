import platform
import sys

def get_system_info():
    system = platform.system()
    machine = platform.machine()
    architecture = platform.architecture()[0]

    print(f"System: {system}")
    print(f"Machine: {machine}")
    print(f"Architecture: {architecture}")
    print(f"Python version: {sys.version}")

get_system_info()

import os
import discord
import ezcord
import logging
from ezcord import log, Bot
import platform
from modules import env
from modules.db import init_db

LOG_WEBHOOK = env.LOG_WEBHOOK
IS_DEBUG = env.DEBUG

ezcord.set_log(
    log_level=logging.DEBUG,
    discord_log_level=logging.INFO,
    webhook_url=LOG_WEBHOOK,
    dc_codeblocks=True
)

ezcord.set_log(
    "link_logger",
    log_level=logging.DEBUG,
    discord_log_level=logging.INFO,
    webhook_url=LOG_WEBHOOK,
    dc_codeblocks=False,
    log_format="`%(levelname)s` %(message)s"
)


def install_safe_message_edit_patch() -> None:
    if getattr(discord.Message.edit, "_rr_safe_patch", False):
        return

    original_edit = discord.Message.edit
    link_log = logging.getLogger("link_logger")

    async def safe_edit(self, *args, **kwargs):
        bot_user_id = getattr(getattr(self, "_state", None), "self_id", None)
        author_id = getattr(getattr(self, "author", None), "id", None)

        if bot_user_id is not None and author_id is not None and author_id != bot_user_id:
            link_log.debug(
                "Skipping message edit for message_id=%s in channel_id=%s: message authored by user_id=%s, bot_user_id=%s",
                getattr(self, "id", "unknown"),
                getattr(getattr(self, "channel", None), "id", "unknown"),
                author_id,
                bot_user_id,
            )
            return self

        try:
            return await original_edit(self, *args, **kwargs)
        except discord.errors.Forbidden as error:
            link_log.debug(
                "Skipping message edit due to forbidden for message_id=%s in channel_id=%s: %s",
                getattr(self, "id", "unknown"),
                getattr(getattr(self, "channel", None), "id", "unknown"),
                error,
            )
            return self

    safe_edit._rr_safe_patch = True
    discord.Message.edit = safe_edit


install_safe_message_edit_patch()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

debug_guilds = [env.GUILD_ID, os.getenv("DEBUG_GUILD2_ID")] if IS_DEBUG else []

bot:Bot = Bot(
    intents=intents,
    error_webhook_url=os.getenv("ERROR_WEBHOOK"),
    language="de",
    ready_event=None,
    debug_guilds=debug_guilds
)
# bot.add_help_command()

@bot.event
async def on_ready():
    infos = {
        "Python": platform.python_version(),
        "Emojis": len(bot.emojis)
    }
    bot.ready(
        new_info=infos,
        style=ezcord.ReadyEvent.table_vertical
    )

if __name__ == "__main__":
    os.makedirs("tmp", exist_ok=True)
    init_db()
    profile = env.get_active_cog_profile()
    bot.load_cogs(profile["cogs_path"], ignored_cogs=profile["ignored_cogs"], subdirectories=True)
    bot.run(os.getenv("TOKEN"))
