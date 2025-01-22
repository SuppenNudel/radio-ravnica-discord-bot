from discord.ext import commands, tasks
import os
import discord
from discord import Bot
import scrapetube
from ezcord import log

class Youtube(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.channels = {
            "<@270288996666441728>": f"https://youtube.com/@gamerii"
        }
        self.videos = {"<@270288996666441728>": []}
        self.streams = {"<@270288996666441728>": []}
        channel_id_str = os.getenv("CHANNEL_YOUTUBE")
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error("The environment variable 'CHANNEL_YOUTUBE' is not a valid integer.")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.check_yt_video.is_running():
            self.check_yt_video.start()
        if not self.check_yt_livestream.is_running():
            self.check_yt_livestream.start()
        log.debug(self.__class__.__name__ + " is ready")

    def add_unique_with_limit(self, item, target_list:list, limit=5):
        if item not in target_list:
            if len(target_list) >= limit:
                target_list.pop(0)  # Remove the oldest item to make space
            target_list.append(item)

    def get_list(self, content_type, channel_name):
        if content_type == "video":
            return self.videos[channel_name]
        if content_type == "stream":
            return self.streams[channel_name]
        raise Exception(f"Invalid content type {content_type}")

    async def handle_video(self, loop, discord_channel:discord.TextChannel, channel_name, video, content_type):
        video_id = video['videoId']

        video_list = self.get_list(content_type, channel_name)

        if loop.current_loop == 0:
            self.add_unique_with_limit(video_id, video_list)
            return
        
        in_list = video_id in video_list
        has_badges = 'badges' in video
        if has_badges:
            return
        if not in_list:
            url = f"https://youtu.be/{video_id}"
            if content_type == "video":
                await discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneues Video von **{channel_name}**\n\n{url}")
            elif content_type == "stream":
                await discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\n{channel_name} streamt gleich! Kommt, schaut vorbei!\n\n{url}")
            self.add_unique_with_limit(video_id, video_list)

    @tasks.loop(seconds=60)
    async def check_yt_livestream(self):
        discord_channel = self.bot.get_channel(self.channel_id)
        if not type(discord_channel) == discord.TextChannel:
            log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
            return
        
        for channel_name in self.channels:
            streams = scrapetube.get_channel(channel_url=self.channels[channel_name], limit=3, content_type="streams")
            stream_list = list(streams)[::-1]
            
            for video in stream_list:
                await self.handle_video(self.check_yt_video, discord_channel, channel_name, video, "stream")

    @tasks.loop(seconds=60*5)
    async def check_yt_video(self):
        discord_channel = self.bot.get_channel(self.channel_id)
        if not type(discord_channel) == discord.TextChannel:
            log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
            return
        
        for channel_name in self.channels:
            videos = scrapetube.get_channel(channel_url=self.channels[channel_name], limit=5)

            video_list = list(videos)[::-1]
            
            # post videos that have not been saved on self.videos yet
            for video in video_list:
                await self.handle_video(self.check_yt_livestream, discord_channel, channel_name, video, "video")

def setup(bot:Bot):
    bot.add_cog(Youtube(bot))
    