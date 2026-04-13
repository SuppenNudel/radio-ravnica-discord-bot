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
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

CHANNEL_PAPER_EVENTS_ID = env.CHANNEL_PAPER_EVENTS_ID
DEBUG = env.DEBUG

PAPER_EVENT_FORMATS = [
    "Standard", "Pioneer", "Modern", "Legacy", "Vintage", "Pauper",
    "Commander", "cEDH", "Peasant", "Draft", "Sealed", "Prerelease",
]
PAPER_EVENT_TYPES = [
    "FNM", "RCQ", "Regional Championship", "Game Day", "Prerelease",
    "Launch Party", "Store Championship", "Sunday Challenge", "Andere(r)",
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

# Weekday options for the recurring event modal
WEEKDAY_OPTIONS = [
    ("Montag", "MO"),
    ("Dienstag", "TU"),
    ("Mittwoch", "WE"),
    ("Donnerstag", "TH"),
    ("Freitag", "FR"),
    ("Samstag", "SA"),
    ("Sonntag", "SU"),
]

# Which week(s) of the month
WEEK_POSITION_OPTIONS = [
    ("1. Woche", "1"),
    ("2. Woche", "2"),
    ("3. Woche", "3"),
    ("4. Woche", "4"),
    ("Letzte Woche", "-1"),
]

# Interval options (every week / every 2 weeks, or none)
INTERVAL_OPTIONS = [
    ("Jede Woche", "1"),
    ("Alle 2 Wochen", "2"),
]

ICAL_WEEKDAY_SHORT = {"MO": "Mo", "TU": "Di", "WE": "Mi", "TH": "Do", "FR": "Fr", "SA": "Sa", "SU": "So"}
ICAL_WEEKDAY_TO_PYTHON = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
WEEK_POSITION_LABEL = {"1": "1.", "2": "2.", "3": "3.", "4": "4.", "-1": "Letzte"}


def build_recurrence_rule(interval_val: str, weekdays: list[str], week_positions: list[str]) -> str | None:
    """Build an RRULE string from modal select values."""
    if not weekdays:
        return None
    if week_positions:
        # Monthly: specific week-of-month + weekday → FREQ=MONTHLY;BYDAY=2WE,4WE
        sorted_positions = sorted(week_positions, key=lambda p: int(p))
        byday_parts = [f"{pos}{day}" for pos in sorted_positions for day in weekdays]
        return f"FREQ=MONTHLY;BYDAY={','.join(byday_parts)}"

    interval = int(interval_val) if interval_val else 1
    byday = ",".join(weekdays)
    return f"FREQ=WEEKLY;INTERVAL={interval};BYDAY={byday}"


def parse_recurrence_rule(rule: str | None) -> dict:
    """Parse an RRULE string back into component parts for the modal selects.

    Returns a dict with keys: 'interval' (str), 'weekdays' (list[str]), 'week_positions' (list[str]).
    'interval' is '0' when there is no recurrence.
    """
    result: dict = {"interval": "", "weekdays": [], "week_positions": []}
    if not rule:
        return result
    rule_parts: dict[str, str] = {}
    for part in rule.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            rule_parts[k.upper()] = v.upper()
    freq = rule_parts.get("FREQ", "")
    try:
        interval = int(rule_parts.get("INTERVAL", "1"))
    except ValueError:
        interval = 1
    byday = rule_parts.get("BYDAY", "")
    if freq == "WEEKLY":
        result["interval"] = str(interval)
        if byday:
            result["weekdays"] = re.findall(r"[A-Z]{2}", byday)
    elif freq == "MONTHLY":
        result["interval"] = ""
        if byday:
            matches = re.findall(r"(-?\d+)([A-Z]{2})", byday)
            positions: list[str] = []
            days: list[str] = []
            for pos, day in matches:
                if pos not in positions:
                    positions.append(pos)
                if day not in days:
                    days.append(day)
            result["week_positions"] = positions
            result["weekdays"] = days
    return result


def recurrence_rule_to_display(rule: str | None) -> str | None:
    """Convert an RRULE string to a short human-readable German label."""
    if not rule or rule == "Keine":
        return None
    label = RECURRENCE_LABEL_MAP.get(rule)
    if label:
        return label
    parsed = parse_recurrence_rule(rule)
    interval_val = parsed["interval"]
    weekdays = parsed["weekdays"]
    week_positions = parsed["week_positions"]
    if not weekdays:
        return None
    day_str = ", ".join(ICAL_WEEKDAY_SHORT.get(d, d) for d in weekdays)
    if week_positions:
        pos_str = ", ".join(WEEK_POSITION_LABEL.get(p, p) for p in week_positions)
        return f"Monatlich: {pos_str} {day_str}"
    else:
        interval = int(interval_val) if interval_val else 1
        if interval == 1:
            return f"Wöchentlich: {day_str}"
        else:
            return f"Alle {interval} Wochen: {day_str}"

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

def parse_fee_amount(message_or_text: float | int | discord.Message | str):
    if type(message_or_text) in [float, int]:
        return float(message_or_text)
    if type(message_or_text) == discord.Message:
        message_or_text = message_or_text.content
    normalized = str(message_or_text).strip().replace(",", ".")
    return float(normalized)

def parse_list(message_or_text):
    if type(message_or_text) == list:
        return message_or_text
    if type(message_or_text) == str:
        return [part.strip() for part in re.split(r'[;,|]', message_or_text)]


def sanitize_filename(name: str, fallback: str = "event") -> str:
    """Sanitize a string for safe use as a Windows filename."""
    if not name:
        return fallback
    # Replace invalid Windows filename characters and control chars
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    sanitized = sanitized.strip().rstrip(".")
    if not sanitized:
        sanitized = fallback

    # Avoid reserved Windows device names
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    if sanitized.upper() in reserved:
        sanitized = f"_{sanitized}"

    return sanitized[:120]

# Defining field types
FIELD_TYPE_TIME = FieldType(parse_datetime)
FIELD_TYPE_TEXT = FieldType()
FIELD_TYPE_TEXT_LONG = FieldType(long=True)
FIELD_TYPE_LIST = FieldType(parse_list)
FIELD_TYPE_NUMBER = FieldType(parse_int)
FIELD_TYPE_FEE_AMOUNT = FieldType(parse_fee_amount)
FIELD_TYPE_IMAGE = FieldType(parse_image)
FIELD_TYPE_LOCATION = FieldType(parse_location)

CURRENCY_OPTIONS = [
    ("Euro", "EUR"),
    ("Schweizer Franken", "CHF"),
    ("Dollar", "USD"),
]

CURRENCY_SYMBOLS = {
    "EUR": "€",
    "CHF": "CHF",
    "USD": "$",
}

class FieldName(Enum):
    TYPE = "Typ"
    TITLE = "Titel"
    START = "Start"
    LOCATION = "Veranstaltungsort"
    FORMATS = "Format(e)"
    RECURRING = "Wiederholung"
    RECURRING_UNTIL = "Wiederholung bis"
    RECURRING_COUNT = "Anzahl weiterer Termine"
    END = "Ende"
    FEE = "Teilnahmegebühr"
    FEE_FROM = "Preis von"
    FEE_TO = "Preis bis"
    FEE_CURRENCY = "Währung"
    DESCRIPTION = "Beschreibung"
    URL = "Link"
    IMAGE = "Flugblatt/Bild"


def format_fee_amount(value: float | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return (f"{value:g}").replace(".", ",")


def build_fee_display(fee_from: float | None, fee_to: float | None, currency: str | None) -> str | None:
    if fee_from is None and fee_to is None:
        return None

    currency_label = CURRENCY_SYMBOLS.get(currency or "EUR", currency or "EUR")
    from_text = format_fee_amount(fee_from)
    to_text = format_fee_amount(fee_to)

    if from_text and to_text:
        if fee_from == fee_to:
            return f"{from_text} {currency_label}"
        return f"{from_text} bis {to_text} {currency_label}"
    if from_text:
        return f"{from_text} {currency_label}"
    return f"bis {to_text} {currency_label}"

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
    
def _fetch_og_image_sync(url: str) -> str | None:
    """Synchronously fetch the best preview image (og:image / twitter:image) from a webpage."""
    try:
        response = requests.get(url, timeout=10, allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        page_url = response.url or url
        for attr in (
            {"property": "og:image"},
            {"name": "og:image"},
            {"name": "twitter:image"},
            {"property": "twitter:image"},
        ):
            tag = soup.find("meta", attrs=attr)
            if tag and tag.get("content"):
                return urljoin(page_url, tag["content"])

        min_favicon_resolution = 64 * 64
        best_favicon_url = None
        best_favicon_resolution = -1
        icon_links = soup.find_all("link", rel=lambda rel: rel and "icon" in rel.lower())
        for link in icon_links:
            href = link.get("href")
            if not href or href.startswith("data:"):
                continue

            resolution = 0
            sizes_raw = (link.get("sizes") or "").strip().lower()
            for token in sizes_raw.split():
                if "x" not in token:
                    continue
                try:
                    width, height = token.split("x", 1)
                    resolution = max(resolution, int(width) * int(height))
                except Exception:
                    continue

            if resolution >= best_favicon_resolution:
                best_favicon_resolution = resolution
                best_favicon_url = urljoin(page_url, href)

        if best_favicon_url and best_favicon_resolution >= min_favicon_resolution:
            return best_favicon_url

        return urljoin(page_url, "/favicon.ico")
    except Exception as exc:
        log.warning(f"fetch_og_image failed for {url}: {exc}")
    return None


async def fetch_og_image(url: str) -> str | None:
    """Async wrapper — runs the blocking HTTP fetch in a thread."""
    return await asyncio.to_thread(_fetch_og_image_sync, url)


async def edit_event_post(event:"PaperEvent", interaction_or_message:discord.Interaction|discord.Message, view):
    await event.resolve_image()
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

class EditStartEndModal(discord.ui.Modal):
    def __init__(self, event: "PaperEvent"):
        super().__init__(title="Zeit (Start & Ende)")
        self.event = event
        self.start_field = event.fields[FieldName.START]
        self.end_field = event.fields[FieldName.END]

        start_value = str(self.start_field.value) if self.start_field.value else ""
        end_value = str(self.end_field.value) if self.end_field.value else ""

        self.start_input = discord.ui.InputText(
            label=FieldName.START.value,
            placeholder=self.start_field.description,
            required=self.start_field.mandatory,
            value=start_value,
            style=discord.InputTextStyle.singleline,
        )
        self.end_input = discord.ui.InputText(
            label=FieldName.END.value,
            placeholder=self.end_field.description,
            required=False,
            value=end_value,
            style=discord.InputTextStyle.singleline,
        )

        self.add_item(self.start_input)
        self.add_item(self.end_input)

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            self.start_field.value = self.start_input.value
            end_input = self.end_input.value.strip() if self.end_input.value else ""
            self.end_field.value = end_input if end_input else None
            await edit_event_post(self.event, interaction, EditTourneyView(self.event))
        except Exception as e:
            await interaction.followup.send(str(e), ephemeral=True)


class EditFeeModal(discord.ui.DesignerModal):
    def __init__(self, event: "PaperEvent"):
        super().__init__(title=FieldName.FEE.value)
        self.event = event
        self.from_field = event.fields[FieldName.FEE_FROM]
        self.to_field = event.fields[FieldName.FEE_TO]
        self.currency_field = event.fields[FieldName.FEE_CURRENCY]
        self.current_currency = self.currency_field.value or "EUR"

        self.from_input = discord.ui.InputText(
            placeholder=self.from_field.description,
            required=False,
            value=format_fee_amount(self.from_field.value) or "",
            style=discord.InputTextStyle.singleline,
        )
        self.to_input = discord.ui.InputText(
            placeholder=self.to_field.description,
            required=False,
            value=format_fee_amount(self.to_field.value) or "",
            style=discord.InputTextStyle.singleline,
        )
        self.currency_select = discord.ui.StringSelect(
            custom_id="pe_fee_currency",
            placeholder="Währung wählen...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=value, default=value == self.current_currency)
                for label, value in CURRENCY_OPTIONS
            ],
        )

        self.add_item(discord.ui.Label(FieldName.FEE_FROM.value, self.from_input, description="Optional; z.B. 10 oder 12,50"))
        self.add_item(discord.ui.Label(FieldName.FEE_TO.value, self.to_input, description="Optional; z.B. 25 oder 29,90"))
        self.add_item(discord.ui.Label(FieldName.FEE_CURRENCY.value, self.currency_select, description="Standard ist Euro"))

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()

            def _get_select_values_from_payload(select_custom_id: str) -> list[str]:
                data = interaction.data if isinstance(interaction.data, dict) else {}
                components = data.get("components", []) if isinstance(data, dict) else []
                for row in components:
                    row_components = row.get("components", []) if isinstance(row, dict) else []
                    for component in row_components:
                        if not isinstance(component, dict):
                            continue
                        if component.get("custom_id") == select_custom_id:
                            values = component.get("values", [])
                            if isinstance(values, list):
                                return [str(v) for v in values]
                            return []
                return []

            fee_from_raw = self.from_input.value.strip() if self.from_input.value else ""
            fee_to_raw = self.to_input.value.strip() if self.to_input.value else ""
            currency_values = _get_select_values_from_payload("pe_fee_currency") or list(self.currency_select.values or []) or [self.current_currency]
            currency_value = currency_values[0] if currency_values else "EUR"

            fee_from = parse_fee_amount(fee_from_raw) if fee_from_raw else None
            fee_to = parse_fee_amount(fee_to_raw) if fee_to_raw else None

            if fee_from is not None and fee_to is not None and fee_to < fee_from:
                raise Exception("'Preis bis' darf nicht kleiner als 'Preis von' sein")

            self.from_field._value = fee_from
            self.to_field._value = fee_to
            self.currency_field._value = currency_value

            await edit_event_post(self.event, interaction, EditTourneyView(self.event))
        except Exception as e:
            await interaction.followup.send(str(e), ephemeral=True)

class EditRecurringModal(discord.ui.DesignerModal):
    def __init__(self, event: "PaperEvent"):
        super().__init__(title="Wiederholung")
        self.event = event
        self.recurring_field = event.fields[FieldName.RECURRING]
        self.until_field = event.fields[FieldName.RECURRING_UNTIL]
        self.count_field = event.fields[FieldName.RECURRING_COUNT]

        # Resolve current rule (may be a legacy label or a raw RRULE string)
        current_raw = self.recurring_field.value
        current_rule = (current_raw[0] if isinstance(current_raw, list) else current_raw) or ""
        if current_rule and not current_rule.startswith("FREQ="):
            current_rule = RECURRENCE_RULE_MAP.get(current_rule, "")
        parsed = parse_recurrence_rule(current_rule)
        current_weekdays = parsed["weekdays"]
        current_positions = parsed["week_positions"]
        current_interval = parsed["interval"]

        self.weekday_select = discord.ui.StringSelect(
            custom_id="pe_recur_weekdays",
            placeholder="Wochentage wählen...",
            min_values=0,
            max_values=len(WEEKDAY_OPTIONS),
            required=False,
            options=[
                discord.SelectOption(label=label, value=value, default=value in current_weekdays)
                for label, value in WEEKDAY_OPTIONS
            ],
        )
        self.week_position_select = discord.ui.StringSelect(
            custom_id="pe_recur_week_positions",
            placeholder="Woche(n) im Monat (leer = jede Woche)...",
            min_values=0,
            max_values=len(WEEK_POSITION_OPTIONS),
            required=False,
            options=[
                discord.SelectOption(label=label, value=value, default=value in current_positions)
                for label, value in WEEK_POSITION_OPTIONS
            ],
        )
        self.interval_select = discord.ui.StringSelect(
            custom_id="pe_recur_interval",
            placeholder="Häufigkeit...",
            min_values=0,
            max_values=1,
            required=False,
            options=[
                discord.SelectOption(label=label, value=value, default=value == current_interval)
                for label, value in INTERVAL_OPTIONS
            ],
        )
        self.until_input = discord.ui.InputText(
            placeholder=self.until_field.description,
            required=False,
            value=str(self.until_field.value) if self.until_field.value else "",
            style=discord.InputTextStyle.singleline,
        )
        self.count_input = discord.ui.InputText(
            placeholder=self.count_field.description,
            required=False,
            value=str(self.count_field.value) if self.count_field.value else "",
            style=discord.InputTextStyle.singleline,
        )

        self.add_item(discord.ui.Label(
            "Wochentage",
            self.weekday_select,
            description="Pflichtfeld fur jede Wiederholung",
        ))
        self.add_item(discord.ui.Label(
            "Woche(n) im Monat",
            self.week_position_select,
            description="Optional; zusammen mit Wochentagen = monatliche Wiederholung",
        ))
        self.add_item(discord.ui.Label(
            "Häufigkeit",
            self.interval_select,
            description="Optional fur wochentliche Wiederholung; leer = jede Woche",
        ))
        self.add_item(discord.ui.Label(
            FieldName.RECURRING_UNTIL.value,
            self.until_input,
            description="Optionales Enddatum der Serie",
        ))
        self.add_item(discord.ui.Label(
            FieldName.RECURRING_COUNT.value,
            self.count_input,
            description="Optionales Limit; wenn beides gesetzt ist, endet die Serie beim früheren Limit",
        ))

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()

            def _get_select_values_from_payload(select_custom_id: str) -> list[str]:
                data = interaction.data if isinstance(interaction.data, dict) else {}
                components = data.get("components", []) if isinstance(data, dict) else []
                for row in components:
                    row_components = row.get("components", []) if isinstance(row, dict) else []
                    for component in row_components:
                        if not isinstance(component, dict):
                            continue
                        if component.get("custom_id") == select_custom_id:
                            values = component.get("values", [])
                            if isinstance(values, list):
                                return [str(v) for v in values]
                            return []
                return []

            interval_values = _get_select_values_from_payload("pe_recur_interval") or list(self.interval_select.values or [])
            weekday_values = _get_select_values_from_payload("pe_recur_weekdays") or list(self.weekday_select.values or [])
            week_position_values = _get_select_values_from_payload("pe_recur_week_positions") or list(self.week_position_select.values or [])

            interval_val = interval_values[0] if interval_values else ""
            weekdays = weekday_values
            week_positions = week_position_values
            until_val = self.until_input.value.strip() if self.until_input.value else ""
            count_val = self.count_input.value.strip() if self.count_input.value else ""

            has_recurrence_input = bool(weekdays or week_positions or interval_values or until_val or count_val)

            if has_recurrence_input and not weekdays:
                await interaction.followup.send(
                    "Bitte mindestens einen Wochentag wählen, damit die Wiederholung gespeichert wird.",
                    ephemeral=True,
                )
                return

            rule = build_recurrence_rule(interval_val, weekdays, week_positions)

            self.recurring_field._value = [rule] if rule else None

            if not rule:
                self.until_field._value = None
                self.count_field._value = None
            else:
                self.until_field.value = until_val if until_val else None
                self.count_field.value = count_val if count_val else None

            await edit_event_post(self.event, interaction, EditTourneyView(self.event))
        except Exception as e:
            await interaction.followup.send(str(e), ephemeral=True)

def make_callback(event, field:InputField):
    async def edit_field_callback(interaction:discord.Interaction):
        if field.name in {FieldName.FEE_FROM, FieldName.FEE_TO, FieldName.FEE_CURRENCY}:
            modal = EditFeeModal(event)
            await interaction.response.send_modal(modal)
        elif field.notion_column:
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
        
        # Remove the view immediately to prevent user interaction during processing
        await interaction.edit_original_response(view=None)
        
        orig_view:EditTourneyView = self.view

        # Ensure all mandatory fields are filled
        if not all(field.value for field in self.event.fields.values() if field.mandatory):
            await interaction.edit_original_response(content="Not all mandatory fields are filled!", embeds=[], attachments=[])
            return

        try:
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
                    attachments=[]
                )
            else:
                await orig_view.send_forum_post(interaction)
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Fehler: {str(e)}",
                embeds=[],
                attachments=[]
            )

