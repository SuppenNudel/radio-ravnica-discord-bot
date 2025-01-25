from ezcord import Cog, log
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot

class Ping(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    @slash_command(description="Ping the bot")
    async def ping(self, ctx:ApplicationContext):
        await ctx.send_response("Pong", ephemeral="True")


def setup(bot:Bot):
    bot.add_cog(Ping(bot))
