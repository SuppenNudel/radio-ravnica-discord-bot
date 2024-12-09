from discord.ext import commands, tasks
import os
from ezcord import Bot
import scrapetube
from ezcord import log


class Youtube(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot:Bot = bot
        self.channels = {
            "<@270288996666441728>": f"https://youtube.com/@gamerii"
        }
        self.videos = {}
        channel_id_str = os.getenv("CHANNEL_YOUTUBE")
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error("The environment variable 'CHANNEL_YOUTUBE' is not a valid integer.")

    @commands.Cog.listener()
    async def on_ready(self):
        self.check.start()

    @tasks.loop(seconds=60)
    async def check(self):
        discord_channel = self.bot.get_channel(self.channel_id)

        for channel_name in self.channels:
            videos = scrapetube.get_channel(channel_url=self.channels[channel_name], limit=5)
            video_ids = [video["videoId"] for video in videos]

            # load up on latest videos
            # ignore initial pull
            if self.check.current_loop == 0:
                self.videos[channel_name] = video_ids
                continue

            # post videos that have not been saved on self.videos yet
            for video_id in video_ids:
                if video_id not in self.videos[channel_name]:
                    url = f"https://youtu.be/{video_id}"
                    await discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>, neues Video von **{channel_name}**\n\n{url}")

            self.videos[channel_name] = video_ids


def setup(bot):
    bot.add_cog(Youtube(bot))