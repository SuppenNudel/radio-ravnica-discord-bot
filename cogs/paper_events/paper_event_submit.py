from ezcord import log, Cog
from discord.ext.commands import slash_command
from discord import ApplicationContext, Bot, InteractionContextType, IntegrationType
import discord
from modules import date_time_interpretation as dti
from modules import notion, env, gmaps
from typing import Optional
from datetime import datetime
from enum import Enum
from cogs.paper_events.paper_events_common import PaperEvent

CHANNEL_PAPER_EVENTS_ID = env.CHANNEL_PAPER_EVENTS_ID
EVENT_DATABASE_ID = env.EVENT_DATABASE_ID
DEBUG = env.DEBUG

class FieldType:
    def __init__(self, icon, text, parser=lambda message: message.content.strip()):
        self.icon = icon
        self.parser = parser
        self.text = text

    def parse(self, value):
        return self.parser(value) if value else None
    
def parse_image(message:discord.Message):
    attachments = message.attachments
    if len(attachments) == 0:
        raise Exception("Bitte füge ein Bild hinzu")
    if len(attachments) > 1:
        raise Exception("Bitte füge nur *ein* Bild hinzu")
    if len(attachments) == 1:
        the_image = attachments[0]
        content_type = the_image.content_type
        if content_type and content_type.startswith("image/"):
            return the_image.url
    raise Exception("Invalider Dateityp: Kein Bild")

def parse_location(message:discord.Message) -> gmaps.Location:
    content = message.content
    location = gmaps.get_location(content)
    return location

# Defining field types
FIELD_TYPE_TIME = FieldType("🕒", "Bitte nenne einen Zeipunkt", lambda message: dti.parse_date(message.content) if message.content else None)
FIELD_TYPE_TEXT = FieldType("📝", "Bitte gib einen Text ein")
FIELD_TYPE_FORMAT = FieldType("📝", "Bitte gib ein Format ein")
FIELD_TYPE_NUMBER = FieldType("🔢", "Bite gib eine Zahl ein", lambda message: int(message.content) if message.content.isdigit() else None)
# FIELD_TYPE_EMAIL = FieldType("✉️", "Gib eine Email ein", lambda x: x if "@" in x else None)
FIELD_TYPE_IMAGE = FieldType("🖼️", "Bitte lade ein Bild hoch", parse_image)
FIELD_TYPE_LOCATION = FieldType("📍", "Bitte gib einen Ort an", parse_location)

class FieldName(Enum):
    TYPE = "Typ"
    TITLE = "Titel"
    START = "Start"
    LOCATION = "Ort (Stadt und Laden)"
    FORMAT = "Format"
    END = "Ende"
    FEE = "Teilnahmegebühren"
    DESCRIPTION = "Beschreibung / Freitext"
    URL = "URL / Link"
    IMAGE = "Bild"

class InputField:
    def __init__(self, name: FieldName, field_type: FieldType, mandatory: bool = False, icon: Optional[str] = None):
        self.name = name
        self.field_type = field_type
        self.mandatory = mandatory
        self._value = None
        self.custom_icon = icon  # Custom icon, if provided

    @property
    def status_emoji(self) -> str:
        """Returns the status emoji based on the field's state."""
        if self.value:
            return "✅"
        return "❌" if self.mandatory else "⚠️"

    @property
    def icon(self) -> str:
        """Returns the custom icon if provided, otherwise falls back to the default."""
        return self.custom_icon or self.field_type.icon

    @property
    def label(self) -> str:
        """Returns the formatted label for the select menu."""
        return f"{self.status_emoji} {self.name.value}"
    
    @property
    def value(self):
        return self._value
    
    @value.setter
    def value(self, new_value):
        self._value = self.field_type.parse(new_value)

