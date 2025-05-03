import discord
from datetime import datetime
from typing import Literal, Optional
import discord
from modules import gmaps, env, ics, notion
from enum import Enum
from modules import date_time_interpretation as dti
import re
import textwrap
from ezcord import log

CHANNEL_PAPER_EVENTS_ID = env.CHANNEL_PAPER_EVENTS_ID
EVENT_DATABASE_ID = env.EVENT_DATABASE_ID
STATE_TAGS = env.STATE_TAGS
AREA_DATABASE_ID = env.AREA_DATABASE_ID
DEBUG = env.DEBUG

class FieldType:
    def __init__(self, parser=None, long=False, max_items=1):
        self.parser = parser
        self.long = long
        self.max_items = max_items

    def parse(self, value):
        if not value:
            return None
        if self.parser:
            return self.parser(value)
        if type(value) == str:
            return value
        if type(value) == discord.Message:
            return value.content.strip()
        if type(value) == discord.Interaction:
            return value.data['values']

def is_https_image_url(url: str) -> bool:
    pattern = re.compile(r'^https:\/\/.*\.(?:png|jpg|jpeg|gif|webp|svg)(?:\?.*)?$', re.IGNORECASE)
    return bool(pattern.match(url))
    
def parse_image(message:discord.Message|str):
    if type(message) == discord.Message:
        if message.content and message.attachments:
            raise Exception("Lade ein Bild hoch *oder* schreibe einen Link zu einem Bild, nicht beides")
        if message.content:
            if is_https_image_url(message.content):
                return message.content
            else:
                raise Exception("Der Link sieht nicht nach einem Link zu einem Bild aus")
    elif type(message) == str:
        return message
    raise Exception("Invalider Dateityp: Kein Bild")

def parse_location(message:discord.Message|str) -> gmaps.Location:
    text = message
    if type(message) == discord.Message:
        text = message.content
    location = gmaps.get_location(text, details=True)
    return location

