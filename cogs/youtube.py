from discord.ext import commands, tasks
import os
import discord
from discord import Bot
import scrapetube
from ezcord import log
from enum import Enum

KEEP_TRACK_COUNT = 5

class ContentType(Enum):
    VIDEOS = "videos"
    SHORTS = "shorts"
    STREAMS = "streams"

class YoutubeChannel():
    def __init__(self, name:str, dc_user_id:int):
        self.name = name
        self.dc_user_id = dc_user_id
        for content_type in ContentType:
            setattr(self, content_type.value, [])

class Youtube(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

        self.channels = [
            YoutubeChannel("gamerii", 270288996666441728)
        ]

        channel_id_str = os.getenv("CHANNEL_YOUTUBE")
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error("The environment variable 'CHANNEL_YOUTUBE' is not a valid integer.")


    @commands.Cog.listener()
    async def on_ready(self):
        discord_channel = self.bot.get_channel(self.channel_id)
        if not type(discord_channel) == discord.TextChannel:
            log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
            raise Exception("discord_channel is not a discord.TextChannel")
        self.discord_channel = discord_channel

        if not self.check_yt_video.is_running():
            self.check_yt_video.start()
        if not self.check_yt_livestream.is_running():
            self.check_yt_livestream.start()

        log.debug(self.__class__.__name__ + " is ready")

    def add_unique_with_limit(self, item, target_list:list, limit=KEEP_TRACK_COUNT):
        if item not in target_list:
            if len(target_list) >= limit:
                target_list.pop(0)  # Remove the oldest item to make space
            target_list.append(item)

    def get_list(self, content_type, channel_name):
        return self.channels[channel_name][content_type]

    async def handle_video(self, loop:tasks.Loop, channel:YoutubeChannel, video, content_type:ContentType):
        video_id = video['videoId']

        video_list = channel.__getattribute__(content_type.value)

        if loop.current_loop == 0:
            self.add_unique_with_limit(video_id, video_list)
            return
        
        in_list = video_id in video_list
        has_badges = 'badges' in video
        if has_badges:
            return
        if not in_list:
            url = f"https://youtu.be/{video_id}"
            if content_type == ContentType.STREAMS:
                await self.discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\n<@{channel.dc_user_id}> streamt gleich! Kommt, schaut vorbei!\n\n{url}")
            else:
                await self.discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneues Video von **<@{channel.dc_user_id}>**\n\n{url}")
            self.add_unique_with_limit(video_id, video_list)

    async def look_for_new_content(self, content_type:ContentType):
        for channel in self.channels:
            result = scrapetube.get_channel(channel_url=f"https://www.youtube.com/@{channel.name}", limit=KEEP_TRACK_COUNT, content_type=content_type.value)
            reverse_result = list(result)[::-1] # so that the newsest video is at the end
            for content in reverse_result:
                await self.handle_video(self.check_yt_livestream, channel, content, content_type)

    @tasks.loop(seconds=60)
    async def check_yt_livestream(self):
        await self.look_for_new_content(ContentType.STREAMS)

    @tasks.loop(seconds=60*5)
    async def check_yt_video(self):
        await self.look_for_new_content(ContentType.VIDEOS)
        await self.look_for_new_content(ContentType.SHORTS)

def setup(bot:Bot):
    bot.add_cog(Youtube(bot))
    