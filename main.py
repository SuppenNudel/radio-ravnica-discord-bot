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
        bot.load_extension('cogs.feed.youtube')
        bot.load_extension('cogs.feed.instagram')
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
