from ezcord import Cog, log
from discord.ext.commands import slash_command, has_role
from discord import ApplicationContext, Bot, Embed, Color
import discord
from modules import date_time_interpretation

class SpelltableEvent():
    def __init__(self, title):
        self.title = title
        self.description = None
        self.time = None
        self.manager:discord.Member|None = None

    def to_embed(self):
        embed = Embed(
            title=self.title,
            description=self.description,
            color=Color.from_rgb(37, 88, 79),  # You can change the embed color
            # author=self.manager,
            timestamp=self.time
        )
        if self.manager:
            embed.set_author(name=self.manager.display_name, icon_url=self.manager.avatar.url)
        if self.time:
            formatted_time = discord.utils.format_dt(self.time)
            embed.add_field(name="Start", value=formatted_time)
        return embed

class SpelltableEventManager(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    @has_role("Moderator")
    @slash_command(description="Erstelle ein Spelltable Turnier für den Server")
    async def erstelle_turnier(self, ctx:ApplicationContext, titel:str, manager:discord.Member):
        event = SpelltableEvent(titel)
        event.manager = manager
        try:
            user = ctx.author
            direct_message = await user.send(
                content="Lass uns zusammen das Turnier erstellen",
                embed=event.to_embed()
            )
            def check(m):
                return m.author == user and isinstance(m.channel, discord.DMChannel)
            
            await ctx.send_response(f"Lass uns zusammen in den Direktnachrichten das Turnier erstellen: {direct_message.jump_url}", ephemeral=True)

            for key, value in vars(event).items():
                if not value:
                    await user.send(f"Gib mir ein(e) {key} für das Event:")
                    event_response = await self.bot.wait_for("message", check=check, timeout=60)
                    new_value = event_response.content
                    if key == "time":
                        new_value = date_time_interpretation.parse_date(new_value)
                    setattr(event, key, new_value)
                    await user.send(embed=event.to_embed())
            
            # await user.send(f"Great! '{event_name.content}' is happening on '{event_time.content}'. Event created!")
            # event_time = await self.bot.wait_for("message", check=check, timeout=60)
        except discord.Forbidden:
            await ctx.respond("I couldn't DM you! Please enable direct messages from server members.")
        except TimeoutError as e:
            await user.send("You took too long to respond. Please try again with `/create_event`.")

        # reply_message = await self.bot.wait_for("message", check=check)
        # await ctx.send_followup(reply_message.content, ephemeral=True)


def setup(bot:Bot):
    bot.add_cog(SpelltableEventManager(bot))
