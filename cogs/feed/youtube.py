from discord.ext import commands, tasks
import os
import discord
from discord import Bot
from ezcord import log
from enum import Enum
import asyncio
import re
import requests
import xml.etree.ElementTree as ET

class ContentType(Enum):
    VIDEOS = "videos"

class YoutubeChannel():
    def __init__(self, handle:str, dc_user_id:int, channel_id:str|None=None):
        self.handle = handle.lstrip("@")
        self.dc_user_id = dc_user_id
        self.channel_id = channel_id
        self.seen_video_ids = set()
        self.content = {}
        for content_type in ContentType:
            self.content[content_type] = None

class Youtube(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.channels = [
            YoutubeChannel("GameRii", 270288996666441728, os.getenv("YOUTUBE_CHANNEL_GAMERII_ID"))
        ]

        channel_id_str = os.getenv("CHANNEL_YOUTUBE")
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error("The environment variable 'CHANNEL_YOUTUBE' is not a valid integer.")


    @commands.Cog.listener()
    async def on_ready(self):
        discord_channel = await discord.utils.get_or_fetch(self.bot, 'channel', self.channel_id)
        if not type(discord_channel) == discord.TextChannel:
            log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
            raise Exception("discord_channel is not a discord.TextChannel")
        self.discord_channel:discord.TextChannel = discord_channel

        if not self.check_channels.is_running():
            self.check_channels.start()

        log.info(self.__class__.__name__ + " is ready")

    async def post_video(self, yt_channel:YoutubeChannel, content_type:ContentType, content):
        url = f"https://www.youtube.com/watch?v={content['videoId']}"
        await self.discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneues Video von **<@{yt_channel.dc_user_id}>**\n\n{url}")

    def resolve_channel_id(self, channel:YoutubeChannel) -> str | None:
        if channel.channel_id:
            return channel.channel_id

        response = requests.get(
            f"https://www.youtube.com/@{channel.handle}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()

        match = re.search(r'channelId\"\s*:\s*\"(UC[\w-]+)\"', response.text)
        if not match:
            match = re.search(r'/channel/(UC[\w-]+)', response.text)
        if not match:
            return None

        channel.channel_id = match.group(1)
        return channel.channel_id

    def fetch_recent_videos_from_feed(self, channel:YoutubeChannel, max_entries:int=10) -> list[dict]:
        if not channel.channel_id:
            raise ValueError(f"Missing YouTube channel id for @{channel.handle}")

        response = requests.get(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.channel_id}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()

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
            videos.append(
                {
                    "videoId": video_id,
                    "title": entry.findtext("atom:title", default="", namespaces=namespaces),
                    "published": entry.findtext("atom:published", default="", namespaces=namespaces),
                }
            )

        return videos

    async def ensure_channel_id(self, channel:YoutubeChannel) -> bool:
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

    async def get_latest_content(self, channel:YoutubeChannel, content_type:ContentType):
        if content_type != ContentType.VIDEOS:
            return None, False

        if not await self.ensure_channel_id(channel):
            return None, True

        loop = asyncio.get_event_loop()

        MAX_RETRIES = 4
        for attempt in range(MAX_RETRIES + 1):
            try:
                contents = await loop.run_in_executor(None, self.fetch_recent_videos_from_feed, channel)
                if not contents:
                    log.debug(f"No {content_type.value} found in the RSS feed for @{channel.handle}")
                return contents, False
            except Exception as e:
                is_transient = any(keyword in str(e) for keyword in (
                    "NameResolutionError", "Failed to resolve", "Max retries exceeded",
                    "ConnectionError", "Timeout", "RemoteDisconnected",
                    "Read timed out", "temporarily unavailable"
                ))
                if is_transient and attempt < MAX_RETRIES:
                    wait = 2 ** attempt
                    log.warning(f"Transient error fetching YouTube RSS for @{channel.handle} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait}s: {e}")
                    await asyncio.sleep(wait)
                else:
                    log.error(f"Error fetching YouTube RSS for @{channel.handle}: {e}")
                    return None, True

    @tasks.loop(minutes=5)
    async def check_channels(self):
        for yt_channel in self.channels:
            for content_type, content_cache in yt_channel.content.items():
                recent_contents, fetch_failed = await self.get_latest_content(yt_channel, content_type)
                if fetch_failed:
                    log.warning(
                        f"Skipping remaining YouTube content types for @{yt_channel.handle} in this cycle after a fetch failure on {content_type.value}."
                    )
                    break
                if not recent_contents:
                    continue

                newest_content = recent_contents[0]

                # Initialize from current feed snapshot to avoid posting older backlog at startup.
                if not yt_channel.seen_video_ids:
                    for content in recent_contents:
                        yt_channel.seen_video_ids.add(content["videoId"])
                    yt_channel.content[content_type] = newest_content
                    continue

                unseen_contents = [
                    content for content in reversed(recent_contents)
                    if content["videoId"] not in yt_channel.seen_video_ids
                ]

                for content in unseen_contents:
                    log.info(f"going to post https://www.youtube.com/watch?v={content['videoId']} as {content_type}")
                    await self.post_video(yt_channel, content_type, content)
                    yt_channel.seen_video_ids.add(content["videoId"])

                yt_channel.content[content_type] = newest_content

def setup(bot:Bot):
    bot.add_cog(Youtube(bot))
    
