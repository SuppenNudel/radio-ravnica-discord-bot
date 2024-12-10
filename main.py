import os
import discord
import ezcord
from dotenv import load_dotenv
import logging

ezcord.set_log(
    log_level=logging.DEBUG,
    discord_log_level=logging.INFO,
    webhook_url=os.getenv("LOG_WEBHOOK"),
    dc_codeblocks=True
)

ezcord.set_log(
    "link_logger",
    log_level=logging.DEBUG,
    discord_log_level=logging.INFO,
    webhook_url=os.getenv("LOG_WEBHOOK"),
    dc_codeblocks=False
)

intents = discord.Intents.default()
intents.members = True

bot = ezcord.Bot(
    intents=intents,
    error_webhook_url=os.getenv("ERROR_WEBHOOK"),
    language="de",
    ready_event=None,
)
bot.add_help_command()

@bot.event
async def on_ready():
    infos = {
        "Emojis": len(bot.emojis)
    }
    bot.ready(new_info=infos)

if __name__ == "__main__":
    load_dotenv()
    bot.load_cogs()
    bot.run(os.getenv("TOKEN"))
