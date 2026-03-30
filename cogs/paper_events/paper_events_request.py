from ezcord import Cog, log
from discord import Bot

class PaperEventsRequest(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.info(self.__class__.__name__ + " is ready")

def setup(bot:Bot):
    bot.add_cog(PaperEventsRequest(bot))
