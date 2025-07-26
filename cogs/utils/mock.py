from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot
import random

def to_mocking_text(text):
    return ''.join(
        c.upper() if random.choice([True, False]) else c.lower()
        for c in text
    )


class Mock(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    # Weitere Checks https://gist.github.com/Painezor/eb2519022cd2c907b56624105f94b190
    @slash_command(
        description="Sende eine Nachricht im Mock-Stil",
    )
    async def mock(self, ctx:ApplicationContext, text: str):
        mock_text = to_mocking_text(text)
        await ctx.send_response(mock_text)

def setup(bot:Bot):
    bot.add_cog(Mock(bot))
