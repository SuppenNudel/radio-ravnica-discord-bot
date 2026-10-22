from dataclasses import dataclass
from contextlib import closing
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence
import asyncio
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

import discord
import requests
from discord import Bot
from discord.ext import commands, tasks
from ezcord import log


class ContentType(Enum):
    VIDEOS = "videos"


@dataclass(frozen=True)
class YoutubeChannelDefinition:
    handle: str
    dc_user_id: int
    channel_id_env: str | None = None


@dataclass(frozen=True)
class YoutubeFeedConfig:
    notification_channel_env: str
    channels: Sequence[YoutubeChannelDefinition]
    message_builder: Callable[["YoutubeChannel", dict], str]


class YoutubeChannel:
    def __init__(self, handle: str, dc_user_id: int, channel_id: str | None = None):
        self.handle = handle.lstrip("@")
        self.dc_user_id = dc_user_id
        self.channel_id = channel_id
        self.feed_etag: str | None = None
        self.seen_video_ids = set()
        self.latest_video = None
        self.content = {ContentType.VIDEOS: None}


class BaseYoutube(commands.Cog):
    feed_config: YoutubeFeedConfig | None = None
    state_db_path = Path(__file__).resolve().parents[1] / "data" / "youtube_feed.db"

    youtube_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Cookie": "SOCS=CAI",
    }

    def __init__(self, bot: Bot):
        if self.feed_config is None:
            raise ValueError("Youtube feed configuration is missing")

        self.bot = bot
        self.channels = [
            YoutubeChannel(
                definition.handle,
                definition.dc_user_id,
                os.getenv(definition.channel_id_env) if definition.channel_id_env else None,
            )
            for definition in self.feed_config.channels
        ]

        self.channel_id: int | None = None
        channel_id_str = os.getenv(self.feed_config.notification_channel_env)
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error(
                    "The environment variable '%s' is not a valid integer.",
                    self.feed_config.notification_channel_env,
                )

        if self.channel_id is not None:
            self.state_db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.state_db_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS seen_videos ("
                    "discord_channel_id TEXT NOT NULL, handle TEXT NOT NULL, video_id TEXT NOT NULL, "
                    "PRIMARY KEY (discord_channel_id, handle, video_id))"
                )
                for channel in self.channels:
                    channel.seen_video_ids.update(
                        row[0] for row in connection.execute(
                            "SELECT video_id FROM seen_videos WHERE discord_channel_id = ? AND handle = ?",
                            (str(self.channel_id), channel.handle.casefold()),
                        )
                    )

    def remember_videos(self, channel: YoutubeChannel, contents: Sequence[dict]):
        video_ids = {content["videoId"] for content in contents}
        with closing(sqlite3.connect(self.state_db_path)) as connection, connection:
            connection.executemany(
                "INSERT OR IGNORE INTO seen_videos (discord_channel_id, handle, video_id) VALUES (?, ?, ?)",
                [(str(self.channel_id), channel.handle.casefold(), video_id) for video_id in video_ids],
            )
        channel.seen_video_ids.update(video_ids)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.channel_id is None:
            log.error(
                "The environment variable '%s' is missing or invalid.",
                self.feed_config.notification_channel_env,
            )
            return

        discord_channel = await discord.utils.get_or_fetch(self.bot, "channel", self.channel_id)
        if not isinstance(discord_channel, discord.TextChannel):
            log.error("type of discord_channel is not discord.TextChannel, but %s", type(discord_channel))
            raise Exception("discord_channel is not a discord.TextChannel")
        self.discord_channel: discord.TextChannel = discord_channel

        if not self.check_channels.is_running():
            self.check_channels.start()

        log.info(self.__class__.__name__ + " is ready")

    async def post_video(
        self,
        yt_channel: YoutubeChannel,
        content_type: ContentType | dict,
        content: dict | None = None,
    ):
        if content is None:
            content = content_type
            content_type = ContentType.VIDEOS

        if content_type != ContentType.VIDEOS:
            raise ValueError(f"Unsupported YouTube content type: {content_type}")

        await self.discord_channel.send(self.feed_config.message_builder(yt_channel, content))

    def resolve_channel_id(self, channel: YoutubeChannel) -> str | None:
        if channel.channel_id:
            return channel.channel_id

        response = requests.get(
            f"https://www.youtube.com/@{channel.handle}",
            timeout=10,
            headers=self.youtube_headers,
        )
        response.raise_for_status()

        match = re.search(r'channelId\"\s*:\s*\"(UC[\w-]+)\"', response.text)
        if not match:
            match = re.search(r"/channel/(UC[\w-]+)", response.text)
        if not match:
            return None

        channel.channel_id = match.group(1)
        return channel.channel_id

    def fetch_recent_videos_from_feed(
        self, channel: YoutubeChannel, max_entries: int = 10
    ) -> list[dict] | None:
        if not channel.channel_id:
            raise ValueError(f"Missing YouTube channel id for @{channel.handle}")

        headers = {"User-Agent": "Mozilla/5.0"}
        if channel.feed_etag:
            headers["If-None-Match"] = channel.feed_etag

        response = requests.get(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.channel_id}",
            timeout=10,
            headers=headers,
        )

        if response.status_code == 304:
            return None

        response.raise_for_status()

        etag = response.headers.get("ETag")
        if etag:
            channel.feed_etag = etag

        root = ET.fromstring(response.text)
        namespaces = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        entries = root.findall("atom:entry", namespaces)

        videos = []
        for entry in entries[:max_entries]:
            video_id = entry.findtext("yt:videoId", default=None, namespaces=namespaces)
            if not video_id:
                continue

            link_element = entry.find("atom:link[@rel='alternate']", namespaces)
            if link_element is not None:
                link_href = link_element.get("href", "")
                if "shorts" in link_href:
                    continue

            videos.append(
                {
                    "videoId": video_id,
                    "title": entry.findtext("atom:title", default="", namespaces=namespaces),
                    "published": entry.findtext("atom:published", default="", namespaces=namespaces),
                }
            )

        return videos

    def fetch_recent_videos_from_page(
        self, channel: YoutubeChannel, max_entries: int = 10
    ) -> list[dict]:
        response = requests.get(
            f"https://www.youtube.com/@{channel.handle}/videos?hl=en&gl=US",
            timeout=10,
            headers=self.youtube_headers,
        )
        response.raise_for_status()

        marker = "var ytInitialData = "
        marker_index = response.text.find(marker)
        if marker_index == -1:
            raise ValueError(f"YouTube page data is missing for @{channel.handle}")

        page_data, _ = json.JSONDecoder().raw_decode(
            response.text[marker_index + len(marker):]
        )

        def iter_lockups(value):
            if isinstance(value, dict):
                lockup = value.get("lockupViewModel")
                if lockup:
                    yield lockup
                    return
                for child in value.values():
                    yield from iter_lockups(child)
            elif isinstance(value, list):
                for child in value:
                    yield from iter_lockups(child)

        videos = []
        for lockup in iter_lockups(page_data):
            video_id = lockup.get("contentId")
            if lockup.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not re.fullmatch(
                r"[\w-]{11}", video_id or ""
            ):
                continue

            metadata = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
            metadata_rows = (
                metadata.get("metadata", {})
                .get("contentMetadataViewModel", {})
                .get("metadataRows", [])
            )
            if any(row.get("badges") for row in metadata_rows):
                continue

            videos.append(
                {
                    "videoId": video_id,
                    "title": metadata.get("title", {}).get("content", ""),
                    "published": "",
                }
            )
            if len(videos) == max_entries:
                break

        return videos

    def fetch_recent_videos(
        self, channel: YoutubeChannel, max_entries: int = 10
    ) -> list[dict] | None:
        try:
            return self.fetch_recent_videos_from_feed(channel, max_entries)
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code != 404:
                raise
            return self.fetch_recent_videos_from_page(channel, max_entries)

    async def ensure_channel_id(self, channel: YoutubeChannel) -> bool:
        if channel.channel_id:
            return True

        loop = asyncio.get_event_loop()
        try:
            channel_id = await loop.run_in_executor(None, self.resolve_channel_id, channel)
        except Exception as error:
            log.error(f"Error resolving YouTube channel id for @{channel.handle}: {error}")
            return False

        if not channel_id:
            log.error(f"Could not resolve YouTube channel id for @{channel.handle}")
            return False

        log.info(f"Resolved YouTube channel id for @{channel.handle}: {channel_id}")
        return True

    async def get_latest_content(self, channel: YoutubeChannel, content_type: ContentType = ContentType.VIDEOS):
        if content_type != ContentType.VIDEOS:
            return None, False, False

        if not await self.ensure_channel_id(channel):
            return None, True, False

        loop = asyncio.get_event_loop()

        max_retries = 4
        for attempt in range(max_retries + 1):
            try:
                contents = await loop.run_in_executor(None, self.fetch_recent_videos, channel)
                if contents is None:
                    log.debug(f"No changes in YouTube RSS feed for @{channel.handle} (304 Not Modified)")
                    return None, False, True
                if not contents:
                    log.debug(f"No videos found in the RSS feed for @{channel.handle}")
                return contents, False, False
            except Exception as error:
                is_transient = any(
                    keyword in str(error)
                    for keyword in (
                        "NameResolutionError",
                        "Failed to resolve",
                        "Max retries exceeded",
                        "ConnectionError",
                        "Timeout",
                        "RemoteDisconnected",
                        "Read timed out",
                        "temporarily unavailable",
                    )
                )
                if is_transient and attempt < max_retries:
                    wait = 2**attempt
                    log.warning(
                        "Transient error fetching YouTube RSS for @%s (attempt %s/%s), retrying in %ss: %s",
                        channel.handle,
                        attempt + 1,
                        max_retries,
                        wait,
                        error,
                    )
                    await asyncio.sleep(wait)
                else:
                    log.error(f"Error fetching YouTube RSS for @{channel.handle}: {error}")
                    return None, True, False

    def update_latest_content(self, channel: YoutubeChannel, newest_content: dict):
        channel.latest_video = newest_content
        channel.content[ContentType.VIDEOS] = newest_content

    @tasks.loop(minutes=10)
    async def check_channels(self):
        for yt_channel in self.channels:
            recent_contents, fetch_failed, not_modified = await self.get_latest_content(yt_channel)
            if fetch_failed:
                log.warning(
                    f"Skipping YouTube check for @{yt_channel.handle} in this cycle after a fetch failure."
                )
                continue
            if not_modified:
                continue
            if not recent_contents:
                continue

            newest_content = recent_contents[0]

            if not yt_channel.seen_video_ids:
                self.remember_videos(yt_channel, recent_contents)
                self.update_latest_content(yt_channel, newest_content)
                continue

            for content in reversed(recent_contents):
                if content["videoId"] in yt_channel.seen_video_ids:
                    continue
                log.info(f"going to post https://www.youtube.com/watch?v={content['videoId']}")
                await self.post_video(yt_channel, ContentType.VIDEOS, content)
                self.remember_videos(yt_channel, [content])

            self.update_latest_content(yt_channel, newest_content)


def create_youtube_cog(config: YoutubeFeedConfig):
    class Youtube(BaseYoutube):
        feed_config = config

    return Youtube