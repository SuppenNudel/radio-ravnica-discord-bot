from discord.ext import commands, tasks
import discord
from ezcord import Cog
from discord import Bot
from ezcord import log
from modules import env, paper_events_db
from modules.paper_events_calendar import update_paper_events_calendar
from datetime import datetime, timedelta, time
import os
import logging
import asyncio

link_log = logging.getLogger("link_logger")

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

# Global registry to track active timers
_active_timers: dict[int, dict[str, asyncio.Task]] = {}

class PaperEventsStatusMonitor(Cog):

    def __init__(self, bot:Bot):
        self.bot = bot
        self.guild: discord.Guild | None = None

    def cog_unload(self):
        if self.midnight_calendar_update.is_running():
            self.midnight_calendar_update.cancel()

    @Cog.listener()
    async def on_ready(self):
        self.guild = self.bot.get_guild(GUILD_ID)
        if self.guild:
            await self._restore_timers()
            # Update the calendar on startup
            await update_paper_events_calendar(self.bot, self.guild)
            if not self.midnight_calendar_update.is_running():
                self.midnight_calendar_update.start()
        else:
            log.error("Guild not found")
        log.debug(self.__class__.__name__ + " is ready")

    @tasks.loop(time=time(hour=0, minute=0, second=0, tzinfo=env.TIMEZONE))
    async def midnight_calendar_update(self):
        """Refresh paper events calendar every midnight in configured timezone."""
        guild = self.guild or self.bot.get_guild(GUILD_ID)
        if not guild:
            log.warning("Skipping midnight paper events calendar update: guild not found")
            return
        self.guild = guild
        await update_paper_events_calendar(self.bot, guild)

    @midnight_calendar_update.before_loop
    async def before_midnight_calendar_update(self):
        await self.bot.wait_until_ready()

    async def _restore_timers(self):
        """Restore timers for all existing events on bot startup."""
        if not self.guild:
            return

        conn = paper_events_db.connect()
        paper_events_db.init_db(conn)
        rows = conn.execute(
            """
            SELECT thread_id, start_at, end_at
            FROM paper_events
            WHERE thread_id IS NOT NULL
                            AND server_id = ?
            ORDER BY start_at ASC
            """,
                        [str(self.guild.id)],
        ).fetchall()

        for row in rows:
            thread_id_str = row["thread_id"]
            if not thread_id_str:
                continue
            thread_id = int(thread_id_str)

            try:
                thread = await self.guild.fetch_channel(thread_id)
                if not isinstance(thread, discord.Thread):
                    continue
            except (discord.errors.NotFound, discord.errors.Forbidden):
                continue

            start_str = row["start_at"]
            end_str = row["end_at"]
            event_start = datetime.fromisoformat(start_str) if start_str else None
            event_end = datetime.fromisoformat(end_str) if end_str else None

            if not event_start:
                continue

            await schedule_event_timers(self.bot, self.guild, thread_id, event_start, event_end)

    async def _archive_thread(self, thread_id: int):
        """Archive a thread when the event has passed."""
        if not self.guild:
            return
            
        try:
            thread = await self.guild.fetch_channel(thread_id)
            if isinstance(thread, discord.Thread) and not thread.archived:
                await thread.edit(archived=True)
                log.info(f"Archived event thread {thread.name} (ID: {thread_id})")
        except discord.errors.NotFound:
            log.debug(f"Thread {thread_id} not found, may have been already deleted")
        except discord.Forbidden:
            log.warning(f"Missing permissions to archive thread {thread_id}")
        except Exception as e:
            log.error(f"Error archiving thread {thread_id}: {e}")
        finally:
            # Clean up timer reference
            if thread_id in _active_timers and 'archive' in _active_timers[thread_id]:
                del _active_timers[thread_id]['archive']

    async def _delete_thread(self, thread_id: int):
        """Delete a thread 30 days after the event has ended."""
        if not self.guild:
            return
            
        try:
            thread = await self.guild.fetch_channel(thread_id)
            if isinstance(thread, discord.Thread):
                await thread.delete()
                log.info(f"Deleted event thread {thread.name} (ID: {thread_id}) - over 30 days past")
        except discord.errors.NotFound:
            log.debug(f"Thread {thread_id} not found, may have been already deleted")
        except discord.Forbidden:
            log.warning(f"Missing permissions to delete thread {thread_id}")
        except Exception as e:
            log.error(f"Error deleting thread {thread_id}: {e}")
        finally:
            # Delete the database entry
            try:
                conn = paper_events_db.connect()
                deleted = paper_events_db.delete_event_by_thread_id(conn, thread_id)
                conn.close()
                if deleted:
                    log.debug(f"Deleted DB entry for thread {thread_id}")
            except Exception as e:
                log.error(f"Error deleting DB entry for thread {thread_id}: {e}")
            # Clean up timer references
            if thread_id in _active_timers:
                del _active_timers[thread_id]


async def schedule_event_timers(bot: Bot, guild: discord.Guild, thread_id: int, event_start: datetime, event_end: datetime | None):
    """
    Schedule timers to archive and delete event threads at appropriate times.
    Called when an event is created or on bot startup.
    """
    date_to_compare = event_end if event_end else event_start
    now = datetime.now(tz=date_to_compare.tzinfo)
    
    # Cancel any existing timers for this thread
    if thread_id in _active_timers:
        for task in _active_timers[thread_id].values():
            if not task.done():
                task.cancel()
        _active_timers[thread_id].clear()
    else:
        _active_timers[thread_id] = {}
    
    # Get the cog instance
    cog: PaperEventsStatusMonitor | None = bot.get_cog("PaperEventsStatusMonitor")  # type: ignore
    if not cog:
        log.error("PaperEventsStatusMonitor cog not found")
        return
    
    # Calculate time until archive
    archive_time = date_to_compare
    delete_time = date_to_compare + timedelta(days=30)
    
    # If event already ended more than 30 days ago, delete immediately
    if delete_time <= now:
        await cog._delete_thread(thread_id)
        return
    
    # If event already ended, archive immediately and schedule delete
    if archive_time < now:
        await cog._archive_thread(thread_id)
        seconds_until_delete = (delete_time - now).total_seconds()
        if seconds_until_delete > 0:
            async def delayed_delete():
                await asyncio.sleep(seconds_until_delete)
                await cog._delete_thread(thread_id)
            
            _active_timers[thread_id]['delete'] = asyncio.create_task(delayed_delete())
        else:
            await cog._delete_thread(thread_id)
    else:
        # Schedule both archive and delete
        seconds_until_archive = (archive_time - now).total_seconds()
        seconds_until_delete = (delete_time - now).total_seconds()
        
        async def delayed_archive():
            await asyncio.sleep(seconds_until_archive)
            await cog._archive_thread(thread_id)
        
        async def delayed_delete():
            await asyncio.sleep(seconds_until_delete)
            await cog._delete_thread(thread_id)
        
        _active_timers[thread_id]['archive'] = asyncio.create_task(delayed_archive())
        _active_timers[thread_id]['delete'] = asyncio.create_task(delayed_delete())


def setup(bot:Bot):
    bot.add_cog(PaperEventsStatusMonitor(bot))
