from modules import instagram
from discord.ext import commands, tasks
import os
import discord
from discord import Bot
from ezcord import log
from modules import env
import json

MOCK = True  # Set to True to use mock data from ig_return.json

class InstagramProfile():
    def __init__(self, name:str, dc_user_id:int):
        self.name = name
        self.dc_user_id = dc_user_id
        self.content_cache = []

class InstagramMonitor(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.channels = [
            InstagramProfile("gamerii93", 270288996666441728)
        ]

        channel_id_str = os.getenv("CHANNEL_INSTAGRAM")
        if channel_id_str is not None:
            try:
                self.channel_id = int(channel_id_str)
            except ValueError:
                log.error("The environment variable 'CHANNEL_INSTAGRAM' is not a valid integer.")

    @commands.slash_command(name="share_instagram_post", description="Share an Instagram post by ID or URL", default_member_permissions=discord.Permissions(manage_guild=True))
    async def share_instagram_post(self, ctx: discord.ApplicationContext, post: str):
        # Restrict command to user with ID 356120044754698252
        if ctx.user.id != 356120044754698252:
            await ctx.respond("Du hast keine Berechtigung, diesen Befehl zu verwenden.", ephemeral=True)
            return

        await ctx.defer()
        try:
            # Extract post ID if a URL is provided
            if post.startswith("http"):
                post_id = instagram.extract_post_id(post)
            else:
                post_id = post

            post_data = instagram.get_post_by_id(post_id, apify_token=os.getenv("API_KEY_INSTAGRAM"))
            if not post_data:
                await ctx.respond("Kein Beitrag gefunden für diese ID/URL.", ephemeral=True)
                return

            # Use the first channel as the author for the post
            ig_channel = self.channels[0]
            await self.post_content(ig_channel, post_data)
            await ctx.respond("Instagram-Post wurde geteilt.", ephemeral=True)
        except Exception as e:
            log.error(f"Fehler beim Teilen des Instagram-Posts: {e}")
            await ctx.respond("Fehler beim Teilen des Instagram-Posts.", ephemeral=True)

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

    async def post_content(self, ig_channel:InstagramProfile, new_post):
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

    async def get_latest_posts(self, channel:InstagramProfile):
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
    