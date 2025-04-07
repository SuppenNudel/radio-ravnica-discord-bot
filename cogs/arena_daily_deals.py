from ezcord import log, Cog
from discord.ext.commands import slash_command, has_role, has_permissions
from discord import ApplicationContext, Bot, default_permissions, InteractionContextType, IntegrationType
import discord.ext.tasks
from modules import bluesky
import os
import logging

link_log = logging.getLogger("link_logger")

BSKY_ARENA_DAILY_DEALS_HANDLE = "arenadailydeals.bsky.social"  # The user you want to monitor
CHANNEL_ID_ARENA = int(os.getenv("CHANNEL_ID_ARENA"))

class ArenaDailyDeals(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.latest_post = None

    @Cog.listener()
    async def on_ready(self):
        guilds_str = "This Bot is installed on the following Servers:"
        for guild in self.bot.guilds:
            guilds_str += f"\n{repr(guild)}"
            if guild.id != 783441128119730236: # RR Server
                link_log.info(f"Owner: {guild.owner.mention}\nIcon: {guild.icon.url if guild.icon else 'No icon'}")
        log.info(guilds_str)
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
        if self.latest_post and not latest_post.uri == self.latest_post.uri:
            image_urls = [image.fullsize for image in latest_post.embed.images]
            channel:discord.TextChannel = await discord.utils.get_or_fetch(self.bot, "channel", CHANNEL_ID_ARENA)
            for image_url in image_urls:
                await channel.send(image_url) #embeds=embeds)
        self.latest_post = latest_post

def setup(bot:Bot):
    bot.add_cog(ArenaDailyDeals(bot))
