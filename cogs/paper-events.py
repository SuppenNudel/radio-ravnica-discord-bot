from discord.ext import commands, tasks
import discord
from ezcord import Bot
from discord import Client
from ezcord import log
import modules.notion as notion
from datetime import datetime, timedelta
import os
import re
import logging

log_link = logging.getLogger("link_logger")

def get_bool_from_env(key: str, default: bool = False) -> bool:
    value = os.getenv(key, str(default)).lower()  # Default to 'false' if key is not found
    return value in ("true", "1", "yes", "on")

GUILD_ID = int(os.getenv("GUILD"))
CHANNEL_PAPER_EVENTS_ID = os.getenv("CHANNEL_PAPER_EVENTS")
DEBUG = get_bool_from_env("DEBUG")
DB_PAPER_EVENTS_ID = "f05d532cf91f4f9cbce38e27dc85b522"

class PaperEvents(commands.Cog):
    def __init__(self, bot:Client):
        self.bot:Client = bot

    @commands.Cog.listener()
    async def on_ready(self):
        log.info("PaperEvents Cog started")
        self.guild = self.bot.get_guild(GUILD_ID)
        if self.guild:
            self.check.start()
        else:
            log.error("Guild not found")

    @tasks.loop(hours=6)
    async def check(self):
        if self.guild == None:
            return
        # check if there are events that have passed
        # close corresponding threads
        # delete even older threads
        entries = notion.get_entries(
            database_id=DB_PAPER_EVENTS_ID,
            filter = {
                "and": [
                    {
                        "property": "Link",
                        "url": {
                            "is_not_empty": True
                        }
                    },
                    {
                        "property": "For Test",
                        "checkbox": {
                            "equals": DEBUG
                        }
                    }
                ]
            }
        )
        for entry in entries['results']:
            properties = entry['properties']
            event_date = properties['Start (und Ende)']['date']
            event_start = datetime.fromisoformat(event_date['start'])
            event_end = None
            if event_date['end']:
                event_end = datetime.fromisoformat(event_date['end'])
            event_discord_channel_id = properties['Thread ID'][properties['Thread ID']['type']][0]['plain_text']
            event_title = properties['Event Titel']['title'][0]['plain_text']
            now = datetime.now(tz=event_start.tzinfo)
            if not event_discord_channel_id:
                log.error(f"Event {event_title} has no Channel ID")
                continue
            thread_id = int(event_discord_channel_id)
            try:
                thread = await self.guild.fetch_channel(thread_id)
            except discord.errors.NotFound:
                # Event Post already deleted
                # log.debug(f"Thread not found: {thread_id}")
                continue
            if not isinstance(thread, discord.Thread):
                log.error(f"Not a Thread")
                continue

            date_to_compare = event_start
            if event_end:
                date_to_compare = event_end

            # geschlossen = archived
            # sperren = locked
            is_archived = thread.archived
            is_passed = date_to_compare < now
            is_way_passed = date_to_compare < now - timedelta(days=30)

            if is_way_passed:
                log.info(f"Event {thread.name} is way passed, going to delete")
                await thread.delete()
            else:
                if (is_passed and not is_archived) or (not is_passed and is_archived):
                    try:
                        await thread.edit(archived=is_passed)
                        log.info(f"Changed thread archive state to: {is_passed}")
                    except discord.Forbidden:
                        log.info(f"Missing permissions to edit thread: {thread.name}")
                    except discord.HTTPException as e:
                        log.info(f"Failed to edit thread {thread.name}: {e}")

def setup(bot):
    bot.add_cog(PaperEvents(bot))
