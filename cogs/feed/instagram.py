from modules import instagram
from discord.ext import commands, tasks
import os
import discord
from discord import Bot
from ezcord import log
from modules import env
import json

MOCK = False  # Set to True to use mock data from ig_return.json

class InstagramChannel():
    def __init__(self, name:str, dc_user_id:int):
        self.name = name
        self.dc_user_id = dc_user_id
        self.content_cache = []

class InstagramMonitor(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.channels = [
            InstagramChannel("gamerii93", 270288996666441728)
        ]

        channel_id_str = os.getenv("CHANNEL_INSTAGRAM")
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error("The environment variable 'CHANNEL_INSTAGRAM' is not a valid integer.")


    @commands.Cog.listener()
    async def on_ready(self):
        discord_channel = await discord.utils.get_or_fetch(self.bot, 'channel', self.channel_id)
        if not type(discord_channel) == discord.TextChannel:
            log.error(f"type of discord_channel is not discord.TextChannel, but {type(discord_channel)}")
            raise Exception("discord_channel is not a discord.TextChannel")
        self.discord_channel:discord.TextChannel = discord_channel

        log.debug(self.__class__.__name__ + " is ready")

        if not self.check_channels.is_running():
            self.check_channels.start()

    async def post_content(self, ig_channel:InstagramChannel, new_post):
        caption = new_post.get("caption")
        log.info(f"Poste neuen Instagram Inhalt:\n{caption}")
        url = new_post.get("url")
        pinned = new_post.get("isPinned")
        type = new_post.get("type")
        video_url = new_post.get("videoUrl")
        tagged_users = new_post.get("taggedUsers")
        # await self.discord_channel.send(f"<@&{os.getenv('ROLE_ANNOUNCEMENT')}>\nneuer Instagram Post von **<@{ig_channel.dc_user_id}>**\n\n{url}")

        quoted_caption = '\n'.join([f"> {line}" for line in caption.split('\n')])
        header = f"📷 Neuer Instagram Post von **<@{ig_channel.dc_user_id}>**\n <{url}>\n{quoted_caption}"
        if type == "Video":
            await self.discord_channel.send(content=f"{header}\n[Video]({video_url})")
        elif type == "Sidecar":
            images = new_post.get("images")
            if len(images) == 1:
                await self.discord_channel.send(content=f"{header}\n{images[0]}")
            else:
                await self.discord_channel.send(content=f"{header}")
                for image in images:
                    await self.discord_channel.send(content=f"{image}")
        elif type == "Image":
            display_url = new_post.get("displayUrl")
            display_url = display_url.replace("https://instagram.fosu2-1.fna.fbcdn.net", "https://scontent-dus1-1.cdninstagram.com")
            await self.discord_channel.send(content=f"{header}\n{display_url}")

    async def get_latest_posts(self, channel:InstagramChannel):
        latest_posts = None
        if MOCK:
            with open("ig_return.json", "r", encoding="utf-8") as f:
                posts = json.load(f)
            latest_posts = [post for post in posts if post not in channel.content_cache]
        else:
            posts = instagram.get_latest_instagram_posts(channel.name, max_posts=5, apify_token=os.getenv("API_KEY_INSTAGRAM"))
            latest_posts = [post for post in posts if post not in channel.content_cache]
        return latest_posts

    @tasks.loop(hours=12)
    async def check_channels(self):
        for ig_channel in self.channels:
            latest_posts = await self.get_latest_posts(ig_channel)
            if self.check_channels.current_loop == 0:
                log.info("Initializing content cache for Instagram channel")
                ig_channel.content_cache.extend(post['id'] for post in latest_posts)
                continue
            for post in latest_posts:
                if post['id'] in ig_channel.content_cache:
                    pass
                    # log.debug(f"Post already in cache: {post.get('id')}")
                else:
                    await self.post_content(ig_channel, post)
                    ig_channel.content_cache.append(post['id'])

def setup(bot:Bot):
    bot.add_cog(InstagramMonitor(bot))
    