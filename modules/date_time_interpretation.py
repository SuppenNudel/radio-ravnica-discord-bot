import dateparser
from datetime import datetime, timedelta
import os
import re
from dateutil.relativedelta import relativedelta
from babel.dates import format_timedelta
import pytz
from ezcord import log
from typing import Any, cast

settings = {
    'RETURN_AS_TIMEZONE_AWARE': True,
    'PREFER_DAY_OF_MONTH': 'first',
    'PREFER_DATES_FROM': 'future',
    'DATE_ORDER': 'DMY',
    'TIMEZONE': 'Europe/Berlin'
    # 'RELATIVE_BASE': None,
}

timezone = pytz.timezone("Europe/Berlin")

def parse_date(user_time_input) -> datetime | None:
    normalized_input = user_time_input.strip().lower()
    normalized_input = re.sub(r"\bam\s+(?=\d{1,2}\.)", "", normalized_input)
    normalized_input = re.sub(
        r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s*uhr\b",
        lambda match: f"{int(match.group(1)):02d}:{match.group(2) or '00'}",
        normalized_input,
    )
    explicit_time = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", normalized_input)
    normalized_input = re.sub(
        r"\b(?:am\s+)?abend(?:s)?\b",
        "" if explicit_time else "18:00",
        normalized_input,
    )
    normalized_input = " ".join(normalized_input.split())
    try:
        parsed_date = datetime.fromisoformat(normalized_input.upper())
    except ValueError:
        parsed_date = dateparser.parse(normalized_input, settings=cast(Any, settings), languages=["de"])
    if parsed_date:
        if parsed_date.tzinfo is None:
            parsed_date = timezone.localize(parsed_date)
        else:
            parsed_date = parsed_date.astimezone(timezone)
        log.debug(f"Parsed by dateparser: '{user_time_input}' -> {parsed_date}")
    else:
        log.debug(f"Failed to parse: '{user_time_input}'")
    
    return parsed_date

def human_delta(datetime2:datetime, datetime1:datetime, locale='de'):
    delta = relativedelta(datetime2, datetime1)

    delta_timedelta = timedelta(
        days=delta.days,
        hours=delta.hours,
        minutes=delta.minutes,
    )
    return format_timedelta(delta_timedelta, locale=locale)

if __name__ == "__main__":
    print(human_delta(datetime.now(), datetime.now() + timedelta(hours=2, minutes=1)))