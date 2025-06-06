import os
import discord
import ezcord
import logging
from ezcord import log, Bot
import platform
from modules import env

LOG_WEBHOOK = os.getenv("LOG_WEBHOOK")
IS_DEBUG = bool(os.getenv("DEBUG"))

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
    dc_codeblocks=False
)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot:Bot = Bot(
    intents=intents,
    error_webhook_url=os.getenv("ERROR_WEBHOOK"),
    language="de",
    ready_event=None,
    debug_guilds=[os.getenv("GUILD")]
)
# bot.add_help_command()

@bot.event
async def on_ready():
    infos = {
        "Python": platform.python_version(),
        "Emojis": len(bot.emojis),
        "Guild": [os.getenv("GUILD")]
    }
    bot.ready(
        new_info=infos,
        style=ezcord.ReadyEvent.table_vertical
    )

if __name__ == "__main__":
    os.makedirs("tmp", exist_ok=True)
    if IS_DEBUG:
        bot.load_extension('cogs.spelltable.spelltable_tournament')
    else:
        bot.load_cogs(subdirectories=True, ignored_cogs=["ping", "hack", "notion_to_forum"])
    bot.add_status_changer(
        "Puzzelt mit Blacky",
    #     discord.Game("plays with you"),
    #     discord.Activity(type=discord.ActivityType.watching, name="you"),
    #     interval=5,
    #     shuffle=True
    )
    bot.run(os.getenv("TOKEN"))
