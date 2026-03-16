import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
import hashlib
import discord
from discord.utils import format_dt
from ezcord import log
from modules import env, paper_events_db
from modules.util.generate_paper_events_calendar import generate_paper_events_calendar

MAX_CALENDAR_ITEMS = 8


class PaperEventCalendarItem:
    def __init__(self, title: str, start: datetime, end: datetime | None = None, url: str | None = None):
        self.title = title
        self.time = start
        self._end = end or start
        self.url = url
        self.title_x_offset = None
        self.title_y_offset = None

    def calc_end(self):
        return self._end


def _build_discord_jump_url(guild_id: str, thread_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{thread_id}/{thread_id}"


def _normalize_dt(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=env.TIMEZONE)
    return value


def _build_calendar_items(guild: discord.Guild) -> list[PaperEventCalendarItem]:
    now = datetime.now(tz=env.TIMEZONE)
    items: list[PaperEventCalendarItem] = []

    conn = paper_events_db.connect()
    paper_events_db.init_db(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM paper_events
        WHERE server_id = ?
          AND thread_id IS NOT NULL
        ORDER BY start_at ASC
        """,
        (str(guild.id),),
    ).fetchall()

    for row in rows:
        start_raw = row["start_at"]
        if not start_raw:
            continue
        try:
            start = _normalize_dt(datetime.fromisoformat(start_raw))
        except Exception:
            continue
        if start is None:
            continue

        end_raw = row["end_at"]
        end = start
        if end_raw:
            try:
                parsed_end = _normalize_dt(datetime.fromisoformat(end_raw))
                if parsed_end:
                    end = parsed_end
            except Exception:
                pass

        # Only show events that start in the future
        if start <= now:
            continue

        title = row["title"] or "Paper Event"
        thread_id = row["thread_id"]
        server_id = row["server_id"]
        discord_url = _build_discord_jump_url(str(server_id), str(thread_id)) if thread_id and server_id else None
        url = discord_url or row["url"]
        items.append(PaperEventCalendarItem(title=title, start=start, end=end, url=url))

    items.sort(key=lambda item: item.time)
    return items


def _calculate_signature(items: list[PaperEventCalendarItem]) -> str:
    signature_parts = []
    for item in items:
        signature_parts.append(
            f"{item.title}|{item.time.isoformat()}|{item.calc_end().isoformat()}|{item.url or ''}"
        )
    payload = "\n".join(signature_parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_calendar_message(items: list[PaperEventCalendarItem]) -> str:
    msg = "## \ud83d\uddd3\ufe0f Paper Events Kalender\n\n"
    if not items:
        msg += "> _Keine kommenden Events._"
        return msg

    visible_items = items[:MAX_CALENDAR_ITEMS]
    msg += "### \ud83d\udcc5 Kommende Events\n"
    for item in visible_items:
        if item.url:
            line = f"> 🏠 **[{item.title}]({item.url})**"
        else:
            line = f"> 🏠 **{item.title}**"
        msg += line + "\n"

        # Use date-only format if time is 0:00
        start_format = 'D' if item.time.hour == 0 and item.time.minute == 0 else 'F'
        start_text = f"{format_dt(item.time, start_format)} ({format_dt(item.time, 'R')})"
        msg += f"> 🗓️ Start: {start_text}\n"

        end = item.calc_end()
        if end and end != item.time:
            end_format = 'D' if end.hour == 0 and end.minute == 0 else 'F'
            end_text = f"{format_dt(end, end_format)} ({format_dt(end, 'R')})"
            msg += f"> \u23f3 Ende: {end_text}\n"
        msg += "\n"

    remaining_count = len(items) - len(visible_items)
    if remaining_count > 0:
        msg += f"> _… und {remaining_count} weitere kommende Events._\n"

    return msg


async def _export_events_json_and_push(guild: discord.Guild) -> None:
    """Export events.json to the paper-events repo and git commit+push."""
    repo_path_str = env.PAPER_EVENTS_EXPORT_REPO_PATH
    if not repo_path_str:
        return

    repo_path = Path(repo_path_str)
    if not repo_path.is_dir():
        log.warning(f"PAPER_EVENTS_EXPORT_REPO_PATH '{repo_path}' does not exist; skipping events.json export")
        return

    output_path = repo_path / "data" / "events.json"

    conn = paper_events_db.connect()
    paper_events_db.init_db(conn)
    try:
        paper_events_db.export_public_events_json(
            conn,
            output_path=output_path,
            server_id=str(guild.id),
        )
    except Exception as e:
        log.error(f"Failed to export events.json: {e}")
        return

    def _git_commit_push() -> str | None:
        r = subprocess.run(
            ["git", "add", str(output_path.relative_to(repo_path))],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return f"git add failed: {r.stderr.strip()}"

        r = subprocess.run(
            ["git", "commit", "-m", "Update events.json"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # "nothing to commit" is not an error
            combined = (r.stdout + r.stderr).lower()
            if "nothing to commit" in combined:
                return None
            return f"git commit failed: {r.stderr.strip()}"

        r = subprocess.run(
            ["git", "push"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return f"git push failed: {r.stderr.strip()}"

        return None

    try:
        err = await asyncio.to_thread(_git_commit_push)
        if err:
            log.error(f"Failed to push events.json: {err}")
        else:
            log.info(f"Pushed updated events.json to {repo_path_str}")
    except Exception as e:
        log.error(f"Unexpected error during events.json git push: {e}")


async def update_paper_events_calendar(bot: discord.Bot, guild: discord.Guild):
    await _export_events_json_and_push(guild)

    if not env.PAPER_EVENTS_CALENDAR_CHANNEL_ID:
        log.warning("PAPER_EVENTS_CALENDAR_CHANNEL_ID not set; skipping paper events calendar update")
        return

    items = _build_calendar_items(guild)
    signature = _calculate_signature(items)

    # Get the channel
    channel = guild.get_channel(env.PAPER_EVENTS_CALENDAR_CHANNEL_ID)
    if not channel:
        try:
            channel = await guild.fetch_channel(env.PAPER_EVENTS_CALENDAR_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden):
            log.warning(f"Paper events calendar channel {env.PAPER_EVENTS_CALENDAR_CHANNEL_ID} not found")
            return
    
    # Ensure it's a text-based channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        log.warning(f"Paper events calendar channel {env.PAPER_EVENTS_CALENDAR_CHANNEL_ID} is not a text channel")
        return

    calendar_message = None
    if env.PAPER_EVENTS_CALENDAR_MESSAGE_ID:
        try:
            calendar_message = await channel.fetch_message(env.PAPER_EVENTS_CALENDAR_MESSAGE_ID)
            log.debug(f"Found existing calendar message: {env.PAPER_EVENTS_CALENDAR_MESSAGE_ID}")
        except discord.NotFound:
            log.warning(f"Paper events calendar message {env.PAPER_EVENTS_CALENDAR_MESSAGE_ID} not found, creating a new one")
        except discord.Forbidden:
            log.warning("Missing permissions to fetch paper events calendar message")
            return

    # Check if update is needed by comparing signatures
    # We store the last signature in memory to avoid unnecessary updates
    if calendar_message and hasattr(update_paper_events_calendar, '_last_signature'):
        if update_paper_events_calendar._last_signature == signature:
            log.debug("Calendar signature unchanged, skipping update")
            return

    # Generate calendar image
    calendar_img_path = None
    try:
        calendar_img_path = generate_paper_events_calendar(items) if items else None
        if calendar_img_path:
            log.debug(f"Generated calendar image: {calendar_img_path}")
    except Exception as e:
        log.error(f"Failed to generate calendar image: {e}")
    
    calendar_file = discord.File(calendar_img_path, filename="calendar.png") if calendar_img_path else None
    message_content = _build_calendar_message(items)

    try:
        if calendar_message:
            if calendar_file:
                await calendar_message.edit(content=message_content, attachments=[], file=calendar_file)
            else:
                await calendar_message.edit(content=message_content, attachments=[])
            log.info(f"Updated paper events calendar message: {calendar_message.jump_url}")
        else:
            if calendar_file:
                calendar_message = await channel.send(message_content, file=calendar_file)
            else:
                calendar_message = await channel.send(message_content)
            env.PAPER_EVENTS_CALENDAR_MESSAGE_ID = calendar_message.id
            env_key = "PAPER_EVENTS_CALENDAR_MESSAGE_ID_DEBUG" if env.DEBUG else "PAPER_EVENTS_CALENDAR_MESSAGE_ID"
            env.save_to_env(env_key, calendar_message.id)
            log.info(f"Created new paper events calendar message: {calendar_message.jump_url}")
        
        # Store signature for next comparison
        update_paper_events_calendar._last_signature = signature
    except Exception as e:
        log.error(f"Failed to update paper events calendar: {e}")
