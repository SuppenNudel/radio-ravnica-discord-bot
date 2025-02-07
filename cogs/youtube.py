from discord.ext import commands, tasks
import os
import discord
from discord import Bot
import scrapetube
from ezcord import log
from enum import Enum

class ContentType(Enum):
    VIDEOS = "videos"
    # SHORTS = "shorts"
    STREAMS = "streams"

class YoutubeChannel():
    def __init__(self, name:str, dc_user_id:int):
        self.name = name
        self.dc_user_id = dc_user_id
        self.content = {}
        for content_type in ContentType:
            self.content[content_type] = None

class Youtube(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

        self.channels = [
            YoutubeChannel("GameRii", 270288996666441728)
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

        log.debug(self.__class__.__name__ + " is ready")

    async def post_video(self, yt_channel:YoutubeChannel, content_type:ContentType, content):
        url = f"https://www.youtube.com/watch?v={content['videoId']}"
        if content_type == ContentType.STREAMS:
            await self.discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\n<@{yt_channel.dc_user_id}> streamt gleich! Kommt, schaut vorbei!\n\n{url}")
        else:
            await self.discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneues Video von **<@{yt_channel.dc_user_id}>**\n\n{url}")

    async def get_latest_content(self, channel:YoutubeChannel, content_type:ContentType):
        generator = scrapetube.get_channel(
            channel_username=channel.name,
            limit=5,
            content_type=content_type.value,
            sort_by='newest')
        skipped = []
        for content in generator:
            if 'badges' in content:
                skipped.append(content)
                continue
            return content
        raise Exception("No 'non-badge' content in the last 5 videos")

    @tasks.loop(minutes=5)
    async def check_channels(self):
        for yt_channel in self.channels:
            for content_type, content_cache in yt_channel.content.items():
                latest_content = await self.get_latest_content(yt_channel, content_type)
                log.debug(f"https://www.youtube.com/watch?v={latest_content['videoId']} is the latest {content_type}")
                if content_cache and not content_cache['videoId'] == latest_content['videoId']:
                    log.debug("it is new, going to post")
                    await self.post_video(yt_channel, content_type, latest_content)
                else:
                    log.debug("it has been posted already")
                yt_channel.content[content_type] = latest_content

def setup(bot:Bot):
    bot.add_cog(Youtube(bot))
    