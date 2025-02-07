from ezcord import log, Cog
from discord.ext.commands import slash_command, has_role, has_permissions
from discord import ApplicationContext, Bot, default_permissions, InteractionContextType, IntegrationType
import discord.ext.tasks
from modules import bluesky
import os

BSKY_ARENA_DAILY_DEALS_HANDLE = "arenadailydeals.bsky.social"  # The user you want to monitor
CHANNEL_ID_ARENA = int(os.getenv("CHANNEL_ID_ARENA"))

class ArenaDailyDeals(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.latest_post = None

    @Cog.listener()
    async def on_ready(self):
        self.author_did = bluesky.get_target_did(BSKY_ARENA_DAILY_DEALS_HANDLE)

        if not self.check_bsky_posts.is_running():
            self.check_bsky_posts.start()
            
        log.debug(self.__class__.__name__ + " is ready")

    async def get_latest_post(self):
        try:
            latest_post = bluesky.check_for_new_post(self.author_did)
            return latest_post
        except Exception as e:
            log.error(e)

    @discord.ext.tasks.loop(minutes=15)
    async def check_bsky_posts(self):
        latest_post = await self.get_latest_post()
        if self.check_bsky_posts.current_loop == 0:
            self.latest_post = latest_post
        elif latest_post.uri == self.latest_post.uri:
            rkey = latest_post.uri.split("/")[-1]
            profile_identifier = BSKY_ARENA_DAILY_DEALS_HANDLE or self.author_did  # Use handle if available
            post_url = f"https://bsky.app/profile/{profile_identifier}/post/{rkey}"
            log.debug(f"New post: {post_url}")
            
            channel:discord.TextChannel = await discord.utils.get_or_fetch(self.bot, "channel", CHANNEL_ID_ARENA)
            await channel.send(post_url)

def setup(bot:Bot):
    bot.add_cog(ArenaDailyDeals(bot))