class Tourney():
    def __init__(self, guild, author):
        self.guild = guild
        self.author:discord.Member = author
        field_list = [
            InputField(FieldName.TITLE, FIELD_TYPE_TEXT, icon="📢"),  # Optional field
            InputField(FieldName.DESCRIPTION, FIELD_TYPE_TEXT, icon="📜"),
            InputField(FieldName.START, FIELD_TYPE_TIME, mandatory=True, icon="⏰"),  # Custom icon for time
            InputField(FieldName.END, FIELD_TYPE_TIME, icon="⏳"),  # Optional field
            InputField(FieldName.LOCATION, FIELD_TYPE_LOCATION, mandatory=True, icon="📍"),
            InputField(FieldName.FEE, FIELD_TYPE_NUMBER, icon="💸"),
            InputField(FieldName.FORMAT, FIELD_TYPE_TEXT, icon="🎮"),  # Optional field with custom icon
            InputField(FieldName.TYPE, FIELD_TYPE_TEXT),
            InputField(FieldName.URL, FIELD_TYPE_TEXT, icon="🔗"),
            InputField(FieldName.IMAGE, FIELD_TYPE_IMAGE, icon="🖼️"),  # Image upload
        ]

        self.fields = self.fields = {field.name: field for field in field_list}

    def create_content(self):
        description:str|None = self.fields[FieldName.DESCRIPTION].value
        content = ""
        if description:
            content += "\n".join(f"> {line}" for line in description.splitlines())
        return f"{content}"
    
    def create_gmap_embed(self) -> discord.Embed|None:
        paper_event = PaperEvent(
            self.author,
            title=self.fields[FieldName.TITLE].value,
            format=self.fields[FieldName.FORMAT].value,
            event_type=self.fields[FieldName.TYPE].value,
            location=self.fields[FieldName.LOCATION].value
        )
        return paper_event.construct_gmaps_embed()

    def create_embed(self) -> discord.Embed:
        title:str|None = self.fields[FieldName.TITLE].value
        paper_event = PaperEvent(
            self.author,
            title=self.fields[FieldName.TITLE].value,
            format=self.fields[FieldName.FORMAT].value,
            event_type=self.fields[FieldName.TYPE].value,
            location=self.fields[FieldName.LOCATION].value
        )

        embed = discord.Embed(color=env.RR_GREEN)
        embed.title = title

        for field_name in [FieldName.START, FieldName.LOCATION, FieldName.FORMAT, FieldName.FEE]:
            field = self.fields[field_name]
            if field.value:
                value = field.value
                if field.field_type == FIELD_TYPE_TIME and type(field.value) == datetime:
                    value = discord.utils.format_dt(field.value, "f")
                embed.add_field(name=field_name.value, value=value, inline=True)

        return paper_event.construct_event_embed()

def check_factory(user):
    def check(m:discord.Message):
        return m.author == user and isinstance(m.channel, discord.DMChannel)
    return check