class DropDownSelect(discord.ui.Select):
    def __init__(self, event:"PaperEvent", inputField:"InputField", orig_message:discord.Message):
        self.input_field = inputField
        self.event = event
        self.orig_message = orig_message

        options = self.get_options()
        
        if inputField.field_type.max_items == "max":
            max_values = len(options)
        else:
            max_values = inputField.field_type.max_items

        super().__init__(
            placeholder=f"Wähle ein oder mehrere {inputField.notion_column}...",
            min_values=1,
            max_values=max_values,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            self.input_field.value = self.values
            await edit_event_post(self.event, self.orig_message, EditTourneyView(self.event))
        except Exception as e:
            await interaction.respond(e, ephemeral=True)

        await interaction.message.delete()
    
    def get_options(self):
        """Generates the options dynamically based on field status."""
        options = notion.get_select_options(EVENT_DATABASE_ID, self.input_field.notion_column)
        return [
            discord.SelectOption(label=option, value=option)
            for option in options
        ]

class DropDownSelectView(discord.ui.View):
    def __init__(self, event, inputField:"InputField", orig_message:discord.Message):
        super().__init__()
        self.add_item(DropDownSelect(event, inputField, orig_message))
    
def parse_datetime(message_or_text:datetime|discord.Message|str):
    if type(message_or_text) == datetime:
        return message_or_text
    if type(message_or_text) == discord.Message:
        message_or_text = message_or_text.content
    return dti.parse_date(message_or_text)

def parse_int(message_or_text:int|discord.Message|str):
    if type(message_or_text) == int:
        return message_or_text
    if type(message_or_text) == discord.Message:
        message_or_text = message_or_text.content
    return int(message_or_text)

def parse_list(message_or_text):
    if type(message_or_text) == list:
        return message_or_text
    if type(message_or_text) == str:
        return [part.strip() for part in re.split(r'[;,|]', message_or_text)]

# Defining field types
FIELD_TYPE_TIME = FieldType(parse_datetime)
FIELD_TYPE_TEXT = FieldType()
FIELD_TYPE_TEXT_LONG = FieldType(long=True)
FIELD_TYPE_LIST = FieldType(parse_list)
FIELD_TYPE_NUMBER = FieldType(parse_int)
FIELD_TYPE_IMAGE = FieldType(parse_image)
FIELD_TYPE_LOCATION = FieldType(parse_location)

class FieldName(Enum):
    TYPE = "Typ"
    TITLE = "Titel"
    START = "Start"
    LOCATION = "Veranstaltungsort"
    FORMATS = "Format(e)"
    END = "Ende"
    FEE = "Teilnahmegebühr [in €]"
    DESCRIPTION = "Beschreibung / Freitext"
    URL = "URL / Link"
    IMAGE = "Bild"

class InputField:
    def __init__(self, name: FieldName, field_type: FieldType, mandatory: bool = False, icon:str = None, description=None, notion_column=None):
        self.name = name
        self.field_type = field_type
        self.mandatory = mandatory
        self._value = None
        self.icon = icon
        self.description = description
        self.notion_column:str = notion_column

    def status_emoji(self, fields) -> str:
        """Returns the status emoji based on the field's state."""
        if self.value:
            return "✅"
        if (self.name == FieldName.TITLE and not fields[FieldName.TYPE].value
            or self.name == FieldName.TYPE and not fields[FieldName.TITLE].value):
            return "⚠️"
        return "⚠️" if self.mandatory else ""

    def label(self, fields) -> str:
        """Returns the formatted label for the select menu."""
        return f"{self.name.value} {self.status_emoji(fields)}"
    
    @property
    def value(self):
        return self._value
    
    @value.setter
    def value(self, new_value):
        self._value = self.field_type.parse(new_value)

def get_timestamp_style(timestamp1: datetime|None, timestamp2: datetime|None) -> Literal["t", "D", "f"]:
    """
    If both timestamps are on the same day the date does not need to get doubled
    therefore short time is enough to show for the end date
    """
    if not timestamp1 or not timestamp2:
        return "f"
    if timestamp1.date() == timestamp2.date():
        return "t"  # Same date: short time
    else:
        return "D"  # Different dates: long date and time
    
async def edit_event_post(event:"PaperEvent", interaction_or_message:discord.Interaction|discord.Message, view):
    embeds = [event.construct_event_embed()]
    gmaps_embed = event.construct_gmaps_embed()
    if gmaps_embed:
        embeds.append(gmaps_embed)
    files = event.get_files()
    if type(interaction_or_message) == discord.Interaction:
        await interaction_or_message.response.edit_message(
            content=event.construct_content(preview=True),
            embeds=embeds,
            files=files,
            attachments=[],
            view=view
            )
    elif type(interaction_or_message) == discord.Message:
        thread = interaction_or_message.channel
        if isinstance(thread, discord.Thread):
            await thread.edit(name=event.construct_thread_title())
        await interaction_or_message.edit(
            content=event.construct_content(preview=False),
            embeds=embeds,
            files=files,
            attachments=[],
            view=view
            )
    else:
        log.error("interaction is neither Interaction nor Message")

class EditFieldModal(discord.ui.Modal):
    def __init__(self, event:"PaperEvent", field:InputField):
        super().__init__(title=field.name.value)

        self.event = event
        self.field = field

        value = ""
        if field.value:
            if type(field.value) == list:
                value = ", ".join(field.value)
            else:
                value = str(field.value)

        self.input = discord.ui.InputText(
            label=field.name.value,
            placeholder=field.description,
            required=field.mandatory,
            value=value,
            style=discord.InputTextStyle.multiline if field.field_type.long else discord.InputTextStyle.singleline
        )
        self.add_item(self.input)

    async def callback(self, interaction: discord.Interaction):
        try:
            new_value = self.input.value
            self.field.value = new_value

            await edit_event_post(self.event, interaction, EditTourneyView(self.event))
        except Exception as e:
            await interaction.respond(e, ephemeral=True)

def make_callback(event, field:InputField):
    async def edit_field_callback(interaction:discord.Interaction):
        if field.notion_column:
            # list items
            view = DropDownSelectView(event, field, interaction.message)
            await interaction.response.send_message(view=view)
        else:
            modal = EditFieldModal(event, field)
            await interaction.response.send_modal(modal)
    return edit_field_callback

class SubmitButton(discord.ui.Button):
    """Custom button for submitting the tournament."""
    def __init__(self, event:"PaperEvent"):
        super().__init__(label="Anpassungen übernehmen" if event.thread else "Turnier einsenden", style=discord.ButtonStyle.primary, disabled=True)

        self.event = event

        all_mandatory_filled = all(field.value for field in event.fields.values() if field.mandatory)
        at_least_one_conditional_filled = event.fields[FieldName.TITLE].value or event.fields[FieldName.TYPE].value
        end_is_earlier = False
        start = event.fields[FieldName.START].value
        end = event.fields[FieldName.END].value
        if start and end:
            if type(start) == datetime and type(end) == datetime:
                if end < start:
                    end_is_earlier = True
                    self.label += "  (Ende ist vor Start)"
            else:
                raise ValueError("One of start or end is not of type datetime")
        is_valid = all_mandatory_filled and at_least_one_conditional_filled and not end_is_earlier
        self.disabled = not is_valid

    async def callback(self, interaction: discord.Interaction):
        response_message = await interaction.respond("Wird bearbeitet...")
        """Handles the submission when clicked."""
        orig_view:EditTourneyView = self.view
        await interaction.followup.edit_message(orig_view.message.id, view=None)

        # Ensure all mandatory fields are filled
        if not all(field.value for field in self.event.fields.values() if field.mandatory):
            await interaction.response.send_message("Not all mandatory fields are filled!", ephemeral=True)
            return
        
        if type(interaction.channel) == discord.DMChannel:
            if self.event.thread:
                # edit
                message:discord.Message = await self.event.thread.fetch_message(self.event.thread.id)
                notion_result = self.event.save_in_notion()
                await edit_event_post(self.event, message, EditPostView(self.event))
                await response_message.edit(content=f"Post und Notioneintrag wurden bearbeitet: \n{message.jump_url}\n{notion_result['public_url']}")
            else:
                await orig_view.send_forum_post(interaction)
        else:
            raise Exception("interaction.channel is not a DMChannel")

class EditTourneyView(discord.ui.View):
    def __init__(self, event:"PaperEvent"):
        super().__init__(timeout=None)
        self.event = event

        for field_name, field in self.event.fields.items():
            label = field.name.value
            if field.mandatory or (field_name in [FieldName.TITLE, FieldName.TYPE]
                                   and not (self.event.fields[FieldName.TITLE].value or self.event.fields[FieldName.TYPE].value)
                                   ):
                label += "*"
            label += " "+field.status_emoji(self.event.fields)
            button = discord.ui.Button(label=label, emoji=field.icon)
            button.callback = make_callback(event, field)
            self.add_item(button)

        # Submit Button
        self.submit_button = SubmitButton(event)
        self.add_item(self.submit_button)
        self.add_cancel_button(self)

    def add_cancel_button(self, view: discord.ui.View):
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red)

        # Define the cancel button's behavior
        async def cancel_button_callback(interaction: discord.Interaction):
            await interaction.response.edit_message(view=None)            
            await self.event.author.send("❌ Erstellung der Veranstaltung abgebrochen.")

        cancel_button.callback = cancel_button_callback
        view.add_item(cancel_button)

    async def send_forum_post(self, interaction:discord.Interaction):
        guild = self.event.guild
        forum_channel = await discord.utils.get_or_fetch(guild, "channel", CHANNEL_PAPER_EVENTS_ID, default=None)

        if not isinstance(forum_channel, discord.ForumChannel):
            await interaction.followup.send(content="This is not a forum channel!")
            return
        
        thread = await self.event.create_and_send_thread(forum_channel)
        notion_result = self.event.save_in_notion()

        await interaction.followup.send(f"Forum Post erstellt: {thread.jump_url}\n[Notion Eintrag]({notion_result['public_url']}) erstellt")
    
    async def on_timeout(self):
        await self.message.edit(view=None)
    
