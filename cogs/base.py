import discord
from discord.ext import commands
from discord.commands import slash_command
from discord import ApplicationContext
import os
from ezcord import log

class Base(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @slash_command(description="hello", guild_ids=[os.getenv("TEST_GUILD")])
    async def hello(self, ctx: discord.ApplicationContext):
        await ctx.respond(f"Hey {ctx.author.mention}")

    @slash_command(description="error test", guild_ids=[os.getenv("TEST_GUILD")])
    async def error(self, ctx:ApplicationContext):
        await ctx.respodn("Hey")


def setup(bot: discord.Bot):
    bot.add_cog(Base(bot))