class SubmitButton(discord.ui.Button):
    """Custom button for submitting the tournament."""
    def __init__(self):
        super().__init__(label="Turnier einsenden", style=discord.ButtonStyle.primary, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        """Handles the submission when clicked."""
        view: EditTourneyView = self.view  # Get the parent view
        view.disable_all_items()
        view.stop()
        await interaction.followup.edit_message(view.message.id, view=view)

        # Ensure all mandatory fields are filled
        if not all(field.value for field in view.tourney.fields.values() if field.mandatory):
            await interaction.response.send_message("Not all mandatory fields are filled!", ephemeral=True)
            return
        
        if type(interaction.channel) == discord.DMChannel:
            await view.send_forum_post(interaction)
        else:
            raise Exception("interaction.channel is not a DMChannel")

class FieldSelect(discord.ui.Select):
    """Custom select menu handling field selection."""
    def __init__(self, fields:dict[FieldName, InputField]):
        self.fields = fields
        super().__init__(
            placeholder="Wähle eine Eigenschaft, die du bearbeiten möchtest...",
            min_values=1,
            max_values=1,
            options=self.get_options()
        )

    def get_options(self):
        """Generates the options dynamically based on field status."""
        return [
            discord.SelectOption(label=field.label, value=field.name.name, emoji=field.custom_icon)
            for field in self.fields.values()
        ]

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not self.view and not type(self.view) == EditTourneyView:
            raise Exception("view is None")
        view:EditTourneyView = self.view
        self.view.disable_all_items()
        self.view.stop()
        await interaction.followup.edit_message(view.message.id, view=view)
        # await interaction.followup.edit_message(interaction.message.id, view=self) # oder einfach löschen

        selection = self.values[0]
        if not type(selection) == str:
            raise Exception("non-string types not supported yet")
        field_name = FieldName[selection]
        if not field_name:
            raise Exception(f"no FieldName {selection}")
        selected_field = view.tourney.fields[field_name]

        user:discord.User = interaction.user
        bot:Bot = interaction.client
        try:
            await interaction.followup.send(f"{selected_field.field_type.text} für {selected_field.name.value}:")
            event_response = await bot.wait_for("message", check=check_factory(user))
            try:
                selected_field.value = event_response
            except Exception as e:
                await interaction.followup.send("Das hat nicht geklappt: "+str(e.args))
            edit_view = EditTourneyView(view.tourney)
            embeds = []
            tourney_embed = self.view.tourney.create_embed()
            if tourney_embed:
                embeds.append(tourney_embed)
            gmaps_embed = self.view.tourney.create_gmap_embed()
            if gmaps_embed:
                embeds.append(gmaps_embed)
            await user.send(content=self.view.tourney.create_content(), embeds=embeds)
            await user.send(view=edit_view)
        except TimeoutError as e:
            await user.send("Du hast dir leider zu viel Zeit gelassen took too long to respond. Bitte versuche es noch einmal mit dem `/poste_turnier` Befehl.")

class EditTourneyView(discord.ui.View):
    def __init__(self, tourney:Tourney):
        super().__init__()
        self.tourney = tourney
        self.select_menu = FieldSelect(self.tourney.fields)

        # Submit Button
        self.submit_button = SubmitButton()
        self.add_item(self.select_menu)
        self.add_item(self.submit_button)

        self.update_submit_button()

    async def send_forum_post(self, interaction:discord.Interaction):
        guild = self.tourney.guild
        forum_channel = await discord.utils.get_or_fetch(guild, "channel", CHANNEL_PAPER_EVENTS_ID, default=None)

        if not isinstance(forum_channel, discord.ForumChannel):
            await interaction.followup.send(content="This is not a forum channel!")
            return
        
        notion_result = self.save_tourney_in_notion()

        # Create a new thread (forum post)
        thread = await forum_channel.create_thread(
            content=f"{self.tourney.create_content()}\n\nDanke an {self.tourney.author.mention} für's Posten!",
            name="New Forum Post Title",  # Change this to your post title
            # content="This is the forum post content.",  # First message in the post
            applied_tags=[],  # Optional: List of tag IDs if the forum has tags
            embed=self.tourney.create_embed()
        )

        await interaction.followup.send(f"Forum Post erstellt: {thread.jump_url}\n[Notion Eintrag]({notion_result['public_url']}) erstellt")


    def update_submit_button(self):
        """Enable or disable the submit button based on mandatory field completion."""
        self.submit_button.disabled = not all(field.value for field in self.tourney.fields.values() if field.mandatory)

    def save_tourney_in_notion(self):
        payload = notion.NotionPayloadBuilder()
        payload.add_title("Event Titel", "TEST EVENT")
        payload.add_checkbox("For Test", DEBUG)
        payload.add_text("Author", self.tourney.author.display_name if self.tourney.author.display_name else self.tourney.author.name)
        if self.tourney.fields[FieldName.TITLE].value:
            payload.add_title("Event Titel", self.tourney.fields[FieldName.TITLE].value)
        if self.tourney.fields[FieldName.START].value:
            payload.add_date("Start (und Ende)", start=self.tourney.fields[FieldName.START].value)
        if self.tourney.fields[FieldName.DESCRIPTION].value:
            payload.add_text("Freitext", self.tourney.fields[FieldName.DESCRIPTION].value)
        return notion.add_to_database(database_id=EVENT_DATABASE_ID, payload=payload.build())
    async def on_timeout(self):
        self.disable_all_items()
        self.stop()
        await self.message.edit(view=self)

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
    async def poste_turnier(self, ctx:ApplicationContext):
        try:
            user = ctx.author
            direct_message = await user.send(
                content="Lass uns zusammen das Turnier erstellen",
                # embed=turnier.create_info_embed()
            )
            
            await ctx.send_response(f"Lass uns zusammen in den Direktnachrichten das Turnier erstellen: {direct_message.jump_url}", ephemeral=True)
            await user.send("Was möchtest du bearbeiten?", view=EditTourneyView(Tourney(ctx.guild, user)))
        except discord.Forbidden:
            await ctx.send_response("Ich konnte dich nicht direkt anschreiben! Bitte erlaube Direktnachrichten von Mitgliedern des Servers.", ephemeral=True)

    @Cog.listener()
    async def on_application_command_error(self, ctx:ApplicationContext, error):
        log.error(error)
        await ctx.respond(f"Es ist ein Fehler aufgetreten: ```{error}```", ephemeral=True)
        raise error


def setup(bot:Bot):
    bot.add_cog(PaperEventSubmit(bot))
