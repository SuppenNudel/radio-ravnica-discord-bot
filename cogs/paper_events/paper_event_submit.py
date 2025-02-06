from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, InteractionContextType, IntegrationType
import discord
from modules import date_time_interpretation as dti
import os

CHANNEL_PAPER_EVENTS = int(os.getenv("CHANNEL_PAPER_EVENTS"))

class PaperTurnier():
    def __init__(self, title):
        self.title = title
        self.description = None
        self.time = None
        self.author:discord.Member|discord.User|None = None
    
    def create_content(self):
        content = ""
        if self.description:
            quoted_freitext = '\n'.join([f"> {line}" for line in self.description.split('\n')])
            content += f"{quoted_freitext}\n\n"
        content += f"Danke an {self.author.mention} für's Posten!"
        return content

    def create_info_embed(self):
        embed = discord.Embed(
            title=self.title,
            # description=self.description,
            color=discord.Color.from_rgb(37, 88, 79),  # You can change the embed color
            # timestamp=self.time
        )
        if self.author:
            embed.set_author(name=self.author.display_name, icon_url=self.author.avatar.url)
        if self.time:
            formatted_time = discord.utils.format_dt(self.time)
            embed.add_field(name="Start", value=formatted_time)
        return embed

def check_factory(user):
    def check(m):
        return m.author == user and isinstance(m.channel, discord.DMChannel)
    return check
    
class EditTurnierView(discord.ui.View):
    def __init__(self, turnier:PaperTurnier, guild):
        super().__init__()
        self.turnier = turnier
        self.guild = guild

    options = [
        discord.SelectOption(label="Titel", description="", emoji="🚩", value="title"),
        discord.SelectOption(label="Zeit", description="Wann das Event startet", emoji="⏱️", value="time"),
        discord.SelectOption(label="Beschreibung", description="", emoji="🗒️", value="description"),
    ]
    @discord.ui.select(
        min_values=1,
        max_values=1,
        placeholder="Was möchtest du bearbeiten?",
        options=options
    )
    async def select_callback(self, select:discord.ui.Select, interaction:discord.interactions.Interaction):
        await interaction.response.defer()
        self.disable_all_items()
        self.stop()
        await interaction.followup.edit_message(interaction.message.id, view=self) # oder einfach löschen

        selection_value = select.values[0]
        selection_label = next((opt.label for opt in self.options if opt.value == selection_value), selection_value)
        await interaction.followup.send(content=f"Ändere {selection_label}:\n")

        user:discord.User = interaction.user
        bot:Bot = interaction.client
        try:
            event_response = await bot.wait_for("message", check=check_factory(user))
            new_value = event_response.content
            if selection_value == "time":
                new_value = dti.parse_date(new_value)
            setattr(self.turnier, selection_value, new_value)

            # show the author how it looks now
            await user.send(content=self.turnier.create_content(), embed=self.turnier.create_info_embed(), view=EditTurnierView(self.turnier, self.guild))
        except TimeoutError as e:
            await user.send("Du hast dir leider zu viel Zeit gelassen took too long to respond. Please try again with `/create_event`.")

    @discord.ui.button(
        label="Turnier einsenden",
        style=discord.ButtonStyle.primary,
    )
    async def button_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        self.disable_all_items()
        self.stop()
        await interaction.followup.edit_message(interaction.message.id, view=self) # oder einfach löschen
        
        guild = self.guild
        forum_channel = await discord.utils.get_or_fetch(guild, "channel", CHANNEL_PAPER_EVENTS, default=None)

        if not isinstance(forum_channel, discord.ForumChannel):
            await interaction.response.send_message("This is not a forum channel!", ephemeral=True)
            return
        

        # Create a new thread (forum post)
        thread = await forum_channel.create_thread(
            content=self.turnier.create_content(),
            name="New Forum Post Title",  # Change this to your post title
            # content="This is the forum post content.",  # First message in the post
            applied_tags=[],  # Optional: List of tag IDs if the forum has tags
            embed=self.turnier.create_info_embed()
        )

        await interaction.followup.send(f"Forum post created: {thread.jump_url}")

class PaperEventSubmit(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    # Weitere Checks https://gist.github.com/Painezor/eb2519022cd2c907b56624105f94b190
    @slash_command(
        description="Reiche ein Paper Turnier/Event ein",
        integration_type={IntegrationType.user_install},
        contexts={InteractionContextType.bot_dm} #, InteractionContextType.private_channel
    )
    # @has_role("Moderator") 
    async def poste_turnier(self, ctx:ApplicationContext, titel:str|None=None):
        turnier = PaperTurnier(titel)
        turnier.author = ctx.author

        try:
            user = ctx.author
            direct_message = await user.send(
                content="Lass uns zusammen das Turnier erstellen",
                embed=turnier.create_info_embed()
            )
            
            await ctx.send_response(f"Lass uns zusammen in den Direktnachrichten das Turnier erstellen: {direct_message.jump_url}", ephemeral=True)
            test = await user.send("Was möchtest du bearbeiten?", view=EditTurnierView(turnier, ctx.guild))
            pass
        except discord.Forbidden:
            await ctx.send_response("Ich konnte dich nicht direkt anschreiben! Bitte erlaube Direktnachrichten von Mitgliedern des Servers.", ephemeral=True)

    @Cog.listener()
    async def on_application_command_error(self, ctx:ApplicationContext, error):
        log.error(error)
        await ctx.respond(f"Es ist ein Fehler aufgetreten: ```{error}```", ephemeral=True)
        raise error


def setup(bot:Bot):
    bot.add_cog(PaperEventSubmit(bot))