class EditTourneyView(discord.ui.View):
    def __init__(self, event:"PaperEvent"):
        super().__init__(timeout=None)
        self.event = event

        row_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        max_buttons_per_row = 5

        def resolve_row(preferred_rows: list[int]) -> int:
            for row in preferred_rows:
                if row_counts[row] < max_buttons_per_row:
                    return row
            for row in [0, 1, 2, 3]:
                if row_counts[row] < max_buttons_per_row:
                    return row
            return 3

        def add_field_button(button: discord.ui.Button, preferred_rows: list[int]):
            row = resolve_row(preferred_rows)
            button.row = row
            row_counts[row] += 1
            self.add_item(button)

        grouped_fields = {
            FieldName.START,
            FieldName.END,
            FieldName.RECURRING,
            FieldName.RECURRING_UNTIL,
            FieldName.RECURRING_COUNT,
            FieldName.FEE_FROM,
            FieldName.FEE_TO,
            FieldName.FEE_CURRENCY,
        }

        for field_name, field in self.event.fields.items():
            if field_name in grouped_fields:
                continue
            label = field.name.value
            is_starred = (
                field.mandatory
                or (
                    field_name in [FieldName.TITLE, FieldName.TYPE]
                    and not (self.event.fields[FieldName.TITLE].value or self.event.fields[FieldName.TYPE].value)
                )
            )
            is_primary_row = field.mandatory or field_name in [FieldName.TITLE, FieldName.TYPE]
            if is_starred:
                label += "*"
            label += " "+field.status_emoji(self.event.fields)
            button = discord.ui.Button(label=label, emoji=field.icon)
            button.callback = make_callback(event, field)
            add_field_button(button, [0, 1] if is_primary_row else [1, 0])

        start_field = self.event.fields[FieldName.START]
        end_field = self.event.fields[FieldName.END]
        start_end_label = f"{FieldName.START.value} & {FieldName.END.value}"
        if start_field.mandatory:
            start_end_label += "*"
        start_end_label += " " + start_field.status_emoji(self.event.fields)
        start_end_button = discord.ui.Button(label=start_end_label, emoji="⏰")

        async def start_end_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(EditStartEndModal(self.event))

        start_end_button.callback = start_end_callback
        add_field_button(start_end_button, [0, 1])

        recurring_field = self.event.fields[FieldName.RECURRING]
        recurring_until_field = self.event.fields[FieldName.RECURRING_UNTIL]
        recurring_count_field = self.event.fields[FieldName.RECURRING_COUNT]
        recurring_label = "Wiederholung"
        if recurring_field.value or recurring_until_field.value or recurring_count_field.value:
            recurring_label += " ✅"
        recurring_button = discord.ui.Button(label=recurring_label, emoji="🔁")

        async def recurring_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(EditRecurringModal(self.event))

        recurring_button.callback = recurring_callback
        add_field_button(recurring_button, [1, 0])

        fee_from_field = self.event.fields[FieldName.FEE_FROM]
        fee_to_field = self.event.fields[FieldName.FEE_TO]
        fee_currency_field = self.event.fields[FieldName.FEE_CURRENCY]
        fee_label = FieldName.FEE.value
        if fee_from_field.value is not None or fee_to_field.value is not None:
            fee_label += " ✅"
        elif fee_currency_field.value and fee_currency_field.value != "EUR":
            fee_label += " ⚙️"
        fee_button = discord.ui.Button(label=fee_label, emoji="💸")

        async def fee_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(EditFeeModal(self.event))

        fee_button.callback = fee_callback
        add_field_button(fee_button, [1, 0])

        # Submit Button
        self.submit_button = SubmitButton(event)
        self.submit_button.row = 4
        self.add_item(self.submit_button)
        self.add_cancel_button(self)

    def add_cancel_button(self, view: discord.ui.View):
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red, row=4)

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
        try:
            guild = self.event.guild
            forum_channel = await discord.utils.get_or_fetch(guild, "channel", CHANNEL_PAPER_EVENTS_ID, default=None)

            if not isinstance(forum_channel, discord.ForumChannel):
                await interaction.edit_original_response(content="This is not a forum channel!", embeds=[], attachments=[])
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
                content=f"Event Post erstellt: {thread.jump_url}",
                embeds=[],
                attachments=[]
            )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Fehler beim Erstellen des Posts: {str(e)}",
                embeds=[],
                attachments=[]
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
        await self.event.resolve_image()
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
            InputField(FieldName.RECURRING, FIELD_TYPE_LIST, icon="🔁", description="Optional", notion_column="Wiederholung"),
            InputField(FieldName.RECURRING_UNTIL, FIELD_TYPE_TIME, icon="📅", description="z.B. am 24.12.2025 um 18:00 Uhr, in 5 Stunden"),
            InputField(FieldName.RECURRING_COUNT, FIELD_TYPE_NUMBER, icon="🔢", description="eine Ganzzahl, wie 3, 4, 5 etc"),
            InputField(FieldName.LOCATION, FIELD_TYPE_LOCATION, mandatory=True, icon="📍", description="Wo das Event/Turnier stattfindet"),
            InputField(FieldName.FEE_FROM, FIELD_TYPE_FEE_AMOUNT, icon="💸", description="z.B. 10 oder 12,50"),
            InputField(FieldName.FEE_TO, FIELD_TYPE_FEE_AMOUNT, icon="💸", description="z.B. 25 oder 29,90"),
            InputField(FieldName.FEE_CURRENCY, FIELD_TYPE_TEXT, icon="💱", description="EUR, CHF oder USD"),
            InputField(FieldName.FORMATS, FieldType(parser=parse_list, max_items="max"), icon="🎮", description=f"Kommasepariert, welche(s) Format(e) gespielt wird/werden", mandatory=True, notion_column="Format(e)"),
            InputField(FieldName.TYPE, FIELD_TYPE_LIST, icon="🏷️", description="Kommasepariert, zum Beipsiel FNM, RCQ, Prerelease...", notion_column="Event Typ"),
            InputField(FieldName.URL, FIELD_TYPE_TEXT, icon="🔗", description="Der Link zur Veranstaltung"),
            InputField(FieldName.IMAGE, FIELD_TYPE_IMAGE, icon="🖼️", description="URL/Link zum Bild"),
        ]
        self.fields:dict[FieldName, InputField] = {field.name: field for field in field_list}
        self.fields[FieldName.FEE_CURRENCY]._value = "EUR"
        self._resolved_image_url: str | None = None
        self._image_cache_key = None

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
        fee_from = row["fee_from"] if "fee_from" in row.keys() else None
        fee_to = row["fee_to"] if "fee_to" in row.keys() else None
        fee_currency = row["fee_currency"] if "fee_currency" in row.keys() else None
        legacy_fee = row["fee_eur"] if "fee_eur" in row.keys() else None
        self.fields[FieldName.FEE_FROM]._value = fee_from if fee_from is not None else legacy_fee
        self.fields[FieldName.FEE_TO]._value = fee_to
        self.fields[FieldName.FEE_CURRENCY]._value = fee_currency or "EUR"
        formats_json = row["formats_json"]
        self.fields[FieldName.FORMATS]._value = json.loads(formats_json) if formats_json else None
        recurrence_rule = row["recurrence_rule"]
        if recurrence_rule:
            recurrence_label = RECURRENCE_LABEL_MAP.get(recurrence_rule)
            if recurrence_label:
                self.fields[FieldName.RECURRING]._value = [recurrence_label]
            else:
                # Store the raw RRULE string for BYDAY-style rules
                self.fields[FieldName.RECURRING]._value = [recurrence_rule]
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
        if recurrence_label and recurrence_label.startswith("FREQ="):
            recurrence_rule = recurrence_label  # already a raw RRULE string
        else:
            recurrence_rule = RECURRENCE_RULE_MAP.get(recurrence_label)
        is_recurring = 1 if recurrence_rule else 0
        recurrence_until = self.fields[FieldName.RECURRING_UNTIL].value if is_recurring else None
        recurrence_count = self.fields[FieldName.RECURRING_COUNT].value if is_recurring else None
        fee_from = self.fields[FieldName.FEE_FROM].value
        fee_to = self.fields[FieldName.FEE_TO].value
        fee_currency = self.fields[FieldName.FEE_CURRENCY].value or "EUR"
        legacy_fee_eur = fee_from if fee_currency == "EUR" and (fee_to is None or fee_to == fee_from) else None
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
            "fee_eur": legacy_fee_eur,
            "fee_from": fee_from,
            "fee_to": fee_to,
            "fee_currency": fee_currency,
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
        safe_title = sanitize_filename(title)
        ics_file = ics.create_ics_file(f"tmp/{safe_title}.ics", title, self.fields[FieldName.START].value, self.fields[FieldName.END].value, description=self.fields[FieldName.DESCRIPTION].value, location=location.formatted_address if location else None)
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
        if url := self.fields[FieldName.URL].value:
            content += f"\n\nWeitere Informationen: {url}"
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
    
    async def resolve_image(self) -> str | None:
        """Pre-resolve and cache the best image URL for this event.

        Priority: explicit IMAGE field > og:image from venue website > og:image from event URL.
        Result is cached per (venue_url, event_url) pair to avoid redundant HTTP requests.
        """
        explicit = self.fields[FieldName.IMAGE].value
        if explicit:
            self._resolved_image_url = explicit
            self._image_cache_key = None  # reset so removing explicit triggers a fresh auto-fetch
            return explicit

        location: gmaps.Location = self.fields[FieldName.LOCATION].value
        venue_url = getattr(location, "url", None) if location else None
        event_url = self.fields[FieldName.URL].value
        cache_key = (venue_url, event_url)

        if self._image_cache_key == cache_key:
            return self._resolved_image_url

        self._image_cache_key = cache_key
        self._resolved_image_url = None

        for url in filter(None, [venue_url, event_url]):
            img = await fetch_og_image(url)
            if img:
                self._resolved_image_url = img
                return img

        return None

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
        image = self.fields[FieldName.IMAGE].value or self._resolved_image_url
        if image:
            embed.set_image(url=image)
        end = self.fields[FieldName.END].value
        if end:
            end_value = discord.utils.format_dt(end, get_timestamp_style(start, end))
            embed.add_field(name=FieldName.END.value, value=end_value, inline=True)
        if start and end:
            embed.add_field(name="Dauer", value=dti.human_delta(end, start), inline=True)

        fee_text = build_fee_display(
            self.fields[FieldName.FEE_FROM].value,
            self.fields[FieldName.FEE_TO].value,
            self.fields[FieldName.FEE_CURRENCY].value,
        )
        if fee_text:
            embed.add_field(name=FieldName.FEE.value, value=fee_text, inline=True)
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
            display = recurrence_rule_to_display(recurrence) or recurrence
            embed.add_field(name=FieldName.RECURRING.value, value=display, inline=True)
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
        await self.resolve_image()
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
        await self.resolve_image()
        embeds = []
        tourney_embed = self.construct_event_embed()
        if tourney_embed:
            embeds.append(tourney_embed)
        gmaps_embed = self.construct_gmaps_embed()
        if gmaps_embed:
            embeds.append(gmaps_embed)
        return await user.send(content=self.construct_content(preview=True), embeds=embeds, files=self.get_files(), view=EditTourneyView(self))
