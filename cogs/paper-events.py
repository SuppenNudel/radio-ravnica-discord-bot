from discord.ext import commands, tasks
import discord
from discord import Bot
from ezcord import log
import modules.notion as notion
from datetime import datetime, timedelta
import os
import logging

log_link = logging.getLogger("link_logger")

def get_bool_from_env(key: str, default: bool = False) -> bool:
    value = os.getenv(key, str(default)).lower()  # Default to 'false' if key is not found
    return value in ("true", "1", "yes", "on")

GUILD = os.getenv("GUILD")
if GUILD:
    GUILD_ID = int(GUILD)
else:
    raise Exception(".env/GUILD not defined")

CHANNEL_PAPER_EVENTS_ID = os.getenv("CHANNEL_PAPER_EVENTS")
DEBUG = get_bool_from_env("DEBUG")
DB_PAPER_EVENTS_ID = "f05d532cf91f4f9cbce38e27dc85b522"

class PaperEvents(commands.Cog):

    def __init__(self, bot:Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        log.info("PaperEvents Cog started")
        self.guild = self.bot.get_guild(GUILD_ID)
        if self.guild:
            if not self.check.is_running():
                self.check.start()
        else:
            log.error("Guild not found")

    @tasks.loop(minutes=15)
    async def check(self):
        if self.guild == None:
            return
        # check if there are events that have passed
        # close corresponding threads
        # delete even older threads
        filter = (
            notion.NotionFilterBuilder()
            .add_url_filter("Link", notion.URLCondition.IS_NOT_EMPTY, True)
            .add_checkbox_filter("For Test", notion.CheckboxCondition.EQUALS, DEBUG)
            .build()
        )
        entries = notion.get_all_entries(
            database_id=DB_PAPER_EVENTS_ID,
            filter = filter
        )
        for entry in entries:
            my_entry = notion.Entry(entry)
            date = my_entry.get_date_property('Start (und Ende)')
            event_start:datetime = date['start']
            event_end:datetime = date['end']
            event_discord_channel_id = my_entry.get_text_property("Thread ID")
            event_title = my_entry.get_text_property("Event Titel")
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
                await thread.delete()
                log.info(f"Deleted Event {thread.name}. It was over a month passed.")
            else:
                if (is_passed and not is_archived) or (not is_passed and is_archived):
                    try:
                        await thread.edit(archived=is_passed)
                        log.info(f"{thread.name} - changed thread archive state to: {is_passed}")
                    except discord.Forbidden:
                        log.info(f"Missing permissions to edit thread: {thread.name}")
                    except discord.HTTPException as e:
                        log.info(f"Failed to edit thread {thread.name}: {e}")

def setup(bot:Bot):
    bot.add_cog(PaperEvents(bot))
