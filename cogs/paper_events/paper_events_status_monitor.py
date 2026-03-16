from discord.ext import commands, tasks
import discord
from ezcord import Cog
from discord import Bot
from ezcord import log
from modules import env, paper_events_db
import modules.paper_events_common as pe_common
from modules.paper_events_calendar import update_paper_events_calendar
from datetime import datetime, timedelta, time
import os
import logging
import asyncio
import calendar

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
            await self._delete_orphan_forum_posts()
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
                    await self._delete_event_entry(thread_id, "channel is no longer a thread")
                    continue
                try:
                    await thread.fetch_message(thread_id)
                except discord.errors.NotFound:
                    await self._delete_event_entry(thread_id, "starter post not found")
                    continue
            except discord.errors.NotFound:
                await self._delete_event_entry(thread_id, "thread not found")
                continue
            except discord.errors.Forbidden:
                continue

            start_str = row["start_at"]
            end_str = row["end_at"]
            event_start = datetime.fromisoformat(start_str) if start_str else None
            event_end = datetime.fromisoformat(end_str) if end_str else None

            if not event_start:
                continue

            await schedule_event_timers(self.bot, self.guild, thread_id, event_start, event_end)

    async def _delete_event_entry(self, thread_id: int, reason: str) -> None:
        conn = paper_events_db.connect()
        try:
            deleted = paper_events_db.delete_event_by_thread_id(conn, thread_id)
        finally:
            conn.close()

        if deleted:
            log.info(f"Deleted stale paper event DB entry for thread {thread_id}: {reason}")

    async def _archive_thread(self, thread_id: int):
        """Archive a thread when the event has passed."""
        if not self.guild:
            return

        await self._create_next_recurring_event(thread_id)
            
        try:
            thread = await self.guild.fetch_channel(thread_id)
            if isinstance(thread, discord.Thread) and not thread.archived:
                try:
                    message = await thread.fetch_message(thread.id)
                    await message.edit(view=None)
                except discord.errors.NotFound:
                    await self._delete_event_entry(thread_id, "starter post not found during archive")
                    return
                except discord.Forbidden:
                    pass
                except Exception as e:
                    log.warning(f"Failed to remove view from archived thread post {thread_id}: {e}")
                await thread.edit(archived=True)
                log.info(f"Archived event thread {thread.name} (ID: {thread_id})")
        except discord.errors.NotFound:
            await self._delete_event_entry(thread_id, "thread not found during archive")
            log.debug(f"Thread {thread_id} not found, may have been already deleted")
        except discord.Forbidden:
            log.warning(f"Missing permissions to archive thread {thread_id}")
        except Exception as e:
            log.error(f"Error archiving thread {thread_id}: {e}")
        finally:
            # Clean up timer reference
            if thread_id in _active_timers and 'archive' in _active_timers[thread_id]:
                del _active_timers[thread_id]['archive']

    @staticmethod
    def _add_months(timestamp: datetime, months: int) -> datetime:
        month_index = timestamp.month - 1 + months
        year = timestamp.year + month_index // 12
        month = month_index % 12 + 1
        day = min(timestamp.day, calendar.monthrange(year, month)[1])
        return timestamp.replace(year=year, month=month, day=day)

    def _next_occurrence(self, start_at: datetime, end_at: datetime | None, recurrence_rule: str) -> tuple[datetime, datetime | None] | None:
        duration = (end_at - start_at) if end_at else None
        rule_parts = {}
        for part in (recurrence_rule or "").split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                rule_parts[key.upper()] = value.upper()

        freq = rule_parts.get("FREQ")
        try:
            interval = int(rule_parts.get("INTERVAL", "1"))
        except ValueError:
            interval = 1

        if freq == "MINUTELY":
            next_start = start_at + timedelta(minutes=interval)
        elif freq == "WEEKLY":
            next_start = start_at + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            next_start = self._add_months(start_at, interval)
        else:
            return None

        next_end = next_start + duration if duration else None
        return next_start, next_end

    async def _delete_orphan_forum_posts(self) -> None:
        if not self.guild:
            return

        forum_channel = self.guild.get_channel(env.CHANNEL_PAPER_EVENTS_ID)
        if forum_channel is None:
            try:
                forum_channel = await self.guild.fetch_channel(env.CHANNEL_PAPER_EVENTS_ID)
            except Exception:
                forum_channel = None

        if not isinstance(forum_channel, discord.ForumChannel):
            log.warning("Orphan forum-post cleanup skipped: paper events channel is not a forum channel")
            return

        conn = paper_events_db.connect()
        paper_events_db.init_db(conn)
        rows = conn.execute(
            """
            SELECT thread_id
            FROM paper_events
            WHERE server_id = ?
              AND thread_id IS NOT NULL
            """,
            (str(self.guild.id),),
        ).fetchall()
        db_thread_ids = {str(row["thread_id"]) for row in rows if row["thread_id"]}
        conn.close()

        all_threads: dict[int, discord.Thread] = {}
        for thread in forum_channel.threads:
            if isinstance(thread, discord.Thread):
                all_threads[thread.id] = thread

        try:
            async for thread in forum_channel.archived_threads(limit=None):
                if isinstance(thread, discord.Thread):
                    all_threads[thread.id] = thread
        except Exception as e:
            log.warning(f"Failed to enumerate archived forum posts for orphan cleanup: {e}")

        deleted_count = 0
        skipped_pinned = 0
        for thread in all_threads.values():
            pinned = bool(getattr(thread, "pinned", False))
            if not pinned:
                flags = getattr(thread, "flags", None)
                if flags is not None:
                    pinned = bool(getattr(flags, "pinned", False))

            if pinned:
                skipped_pinned += 1
                continue

            if str(thread.id) in db_thread_ids:
                continue

            try:
                await thread.delete()
                deleted_count += 1
            except discord.NotFound:
                pass
            except discord.Forbidden:
                log.warning(f"Missing permissions to delete orphan forum post {thread.id}")
            except Exception as e:
                log.warning(f"Failed to delete orphan forum post {thread.id}: {e}")

        if deleted_count or skipped_pinned:
            log.info(
                f"Orphan forum-post cleanup finished: deleted {deleted_count}, skipped pinned {skipped_pinned}"
            )

    async def _resolve_row_author(self, row) -> discord.Member | None:
        if not self.guild:
            return None
        author_id_raw = row["author_id"]
        author: discord.Member | None = None
        if author_id_raw:
            try:
                author_id = int(author_id_raw)
                author = self.guild.get_member(author_id)
                if author is None:
                    author = await self.guild.fetch_member(author_id)
            except Exception:
                author = None
        if not isinstance(author, discord.Member):
            author = self.guild.me or self.guild.owner
        return author if isinstance(author, discord.Member) else None

    async def _delete_future_occurrence(self, row) -> None:
        thread_id_raw = row["thread_id"]
        if not thread_id_raw:
            return
        thread_id = int(thread_id_raw)

        if thread_id in _active_timers:
            for task in _active_timers[thread_id].values():
                if not task.done():
                    task.cancel()
            del _active_timers[thread_id]

        if self.guild:
            try:
                thread = await self.guild.fetch_channel(thread_id)
                if isinstance(thread, discord.Thread):
                    await thread.delete()
            except Exception:
                pass

        conn = paper_events_db.connect()
        try:
            paper_events_db.delete_event_by_thread_id(conn, thread_id)
        finally:
            conn.close()

    async def _propagate_recurring_edits(self, source_thread_id: int) -> None:
        """No-op: events are now independent, no edit propagation needed."""
        pass

    async def _create_next_recurring_event(self, thread_id: int):
        """If thread belongs to a recurring event, create the next occurrence according to recurrence_rule."""
        if not self.guild:
            return

        conn = paper_events_db.connect()
        paper_events_db.init_db(conn)
        row = conn.execute(
            """
            SELECT *
            FROM paper_events
            WHERE thread_id = ?
              AND server_id = ?
            LIMIT 1
            """,
            (str(thread_id), str(self.guild.id)),
        ).fetchone()

        if not row:
            conn.close()
            return

        is_recurring = int(row["is_recurring"] or 0)
        recurrence_rule = row["recurrence_rule"]
        if not is_recurring or not recurrence_rule:
            conn.close()
            return

        start_str = row["start_at"]
        end_str = row["end_at"]
        if not start_str:
            conn.close()
            return

        try:
            start_at = datetime.fromisoformat(start_str)
            end_at = datetime.fromisoformat(end_str) if end_str else None
        except Exception:
            conn.close()
            return

        next_occurrence = self._next_occurrence(start_at, end_at, recurrence_rule)
        if not next_occurrence:
            log.warning(f"Unsupported recurrence_rule '{recurrence_rule}' for thread {thread_id}")
            conn.close()
            return
        next_start, next_end = next_occurrence

        recurrence_until_raw = row["recurrence_until"]
        recurrence_count = row["recurrence_count"]

        # Prevent unlimited MINUTELY series
        if recurrence_rule == "FREQ=MINUTELY;INTERVAL=1" and recurrence_count is None and not recurrence_until_raw:
            conn.close()
            log.info(
                f"Recurring series for thread {thread_id} skipped: unlimited MINUTELY recurrence requires recurrence_count or recurrence_until"
            )
            return

        # Check recurrence_until
        if recurrence_until_raw:
            try:
                recurrence_until = datetime.fromisoformat(recurrence_until_raw)
            except Exception:
                recurrence_until = None
            if recurrence_until and next_start > recurrence_until:
                conn.close()
                log.info(f"Recurring series for thread {thread_id} reached recurrence_until {recurrence_until.isoformat()}")
                return

        # Check recurrence_count
        next_recurrence_count = None
        if recurrence_count is not None:
            next_recurrence_count = int(recurrence_count) - 1
            if next_recurrence_count < 0:
                conn.close()
                log.info(f"Recurring series for thread {thread_id} reached recurrence_count {recurrence_count}")
                return

        conn.close()

        forum_channel = self.guild.get_channel(env.CHANNEL_PAPER_EVENTS_ID)
        if forum_channel is None:
            try:
                forum_channel = await self.guild.fetch_channel(env.CHANNEL_PAPER_EVENTS_ID)
            except Exception:
                forum_channel = None
        if not isinstance(forum_channel, discord.ForumChannel):
            log.warning("Recurring event creation skipped: paper events channel is not a forum channel")
            return

        author: discord.Member | None = None
        author_id_raw = row["author_id"]
        if author_id_raw:
            try:
                author_id = int(author_id_raw)
                author = self.guild.get_member(author_id)
                if author is None:
                    author = await self.guild.fetch_member(author_id)
            except Exception:
                author = None

        if not isinstance(author, discord.Member):
            author = self.guild.me or self.guild.owner
        if not isinstance(author, discord.Member):
            log.warning(f"Recurring event creation skipped: no valid author for thread {thread_id}")
            return

        paper_event = pe_common.PaperEvent(self.guild, author)
        paper_event.fill_fields_from_db_row(row)
        paper_event.fields[pe_common.FieldName.START]._value = next_start
        paper_event.fields[pe_common.FieldName.END]._value = next_end

        try:
            thread = await paper_event.create_and_send_thread(forum_channel)
            new_row_id = paper_event.save_to_db()

            conn = paper_events_db.connect()
            conn.execute(
                """
                UPDATE paper_events
                SET is_generated = 1,
                    is_recurring = 1,
                    recurrence_rule = ?,
                    recurrence_until = ?,
                    recurrence_count = ?
                WHERE id = ?
                """,
                (recurrence_rule, recurrence_until_raw, next_recurrence_count, new_row_id),
            )
            conn.commit()
            conn.close()

            await schedule_event_timers(self.bot, self.guild, thread.id, next_start, next_end)
            await update_paper_events_calendar(self.bot, self.guild)

            log.info(f"Created next recurring event for thread {thread_id}: new thread {thread.id}")
        except Exception as e:
            log.error(f"Failed to create next recurring event for thread {thread_id}: {e}")

    async def propagate_recurring_edits(self, thread_id: int) -> None:
        await self._propagate_recurring_edits(thread_id)

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


async def propagate_recurring_edits(bot: Bot, guild: discord.Guild, thread_id: int):
    cog: PaperEventsStatusMonitor | None = bot.get_cog("PaperEventsStatusMonitor")  # type: ignore
    if not cog:
        log.error("PaperEventsStatusMonitor cog not found")
        return
    if cog.guild is None:
        cog.guild = guild
    await cog.propagate_recurring_edits(thread_id)


def setup(bot:Bot):
    bot.add_cog(PaperEventsStatusMonitor(bot))
