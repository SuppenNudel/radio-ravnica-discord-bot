from ezcord import log, Cog
from discord.ext.commands import slash_command, has_role, has_permissions
from discord import ApplicationContext, Bot, default_permissions, InteractionContextType, IntegrationType

class Ping(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    # Weitere Checks https://gist.github.com/Painezor/eb2519022cd2c907b56624105f94b190
    @slash_command(
        description="Ping the bot",
        integration_type={IntegrationType.user_install},
        contexts={InteractionContextType.bot_dm} #, InteractionContextType.private_channel
    )
    @has_permissions(manage_guild=True)
    # @has_role("Moderator") 
    async def ping(self, ctx:ApplicationContext):
        await ctx.send_response("Pong", ephemeral="True")

    @Cog.listener()
    async def on_application_command_error(self, ctx:ApplicationContext, error):
        log.error(error)
        await ctx.respond(f"Es ist ein Fehler aufgetreten: ```{error}```", ephemeral=True)
        raise error


def setup(bot:Bot):
    bot.add_cog(Ping(bot))
