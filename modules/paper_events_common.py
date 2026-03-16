import discord
from datetime import datetime
from typing import Literal, Optional
import discord
from modules import gmaps, env, ics, paper_events_db
from modules.paper_events_calendar import update_paper_events_calendar
from enum import Enum
import json

# Import will be done at runtime to avoid circular dependency
def _get_schedule_function():
    from cogs.paper_events.paper_events_status_monitor import schedule_event_timers
    return schedule_event_timers


def _get_recurring_propagation_function():
    from cogs.paper_events.paper_events_status_monitor import propagate_recurring_edits
    return propagate_recurring_edits

from modules import date_time_interpretation as dti
import re
import textwrap
from ezcord import log

CHANNEL_PAPER_EVENTS_ID = env.CHANNEL_PAPER_EVENTS_ID
DEBUG = env.DEBUG

PAPER_EVENT_FORMATS = [
    "Standard", "Pioneer", "Modern", "Legacy", "Vintage", "Pauper",
    "Commander", "cEDH", "Peasant", "Draft", "Sealed", "Prerelease",
]
PAPER_EVENT_TYPES = [
    "FNM", "RCQ", "Regional Championship", "Game Day", "Prerelease",
    "Launch Party", "Store Championship", "Sunday Challenge", "Other",
]
RECURRENCE_OPTIONS = [
    "Keine",
    "Wöchentlich",
    "Alle 2 Wochen",
    "Monatlich",
]
if DEBUG:
    RECURRENCE_OPTIONS.insert(1, "1 Minute (Test)")

