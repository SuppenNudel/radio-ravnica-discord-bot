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

PAPER_EVENTS_CALENDAR_CHANNEL_ID = (
    get_int_from_env("PAPER_EVENTS_CALENDAR_CHANNEL_ID_DEBUG", config)
    if DEBUG
    else get_int_from_env("PAPER_EVENTS_CALENDAR_CHANNEL_ID", config)
)
PAPER_EVENTS_CALENDAR_MESSAGE_ID = (
    get_int_from_env("PAPER_EVENTS_CALENDAR_MESSAGE_ID_DEBUG", config)
    if DEBUG
    else get_int_from_env("PAPER_EVENTS_CALENDAR_MESSAGE_ID", config)
)

SPELLTABLE_CALENDAR_CHANNEL_ID = get_int_from_env("SPELLTABLE_CALENDAR_CHANNEL_ID")
SPELLTABLE_CALENDAR_MESSAGE_ID = get_int_from_env("SPELLTABLE_CALENDAR_MESSAGE_ID", config)
CREATE_TOURNAMENT_COMMAND_ID = get_int_from_env("CREATE_TOURNAMENT_COMMAND_ID")
LOG_WEBHOOK = os.getenv("LOG_WEBHOOK")
API_KEY_IMGBB = os.getenv("API_KEY_IMGBB")
CHANNEL_NEWS_DE = get_int_from_env("CHANNEL_NEWS_DE")
CHANNEL_NEWS_EN = get_int_from_env("CHANNEL_NEWS_EN")
API_KEY_YOUTUBE = os.getenv("API_KEY_YOUTUBE")

# Load feature profiles from JSON
def load_cog_profiles() -> dict:
    """Load cog profiles from cog_profiles.json."""
    try:
        profiles_path = Path("cog_profiles.json")
        if profiles_path.exists():
            with open(profiles_path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    # Fallback empty dict
    return {}

COG_PROFILES = load_cog_profiles()

def get_git_branch_name() -> str:
    """Get current git branch name."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "main"

def get_active_cog_profile() -> dict:
    """Get cog loading profile for current branch."""
    # Check for explicit override
    profile_name = os.getenv("FEATURE_PROFILE")
    
    # If no override, try to match git branch to profile
    if not profile_name:
        branch = get_git_branch_name()
        # Try exact match, then try to find matching profile from branch name
        if branch in COG_PROFILES:
            profile_name = branch
        else:
            # Try to find a profile name within the branch name (e.g., "feature/feed" -> "feed")
            for prof_name in COG_PROFILES.keys():
                if prof_name in branch.lower():
                    profile_name = prof_name
                    break
    
    # Fall back to main profile
    if not profile_name or profile_name not in COG_PROFILES:
        profile_name = "full"
    
    return COG_PROFILES.get(profile_name, {"cogs_path": "cogs", "ignored_cogs": []})