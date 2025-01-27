from ezcord import Cog, log
from discord.ext.commands import slash_command, has_role
from discord import ApplicationContext, Bot, Embed, Color

class SpelltableEventManager(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(self, ctx:ApplicationContext, titel:str):
        await ctx.send_response(f"Pong {titel}", ephemeral=True)
        embed = Embed(
            title=titel,
            description="Beschreibung",
            color=Color.from_rgb(37, 88, 79),  # You can change the embed color
        )
        await ctx.send(content="Hallo", embed=embed)
        reply_message = self.bot.wait_for("message", check=lambda message: message.author == ctx.author)
        ctx.send_response(reply_message.content, ephemeral=True)


def setup(bot:Bot):
    bot.add_cog(SpelltableEventManager(bot))
