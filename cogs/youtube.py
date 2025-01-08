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
        self.check.start()
        self.check_livestream.start()

    def add_unique_with_limit(self, item, target_list:list, limit=5):
        if item not in target_list:
            if len(target_list) >= limit:
                target_list.pop(0)  # Remove the oldest item to make space
            target_list.append(item)

    async def handle_video(self, discord_channel:discord.TextChannel, channel_name, video, content_type):
        video_id = video['videoId']

        if self.check.current_loop == 0:
            self.add_unique_with_limit(video_id, self.videos[channel_name])
            return
        
        in_list = video_id in self.videos[channel_name]
        has_badges = 'badges' in video
        if has_badges:
            return
        if not in_list and not has_badges:
            url = f"https://youtu.be/{video_id}"
            if content_type == "video":
                await discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneues Video von **{channel_name}**\n\n{url}")
                self.add_unique_with_limit(video_id, self.videos[channel_name])
            elif content_type == "stream":
                await discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\n{channel_name} streamt gleich! Kommt, schaut vorbei!\n\n{url}")
        # return video_id

    @tasks.loop(seconds=60)
    async def check_livestream(self):
        for channel_name in self.channels:
            streams = scrapetube.get_channel(channel_url=self.channels[channel_name], limit=2, content_type="streams")
            stream_list = list(streams)[::-1]
            for stream in stream_list:
                video_id = stream['videoId']
                if self.check_livestream.current_loop == 0:
                    self.add_unique_with_limit(video_id, self.streams[channel_name])
                    continue
                if video_id in self.streams[channel_name]:
                    continue
                log.info(f"Posting Stream with id {video_id}")
                discord_channel = self.bot.get_channel(self.channel_id)
                if not type(discord_channel) == discord.TextChannel:
                    log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
                    continue
                await discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\n{channel_name} streamt gleich! Kommt, schaut vorbei!\n\n{f"https://youtu.be/{video_id}"}")

    @tasks.loop(seconds=60*5)
    async def check(self):
        discord_channel = self.bot.get_channel(self.channel_id)
        if not type(discord_channel) == discord.TextChannel:
            log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
            return
        
        for channel_name in self.channels:
            videos = scrapetube.get_channel(channel_url=self.channels[channel_name], limit=5)

            video_list = list(videos)[::-1]

            # post videos that have not been saved on self.videos yet
            for video in video_list:
                await self.handle_video(discord_channel, channel_name, video, "video")

def setup(bot:Bot):
    bot.add_cog(Youtube(bot))