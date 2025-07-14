import os
import json
from dotenv import load_dotenv, dotenv_values
from pathlib import Path
import pytz

TIMEZONE = pytz.timezone("Europe/Berlin")

load_dotenv()
# load_dotenv("config.env")

CONFIG_PATH = Path("config.env")
config = dotenv_values(dotenv_path=CONFIG_PATH)

def save_to_env(key, value):
    config[key] = value
    lines = CONFIG_PATH.read_text().splitlines() if CONFIG_PATH.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n")

def get_int_from_env(key:str, env_values=None) -> int:
    if env_values:
        value = env_values.get(key)
    else:
        value = os.getenv(key)
    if value:
        return int(value)
    return None

def get_bool_from_env(key: str, default: bool = False) -> bool:
    value = os.getenv(key, str(default)).lower()  # Default to 'false' if key is not found
    return value in ("true", "1", "yes", "on")

def get_dict_from_env(key: str, default: str = "{}") -> dict[str, str]:
    return json.loads(os.getenv(key, default))

RR_GREEN = 0x25584F
COLORLESS_EMBED = 0x36393F
SPIKE_RED = 0x9C1D26
DEBUG = get_bool_from_env("DEBUG")
STATE_TAGS = get_dict_from_env("STATE_TAGS")
GUILD_ID = get_int_from_env("GUILD")
EVENT_DATABASE_ID:str = os.getenv("EVENT_DATABASE_ID")
AREA_DATABASE_ID = os.getenv("AREA_DATABASE_ID")
AUA_DATABASE_ID = os.getenv("DATABASE_ID_AUA")
CHANNEL_PAPER_EVENTS_ID = get_int_from_env("CHANNEL_PAPER_EVENTS")
GMAPS_TOKEN = os.getenv("GMAPS_TOKEN")

SPELLTABLE_CALENDAR_CHANNEL_ID = get_int_from_env("SPELLTABLE_CALENDAR_CHANNEL_ID")
SPELLTABLE_CALENDAR_MESSAGE_ID = get_int_from_env("SPELLTABLE_CALENDAR_MESSAGE_ID", config)
CREATE_TOURNAMENT_COMMAND_ID = get_int_from_env("CREATE_TOURNAMENT_COMMAND_ID")
LOG_WEBHOOK = os.getenv("LOG_WEBHOOK")
API_KEY_IMGBB = os.getenv("API_KEY_IMGBB")
CHANNEL_NEWS_DE = get_int_from_env("CHANNEL_NEWS_DE")
CHANNEL_NEWS_EN = get_int_from_env("CHANNEL_NEWS_EN")
API_KEY_YOUTUBE = os.getenv("API_KEY_YOUTUBE")