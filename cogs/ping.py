from discord import Bot
from discord.ext import commands
import discord

class Ping(commands.Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print("ping is ready")

    @commands.slash_command()
    async def ping(self, ctx:discord.commands.ApplicationContext):
        await ctx.send_response("Pong", ephemeral="True")


def setup(bot:Bot):
    bot.add_cog(Ping(bot))