class EditPostView(discord.ui.View):
    def __init__(self, event:"PaperEvent"):
        super().__init__(timeout=None)
        self.event = event

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️")
    async def confirm_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        if not interaction.user:
            return
        if self.event.author != interaction.user and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.respond("Du bist nicht Author dieses Posts!", ephemeral=True)
            return
        # send PM to edit
        pm_message = await self.event.send_preview(interaction.user)
        await interaction.respond("Die Bearbeitung findet in deinen DMs statt: "+pm_message.jump_url, ephemeral=True)
        
class PaperEvent():
    def __init__(
            self,
            guild:discord.Guild,
            author:discord.Member,
        ):
        self.guild = guild
        self.author = author
        self.thread:discord.Thread = None

        field_list = [
            InputField(FieldName.TITLE, FIELD_TYPE_TEXT, icon="📢", description=f"Wenn nicht angegeben: {FieldName.FORMATS.value} + {FieldName.TYPE.value}"),
            InputField(FieldName.DESCRIPTION, FIELD_TYPE_TEXT_LONG, icon="📜", description="Freitext"),
            InputField(FieldName.START, FIELD_TYPE_TIME, mandatory=True, icon="⏰", description="Wann das Event/Turnier los geht"),
            InputField(FieldName.END, FIELD_TYPE_TIME, icon="⏳", description="Wann das Event/Turnier zu Ende ist"),
            InputField(FieldName.LOCATION, FIELD_TYPE_LOCATION, mandatory=True, icon="📍", description="Wo das Event/Turnier stattfindet"),
            InputField(FieldName.FEE, FIELD_TYPE_NUMBER, icon="💸"),
            InputField(FieldName.FORMATS, FieldType(parser=parse_list, max_items="max"), icon="🎮", description=f"Kommasepariert, welche(s) Format(e) gespielt wird/werden", mandatory=True, notion_column="Format(e)"),
            InputField(FieldName.TYPE, FIELD_TYPE_LIST, icon="🏷️", description="Kommasepariert, zum Beipsiel FNM, RCQ, Prerelease...", notion_column="Event Typ"),
            InputField(FieldName.URL, FIELD_TYPE_TEXT, icon="🔗", description="Der Link zur Veranstaltung"),
            InputField(FieldName.IMAGE, FIELD_TYPE_IMAGE, icon="🖼️", description="Repräsentiert die Veranstaltung. Wenn nicht angegeben: Versuch Bild aus Link"),
        ]
        self.fields:dict[FieldName, InputField] = {field.name: field for field in field_list}
    
    def fill_fields_from_notion_entry(self, entry:notion.Entry):
        self.fields[FieldName.TITLE].value = entry.get_text_property("Event Titel")
        self.fields[FieldName.DESCRIPTION].value = entry.get_text_property("Freitext")
        self.fields[FieldName.START].value = entry.get_date_property("Start (und Ende)")['start']
        self.fields[FieldName.END].value = entry.get_date_property("Start (und Ende)")['end']
        # location = gmaps.Location()
        location_name = entry.get_text_property("Name des Ladens")
        # street_and_number = entry.get_text_property("Straße + Hausnummer")
        # location.street = entry.get_text_property("Straße + Hausnummer")
        location_city = entry.get_text_property("Stadt")
        # location.state = entry.get_text_property("(Bundes)land")
        self.fields[FieldName.LOCATION].value = f"{location_name} {location_city}"
        self.fields[FieldName.FEE].value = entry.get_number_property("Gebühr")
        formats = entry.get_multi_select_property("Format(e)")
        self.fields[FieldName.FORMATS].value = formats
        self.fields[FieldName.TYPE].value = entry.get_status_property("Event Typ")
        self.fields[FieldName.URL].value = entry.get_url_property("URL")
        self.fields[FieldName.IMAGE].value = entry.get_file_property("Cover Bild")

    def save_in_notion(self):
        payload = notion.NotionPayloadBuilder()
        image:str|None = self.fields[FieldName.IMAGE].value
        if image:
            payload.add_file("Cover Bild", file_url=image, file_name="Cover Image")

        payload.add_title("Event Titel", self.build_title())
        payload.add_checkbox("For Test", DEBUG)
        payload.add_text("Author", self.author.display_name if self.author.display_name else self.author.name)
        payload.add_text("Author ID", str(self.author.id))
        if self.fields[FieldName.TYPE].value:
            payload.add_select("Event Typ", self.fields[FieldName.TYPE].value[0])
        if self.fields[FieldName.FORMATS].value:
            payload.add_multiselect("Format(e)", self.fields[FieldName.FORMATS].value)
        payload.add_date(
            "Start (und Ende)",
            start=self.fields[FieldName.START].value,
            end=self.fields[FieldName.END].value
        )
        payload.add_text("Freitext", self.fields[FieldName.DESCRIPTION].value or "")
        if self.fields[FieldName.FEE].value:
            payload.add_number("Gebühr", self.fields[FieldName.FEE].value)
        location:gmaps.Location = self.fields[FieldName.LOCATION].value
        payload.add_text("Name des Ladens", location.name or "")
        payload.add_text("Stadt", location.city['long_name'])
        payload.add_relation("(Bundes)land", location.get_area_page_id())
        if self.fields[FieldName.URL].value:
            payload.add_url("URL", self.fields[FieldName.URL].value)
        payload.add_text("Server ID", str(self.guild.id))
        payload.add_text("Thread ID", str(self.thread.id))
        filter = (
            notion.NotionFilterBuilder()
            .add_text_filter("Server ID", notion.TextCondition.EQUALS, str(self.guild.id))
            .add_text_filter("Thread ID", notion.TextCondition.EQUALS, str(self.thread.id))
            .build())
        return notion.add_or_update_entry(database_id=EVENT_DATABASE_ID, payload=payload.build(), filter=filter)

    def build_title(self):
        title = self.fields[FieldName.TITLE].value
        if title:
            return title
        else:
            formats = self.fields[FieldName.FORMATS].value
            format = ", ".join(formats) if formats else f"<{FieldName.FORMATS.value}>"
            event_type_list = self.fields[FieldName.TYPE].value
            type = event_type_list[0] if event_type_list else f"<{FieldName.TYPE.value}>"
            return f"{format} {type}"
        
    def get_files(self):
        files = []
        location:gmaps.Location|None = self.fields[FieldName.LOCATION].value
        if location:
            files.append(discord.File(location.file_path, filename=location.file_name))
        title = self.build_title()
        ics_file = ics.create_ics_file(f"{title}.ics", title, self.fields[FieldName.START].value, self.fields[FieldName.END].value, description=self.fields[FieldName.DESCRIPTION].value, location=location.formatted_address if location else None)
        if ics_file:
            files.append(discord.File(ics_file, filename=ics_file))
        return files
    
    def construct_thread_title(self):
        start:datetime|None = self.fields[FieldName.START].value
        start_str = start.strftime('%d.%m.%Y') if start else f'<{FieldName.START.value}>'
        
        title = self.build_title()
        location:gmaps.Location|None = self.fields[FieldName.LOCATION].value
        if location:
            location_str = f"{location.name} in {location.city['long_name']}"
        else:
            location_str = f'<{FieldName.LOCATION.value}>'

        return textwrap.shorten(f"{start_str} - {title} @ {location_str}", width=100, placeholder="...")
    
    def construct_content(self, preview=True):
        description:str|None = self.fields[FieldName.DESCRIPTION].value
        content = ""
        if preview:
            content += f"# {self.construct_thread_title()}\n"
        if description:
            content += description
        if not preview:
            content += f"\n\nDanke an {self.author.mention} für's Posten!"
        return content

    def construct_gmaps_embed(self) -> discord.Embed|None:
        location:gmaps.Location|None = self.fields[FieldName.LOCATION].value
        if not location:
            return None
        embed_fields = [
                discord.EmbedField(name="Name", value=location.name, inline=False),
                discord.EmbedField(name="Adresse", value=location.formatted_address, inline=False),
            ]
        if location.url:
            embed_fields.append(discord.EmbedField(name="Webseite", value=location.url, inline=False))
        embed = discord.Embed(
            color=env.RR_GREEN,
            title="Google Maps",
            url=location.get_search_url(),
            image=f"attachment://{location.file_name}",
            fields=embed_fields,
        )
        return embed
    
    def construct_event_embed(self):
        start = self.fields[FieldName.START].value
        if start:
            start_value = f"{discord.utils.format_dt(start, 'f')}\n{discord.utils.format_dt(start, 'R')}"
        else:
            start_value = f"<{FieldName.START.value}>"
        embed = discord.Embed(
            title=self.build_title(),
            color=env.RR_GREEN,
            url=self.fields[FieldName.URL].value,
            fields=[
                discord.EmbedField(FieldName.START.value, value=start_value, inline=True)
            ],
        )
        image = self.fields[FieldName.IMAGE].value
        if image:
            embed.set_image(url=image)
        end = self.fields[FieldName.END].value
        if end:
            end_value = discord.utils.format_dt(end, get_timestamp_style(start, end))
            embed.add_field(name=FieldName.END.value, value=end_value, inline=True)
        if start and end:
            embed.add_field(name="Dauer", value=dti.human_delta(end, start), inline=True)

        fee = self.fields[FieldName.FEE].value
        if fee:
            embed.add_field(name=FieldName.FEE.value, value=str(fee)+" €", inline=True)
        formats = self.fields[FieldName.FORMATS].value
        format = ", ".join(formats) if formats else f"<{FieldName.FORMATS.value}>"
        embed.add_field(name=FieldName.FORMATS.value, value=format, inline=False)
        event_type_list = self.fields[FieldName.TYPE].value
        type = event_type_list[0] if event_type_list else None # f"<{FieldName.TYPE.value}>"
        if type:
            embed.add_field(name=FieldName.TYPE.value, value=type, inline=True)
        return embed

    async def create_and_send_thread(self, channel:discord.ForumChannel):
        location:gmaps.Location = self.fields[FieldName.LOCATION].value
        (area_name, tag_name) = location.get_area_and_tag_name()
        tag = discord.utils.get(channel.available_tags, name=tag_name)
        forum_post = await channel.create_thread(
            name=self.construct_thread_title(),
            content=self.construct_content(preview=False),
            embeds=[self.construct_event_embed(), self.construct_gmaps_embed()],
            applied_tags=[tag],
            files=self.get_files(),
            view=EditPostView(self)
        )
        self.thread = forum_post
        return forum_post
    
    async def send_preview(self, user:discord.User):
        embeds = []
        tourney_embed = self.construct_event_embed()
        if tourney_embed:
            embeds.append(tourney_embed)
        gmaps_embed = self.construct_gmaps_embed()
        if gmaps_embed:
            embeds.append(gmaps_embed)
        return await user.send(content=self.construct_content(preview=True), embeds=embeds, files=self.get_files(), view=EditTourneyView(self))
