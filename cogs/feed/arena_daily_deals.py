import asyncio
from atproto_client.exceptions import NetworkError
from ezcord import log, Cog
from discord.ext.commands import slash_command, has_role, has_permissions
from discord import ApplicationContext, Bot, default_permissions, InteractionContextType, IntegrationType
import discord.ext.tasks
from modules import bluesky
import os
import logging
import re

link_log = logging.getLogger("link_logger")

BSKY_ARENA_DAILY_DEALS_HANDLE = "notarenadailydeals.bsky.social"  # The user you want to monitor
CHANNEL_ID_ARENA = int(os.getenv("CHANNEL_ID_ARENA"))
ROLE_ID_ARENA_DEALS = 1557669813227233310
DEAL_HASHTAG_PATTERN = re.compile(r"(?<!\w)#(?:gold|draftToken)\b", re.IGNORECASE)

class ArenaDailyDeals(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot
        self.latest_post = None
        self.author_did: str | None = None

    @Cog.listener()
    async def on_ready(self):
        # guilds_str = "This Bot is installed on the following Servers:"
        # for guild in self.bot.guilds:
        #     guilds_str += f"\n{repr(guild)}"
        #     if guild.id != 783441128119730236: # RR Server
        #         link_log.info(f"Owner: {guild.owner.mention}\nIcon: {guild.icon.url if guild.icon else 'No icon'}")
        # log.info(guilds_str)
        
        if not self.check_bsky_posts.is_running():
            self.check_bsky_posts.start()
            
        log.info(self.__class__.__name__ + " is ready")

    async def get_latest_post(self):
        try:
            if self.author_did is None:
                self.author_did = await asyncio.to_thread(
                    bluesky.get_target_did, BSKY_ARENA_DAILY_DEALS_HANDLE
                )
            latest_post = await asyncio.to_thread(bluesky.check_for_new_post, self.author_did)
            return latest_post
        except NetworkError as error:
            log.warning(
                "Bluesky network request failed (handle=%s, did=%s, error=%s, status=%s); "
                "retrying on the next poll in 15 minutes",
                BSKY_ARENA_DAILY_DEALS_HANDLE,
                self.author_did,
                type(error).__name__,
                error.response.status_code if error.response is not None else None,
            )
            return None
        except Exception:
            log.exception(
                "Failed latest-post check (source=bluesky, handle=%s, did=%s)",
                BSKY_ARENA_DAILY_DEALS_HANDLE,
                self.author_did,
            )
            return None

    @discord.ext.tasks.loop(minutes=15)
    async def check_bsky_posts(self):
        try:
            latest_post = await self.get_latest_post()
            if latest_post is None:
                return

            if self.latest_post and not latest_post.uri == self.latest_post.uri:
                embed = getattr(latest_post, "embed", None)
                images = getattr(embed, "images", None) if embed else None
                image_urls = [image.fullsize for image in images if getattr(image, "fullsize", None)] if images else []
                record = getattr(latest_post, "record", None)
                post_text = getattr(record, "text", "") or ""
                notify_role = DEAL_HASHTAG_PATTERN.search(post_text) is not None

                if not image_urls:
                    log.warning(
                        "New Bluesky post has no images to send (handle=%s, uri=%s)",
                        BSKY_ARENA_DAILY_DEALS_HANDLE,
                        getattr(latest_post, "uri", "unknown"),
                    )
                if image_urls or notify_role:
                    channel:discord.TextChannel = await discord.utils.get_or_fetch(self.bot, "channel", CHANNEL_ID_ARENA)
                    if notify_role:
                        await channel.send(
                            f"<@&{ROLE_ID_ARENA_DEALS}>",
                            allowed_mentions=discord.AllowedMentions(
                                roles=[discord.Object(id=ROLE_ID_ARENA_DEALS)],
                                users=False,
                                everyone=False,
                                replied_user=False,
                            ),
                        )
                    for image_url in image_urls:
                        await channel.send(image_url) #embeds=embeds)

            self.latest_post = latest_post
        except Exception:
            log.exception(
                "Unexpected error in Bluesky daily deals loop (handle=%s, did=%s)",
                BSKY_ARENA_DAILY_DEALS_HANDLE,
                getattr(self, "author_did", None),
            )

def setup(bot:Bot):
    bot.add_cog(ArenaDailyDeals(bot))
