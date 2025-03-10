import discord
from datetime import datetime
from typing import Literal, Optional
import discord
from modules import gmaps, env, ics, notion
from enum import Enum
from modules import date_time_interpretation as dti
import re
import textwrap

EVENT_DATABASE_ID = env.EVENT_DATABASE_ID
STATE_TAGS = env.STATE_TAGS
AREA_DATABASE_ID = env.AREA_DATABASE_ID

class FieldType:
    def __init__(self, icon, text, parser=None, ui_item:type[discord.ui.Item]|None=None):
        self.icon = icon
        self.parser = parser
        self.text = text
        self.ui_item = ui_item

    def parse(self, value):
        if not value:
            return None
        if self.parser:
            return self.parser(value)
        if type(value) == discord.Message:
            return value.content.strip()
        if type(value) == discord.Interaction:
            return value.data['values']

def is_https_image_url(url: str) -> bool:
    pattern = re.compile(r'^https:\/\/.*\.(?:png|jpg|jpeg|gif|webp|svg)(?:\?.*)?$', re.IGNORECASE)
    return bool(pattern.match(url))
    
def parse_image(message:discord.Message):
    if message.content and message.attachments:
        raise Exception("Lade ein Bild hoch *oder* schreibe einen Link zu einem Bild, nicht beides")
    if message.content:
        if is_https_image_url(message.content):
            return message.content
        else:
            raise Exception("Der Link sieht nicht nach einem Link zu einem Bild aus")
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
    location = gmaps.get_location(content, details=True)
    return location

class FormatSelect(discord.ui.Select):
    def __init__(self):
        options = self.get_options()
        super().__init__(
            placeholder="Wähle ein oder mehrere Format(e)...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    def get_options(self):
        """Generates the options dynamically based on field status."""
        options = notion.get_select_options(EVENT_DATABASE_ID, "Format(e)")
        return [
            discord.SelectOption(label=option, value=option)
            for option in options
        ]



class EventTypeSelect(discord.ui.Select):
    def __init__(self):
        options = self.get_options()
        super().__init__(
            placeholder="Wähle ein Event Typ...",
            min_values=1,
            max_values=1,
            options=options
        )

    def get_options(self):
        """Generates the options dynamically based on field status."""
        options = notion.get_select_options(EVENT_DATABASE_ID, "Event Typ")
        return [
            discord.SelectOption(label=option, value=option)
            for option in options
        ]

# Defining field types
FIELD_TYPE_TIME = FieldType("🕒", "Bitte nenne einen Zeitpunkt", lambda message: dti.parse_date(message.content) if message.content else None)
FIELD_TYPE_TEXT = FieldType("📝", "Bitte gib einen Text ein")
FIELD_TYPE_FORMAT = FieldType("📝", "Wähle ein Format", ui_item=FormatSelect)
FIELD_TYPE_EVENT_TYPE = FieldType("📝", "Wähle ein Event Typ", ui_item=EventTypeSelect)
FIELD_TYPE_NUMBER = FieldType("🔢", "Bite gib eine Zahl ein", lambda message: int(message.content) if message.content.isdigit() else None)
# FIELD_TYPE_EMAIL = FieldType("✉️", "Gib eine Email ein", lambda x: x if "@" in x else None)
FIELD_TYPE_IMAGE = FieldType("🖼️", "Bitte schreibe einen Link zu einem Bild oder lade ein Bild hoch", parse_image)
FIELD_TYPE_LOCATION = FieldType("📍", "Bitte gib einen Ort an", parse_location)

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
    def __init__(self, name: FieldName, field_type: FieldType, mandatory: bool = False, icon: Optional[str] = None, description=None):
        self.name = name
        self.field_type = field_type
        self.mandatory = mandatory
        self._value = None
        self.custom_icon = icon  # Custom icon, if provided
        self.description = description

    def status_emoji(self, fields) -> str:
        """Returns the status emoji based on the field's state."""
        if self.value:
            return "✅"
        # return "❌" if self.mandatory else "⚠️"
        if (self.name == FieldName.TITLE and not fields[FieldName.TYPE].value
            or self.name == FieldName.TYPE and not fields[FieldName.TITLE].value):
            return "⚠️"
        return "⚠️" if self.mandatory else ""

    @property
    def icon(self) -> str:
        """Returns the custom icon if provided, otherwise falls back to the default."""
        return self.custom_icon or self.field_type.icon

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

class PaperEvent():
    def __init__(
            self,
            guild:discord.Guild,
            author:discord.Member,
        ):
        self.guild = guild
        self.author = author

        field_list = [
            InputField(FieldName.TITLE, FIELD_TYPE_TEXT, icon="📢", description=f"Wenn nicht angegeben: {FieldName.FORMATS.value} + {FieldName.TYPE.value}"),
            InputField(FieldName.DESCRIPTION, FIELD_TYPE_TEXT, icon="📜", description="Freitext"),
            InputField(FieldName.START, FIELD_TYPE_TIME, mandatory=True, icon="⏰", description="Wann das Event/Turnier los geht"),
            InputField(FieldName.END, FIELD_TYPE_TIME, icon="⏳", description="Wann das Event/Turnier zu Ende ist"),
            InputField(FieldName.LOCATION, FIELD_TYPE_LOCATION, mandatory=True, icon="📍", description="Wo das Event/Turnier stattfindet"),
            InputField(FieldName.FEE, FIELD_TYPE_NUMBER, icon="💸"),
            InputField(FieldName.FORMATS, FIELD_TYPE_FORMAT, icon="🎮", description=f"Welche(s) Format(e) gespielt wird/werden", mandatory=True),
            InputField(FieldName.TYPE, FIELD_TYPE_EVENT_TYPE, icon="🏷️", description="Zum Beipsiel FNM, RCQ, Prerelease..."),
            InputField(FieldName.URL, FIELD_TYPE_TEXT, icon="🔗", description="Der Link zur Veranstaltung"),
            InputField(FieldName.IMAGE, FIELD_TYPE_IMAGE, icon="🖼️", description="Repräsentiert die Veranstaltung. Wenn nicht angegeben: Versuch Bild aus Link"),
        ]
        self.fields = self.fields = {field.name: field for field in field_list}

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
        # ics.create_ics_file(file_name, event_name, start_datetime, end_datetime:datetime, description=None, location=None)
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
            content += f"# {self.construct_thread_title()}\n    "
        if description:
            content += "\n".join(f"> {line}" for line in description.splitlines())
        if not preview:
            content += f"\n\nDanke an {self.author.mention} für's Posten!"
        return f"{content}"

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
            files=self.get_files()
        )
        return forum_post
    
    async def send_preview(self, user:discord.User):
        embeds = []
        tourney_embed = self.construct_event_embed()
        if tourney_embed:
            embeds.append(tourney_embed)
        gmaps_embed = self.construct_gmaps_embed()
        if gmaps_embed:
            embeds.append(gmaps_embed)
        return await user.send(content=self.construct_content(preview=True), embeds=embeds, files=self.get_files())