RECURRENCE_RULE_MAP = {
    "1 Minute (Test)": "FREQ=MINUTELY;INTERVAL=1",
    "Wöchentlich": "FREQ=WEEKLY;INTERVAL=1",
    "Alle 2 Wochen": "FREQ=WEEKLY;INTERVAL=2",
    "Monatlich": "FREQ=MONTHLY;INTERVAL=1",
}
RECURRENCE_LABEL_MAP = {value: key for key, value in RECURRENCE_RULE_MAP.items()}
EVENT_SELECT_OPTIONS = {
    "Format(e)": PAPER_EVENT_FORMATS,
    "Event Typ": PAPER_EVENT_TYPES,
    "Wiederholung": RECURRENCE_OPTIONS,
}

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
    RECURRING = "Wiederholung"
    RECURRING_UNTIL = "Wiederholung bis"
    RECURRING_COUNT = "Weitere Termine"
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
        await interaction_or_message.edit_original_response(
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
            await interaction.response.defer()
            new_value = self.input.value
            self.field.value = new_value

            await edit_event_post(self.event, interaction, EditTourneyView(self.event))
        except Exception as e:
            await interaction.followup.send(str(e), ephemeral=True)

class SelectFieldModal(discord.ui.DesignerModal):
    """Modal with a dropdown select for notion-backed list/multiselect fields."""
    def __init__(self, event: "PaperEvent", field: InputField):
        super().__init__(title=field.name.value)
        self.event = event
        self.field = field

        options_raw = EVENT_SELECT_OPTIONS.get(field.notion_column, [])
        current = field.value if isinstance(field.value, list) else ([field.value] if field.value else [])

        options = [
            discord.SelectOption(label=opt, value=opt, default=opt in current)
            for opt in options_raw
        ]

        if field.field_type.max_items == "max":
            max_values = len(options) if options else 1
        else:
            max_values = field.field_type.max_items

        self.select = discord.ui.StringSelect(
            placeholder=f"Wähle {field.name.value}...",
            options=options,
            min_values=1,
            max_values=max_values,
        )
        self.add_item(discord.ui.Label(field.name.value, self.select))

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            self.field.value = self.select.values
            await edit_event_post(self.event, interaction, EditTourneyView(self.event))
        except Exception as e:
            await interaction.followup.send(str(e), ephemeral=True)

def make_callback(event, field:InputField):
    async def edit_field_callback(interaction:discord.Interaction):
        if field.notion_column:
            modal = SelectFieldModal(event, field)
            await interaction.response.send_modal(modal)
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
        """Handles the submission when clicked."""
        await interaction.response.defer()
        orig_view:EditTourneyView = self.view

        # Ensure all mandatory fields are filled
        if not all(field.value for field in self.event.fields.values() if field.mandatory):
            await interaction.edit_original_response(content="Not all mandatory fields are filled!", view=orig_view)
            return

        if self.event.thread:
            # edit
            message:discord.Message = await self.event.thread.fetch_message(self.event.thread.id)
            self.event.save_to_db()
            await edit_event_post(self.event, message, EditPostView(self.event))
            await update_paper_events_calendar(interaction.client, self.event.guild)

            # Reschedule timers for the edited event
            schedule_fn = _get_schedule_function()
            propagate_fn = _get_recurring_propagation_function()
            start = self.event.fields[FieldName.START].value
            end = self.event.fields[FieldName.END].value
            await schedule_fn(interaction.client, self.event.guild, self.event.thread.id, start, end)
            await propagate_fn(interaction.client, self.event.guild, self.event.thread.id)

            await interaction.edit_original_response(
                content=f"Post wurde bearbeitet:\n{message.jump_url}",
                embeds=[],
                attachments=[],
                view=None
            )
        else:
            await orig_view.send_forum_post(interaction)

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
            await interaction.response.defer()
            try:
                await interaction.delete_original_response()
            except Exception:
                await interaction.edit_original_response(content=None, embeds=[], attachments=[], view=None)

        cancel_button.callback = cancel_button_callback
        view.add_item(cancel_button)

    async def send_forum_post(self, interaction:discord.Interaction):
        guild = self.event.guild
        forum_channel = await discord.utils.get_or_fetch(guild, "channel", CHANNEL_PAPER_EVENTS_ID, default=None)

        if not isinstance(forum_channel, discord.ForumChannel):
            await interaction.edit_original_response(content="This is not a forum channel!", view=None)
            return
        
        thread = await self.event.create_and_send_thread(forum_channel)
        self.event.save_to_db()
        await update_paper_events_calendar(interaction.client, self.event.guild)

        # Schedule timers for the new event
        schedule_fn = _get_schedule_function()
        start = self.event.fields[FieldName.START].value
        end = self.event.fields[FieldName.END].value
        await schedule_fn(interaction.client, self.event.guild, thread.id, start, end)

        await interaction.edit_original_response(
            content=f"Forum Post erstellt: {thread.jump_url}",
            embeds=[],
            attachments=[],
            view=None,
        )
    
    async def on_timeout(self):
        try:
            await self.message.edit(view=None)
        except discord.NotFound:
            # Message was deleted, nothing to do
            pass
    
class EditPostView(discord.ui.View):
    def __init__(self, event:"PaperEvent"):
        super().__init__(timeout=None)
        self.event = event

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️")
    async def confirm_callback(self, button:discord.ui.Button, interaction:discord.Interaction):
        if not interaction.user:
            return
        if self.event.author is None and any(role.name == "Moderator" for role in interaction.user.roles):
            self.event.author = interaction.user
        if self.event.author != interaction.user and not any(role.name == "Moderator" for role in interaction.user.roles):
            await interaction.response.send_message("Du bist nicht Author dieses Posts!", ephemeral=True)
            return
        embeds = [self.event.construct_event_embed()]
        gmaps_embed = self.event.construct_gmaps_embed()
        if gmaps_embed:
            embeds.append(gmaps_embed)

        # Open ephemeral edit view in-channel with the current preview immediately visible.
        await interaction.response.send_message(
            content=self.event.construct_content(preview=True),
            embeds=embeds,
            files=self.event.get_files(),
            view=EditTourneyView(self.event),
            ephemeral=True
        )
        
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
            InputField(FieldName.START, FIELD_TYPE_TIME, mandatory=True, icon="⏰", description="z.B. morgen um 17 Uhr, am 24.12.2025 um 12:00 Uhr, in 2 Stunden"),
            InputField(FieldName.END, FIELD_TYPE_TIME, icon="⏳", description="z.B. morgen um 22 Uhr, am 24.12.2025 um 18:00 Uhr, in 5 Stunden"),
            InputField(FieldName.RECURRING, FIELD_TYPE_LIST, icon="🔁", description="Optional: Keine, 1 Minute (Test), Wöchentlich, Alle 2 Wochen, Monatlich", notion_column="Wiederholung"),
            InputField(FieldName.RECURRING_UNTIL, FIELD_TYPE_TIME, icon="📅", description="Optional: Letzter erlaubter Termin der Wiederholung"),
            InputField(FieldName.RECURRING_COUNT, FIELD_TYPE_NUMBER, icon="🔢", description="Optional: Wie viele weitere Termine nach diesem noch folgen sollen"),
            InputField(FieldName.LOCATION, FIELD_TYPE_LOCATION, mandatory=True, icon="📍", description="Wo das Event/Turnier stattfindet"),
            InputField(FieldName.FEE, FIELD_TYPE_NUMBER, icon="💸"),
            InputField(FieldName.FORMATS, FieldType(parser=parse_list, max_items="max"), icon="🎮", description=f"Kommasepariert, welche(s) Format(e) gespielt wird/werden", mandatory=True, notion_column="Format(e)"),
            InputField(FieldName.TYPE, FIELD_TYPE_LIST, icon="🏷️", description="Kommasepariert, zum Beipsiel FNM, RCQ, Prerelease...", notion_column="Event Typ"),
            InputField(FieldName.URL, FIELD_TYPE_TEXT, icon="🔗", description="Der Link zur Veranstaltung"),
            InputField(FieldName.IMAGE, FIELD_TYPE_IMAGE, icon="🖼️", description="Repräsentiert die Veranstaltung. Wenn nicht angegeben: Versuch Bild aus Link"),
        ]
        self.fields:dict[FieldName, InputField] = {field.name: field for field in field_list}

    def _build_recurrence_status_text(self) -> str | None:
        recurrence_list = self.fields[FieldName.RECURRING].value
        recurrence = recurrence_list[0] if recurrence_list else None
        if not recurrence or recurrence == "Keine":
            return None

        recurrence_until = self.fields[FieldName.RECURRING_UNTIL].value
        recurrence_count_raw = self.fields[FieldName.RECURRING_COUNT].value

        lines: list[str] = []

        if recurrence_count_raw:
            try:
                recurrence_count = int(recurrence_count_raw)
            except Exception:
                recurrence_count = None

            if recurrence_count is not None:
                suffix = "Termin" if recurrence_count == 1 else "Termine"
                lines.append(f"Noch {recurrence_count} weitere {suffix}")

        if recurrence_until:
            lines.append(f"Bis {discord.utils.format_dt(recurrence_until, 'f')}")

        if not lines:
            return None
        return "\n".join(lines)
    
    def fill_fields_from_db_row(self, row):
        """Populate fields from a SQLite row."""
        self.fields[FieldName.TITLE]._value = row["title"]
        self.fields[FieldName.DESCRIPTION]._value = row["description"]
        start_str = row["start_at"]
        if start_str:
            try:
                self.fields[FieldName.START]._value = datetime.fromisoformat(start_str)
            except Exception:
                pass
        end_str = row["end_at"]
        if end_str:
            try:
                self.fields[FieldName.END]._value = datetime.fromisoformat(end_str)
            except Exception:
                pass
        location_parts = [row["location_name"], row["location_address"]]
        location_str = " ".join(p for p in location_parts if p)
        if location_str:
            try:
                self.fields[FieldName.LOCATION].value = location_str
            except Exception as e:
                log.error(f"Could not resolve location '{location_str}': {e}")
        self.fields[FieldName.FEE]._value = row["fee_eur"]
        formats_json = row["formats_json"]
        self.fields[FieldName.FORMATS]._value = json.loads(formats_json) if formats_json else None
        recurrence_rule = row["recurrence_rule"]
        if recurrence_rule:
            recurrence_label = RECURRENCE_LABEL_MAP.get(recurrence_rule)
            if recurrence_label:
                self.fields[FieldName.RECURRING]._value = [recurrence_label]
        recurrence_until = row["recurrence_until"]
        if recurrence_until:
            try:
                self.fields[FieldName.RECURRING_UNTIL]._value = datetime.fromisoformat(recurrence_until)
            except Exception:
                pass
        self.fields[FieldName.RECURRING_COUNT]._value = row["recurrence_count"]
        event_type = row["event_type"]
        self.fields[FieldName.TYPE]._value = [event_type] if event_type else None
        self.fields[FieldName.URL]._value = row["url"]
        self.fields[FieldName.IMAGE]._value = row["image_url"]

    def save_to_db(self) -> int:
        from modules import paper_events_db

        def _long_name(component) -> str:
            if isinstance(component, dict):
                return component.get("long_name", "")
            return ""

        conn = paper_events_db.connect()
        paper_events_db.init_db(conn)
        location: gmaps.Location | None = self.fields[FieldName.LOCATION].value
        start = self.fields[FieldName.START].value
        end = self.fields[FieldName.END].value
        event_type_list = self.fields[FieldName.TYPE].value
        recurrence_list = self.fields[FieldName.RECURRING].value
        recurrence_label = recurrence_list[0] if recurrence_list else None
        recurrence_rule = RECURRENCE_RULE_MAP.get(recurrence_label)
        is_recurring = 1 if recurrence_rule else 0
        recurrence_until = self.fields[FieldName.RECURRING_UNTIL].value if is_recurring else None
        recurrence_count = self.fields[FieldName.RECURRING_COUNT].value if is_recurring else None
        event_data = {
            "server_id": str(self.guild.id),
            "thread_id": str(self.thread.id) if self.thread else None,
            "author_id": str(self.author.id) if self.author else None,
            "author_name": (self.author.display_name or self.author.name) if self.author else None,
            "title": self.build_title(),
            "description": self.fields[FieldName.DESCRIPTION].value or None,
            "start_at": start.isoformat() if start else None,
            "end_at": end.isoformat() if end else None,
            "location_name": location.name if location else None,
            "location_address": location.formatted_address if location else None,
            "location_city": _long_name(location.city) if location else None,
            "location_country": _long_name(location.country) if location else None,
            "location_lat": location.coord.lat if location and location.coord else None,
            "location_lng": location.coord.lng if location and location.coord else None,
            "fee_eur": self.fields[FieldName.FEE].value,
            "formats": self.fields[FieldName.FORMATS].value or [],
            "event_type": event_type_list[0] if event_type_list else None,
            "is_recurring": is_recurring,
            "recurrence_rule": recurrence_rule,
            "recurrence_until": recurrence_until.isoformat() if recurrence_until else None,
            "recurrence_count": recurrence_count,
            "url": self.fields[FieldName.URL].value,
            "image_url": self.fields[FieldName.IMAGE].value,
        }
        return paper_events_db.upsert_event(conn, event_data)

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
        ics_file = ics.create_ics_file(f"tmp/{title}.ics", title, self.fields[FieldName.START].value, self.fields[FieldName.END].value, description=self.fields[FieldName.DESCRIPTION].value, location=location.formatted_address if location else None)
        if ics_file:
            files.append(discord.File(ics_file, filename=ics_file))
        return files
    
    def construct_thread_title(self):
        start:datetime|None = self.fields[FieldName.START].value
        start_str = start.strftime('%d.%m.%Y') if start else f'<{FieldName.START.value}>'
        
        title = self.build_title()
        location:gmaps.Location|None = self.fields[FieldName.LOCATION].value
        if location:
            city_name = location.city.get("long_name", "") if isinstance(location.city, dict) else ""
            location_name = location.name or location.formatted_address or "Unbekannter Ort"
            location_str = f"{location_name} in {city_name}" if city_name else location_name
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
        if not preview and self.author:
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
        recurrence_list = self.fields[FieldName.RECURRING].value
        recurrence = recurrence_list[0] if recurrence_list else None
        if recurrence and recurrence != "Keine":
            embed.add_field(name=FieldName.RECURRING.value, value=recurrence, inline=True)
        recurrence_until = self.fields[FieldName.RECURRING_UNTIL].value
        if recurrence_until:
            embed.add_field(name=FieldName.RECURRING_UNTIL.value, value=discord.utils.format_dt(recurrence_until, 'f'), inline=True)
        recurrence_count = self.fields[FieldName.RECURRING_COUNT].value
        if recurrence_count:
            embed.add_field(name=FieldName.RECURRING_COUNT.value, value=str(recurrence_count), inline=True)
        recurrence_status = self._build_recurrence_status_text()
        if recurrence_status:
            embed.add_field(name="Wiederholung Status", value=recurrence_status, inline=False)
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
