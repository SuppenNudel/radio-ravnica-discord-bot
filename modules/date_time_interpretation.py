import dateparser
from datetime import datetime, timedelta
from modules.util import google_ai
import os
from dateutil.relativedelta import relativedelta
from babel.dates import format_timedelta
import pytz
from ezcord import log

settings = {
    'RETURN_AS_TIMEZONE_AWARE': True,
    'PREFER_DAY_OF_MONTH': 'first',
    'PREFER_DATES_FROM': 'future',
    'TIMEZONE': 'Europe/Berlin'
    # 'RELATIVE_BASE': None,
}

timezone = pytz.timezone("Europe/Berlin")

def parse_date(user_time_input) -> datetime | None:
    parsed_date = dateparser.parse(user_time_input, settings=settings, languages=["de"])
    if parsed_date:
        log.debug(f"Parsed by dateparser: '{user_time_input}' -> {parsed_date}")
        return parsed_date

    now = datetime.now(tz=timezone)

    # Generate text
    prompt = f"Jetzt ist {now}. Welches Datum und Uhrzeit ist {user_time_input}? Prüfe das Ergebnis nochmal nach! Gib mir nur das Datum mit Uhrzeit."
    log.debug(f"Gemini Prompt: {prompt}")
    response = google_ai.prompt(prompt)
    log.debug(f"Gemini Response: {response}")
    parsed_date = dateparser.parse(response, settings=settings)
    log.debug(f"Parsed by dateparser after gemini: '{response}' -> {parsed_date}")

    log.debug(f"using {google_ai.MODEL} {user_time_input} -> {parsed_date}")

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