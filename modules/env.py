import os
import json
from dotenv import load_dotenv

load_dotenv()

def get_int_from_env(key:str) -> int:
    value = os.getenv(key)
    return int(value)

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
CHANNEL_PAPER_EVENTS_ID = get_int_from_env("CHANNEL_PAPER_EVENTS")
GMAPS_TOKEN = os.getenv("GMAPS_TOKEN")