import dateparser
from datetime import datetime, timedelta
import google.generativeai as genai
import os
from dateutil.relativedelta import relativedelta
from babel.dates import format_timedelta

# Set up API key
genai.configure(api_key=os.getenv("GEMINI_KEY"), transport="rest")
# Initialize the model
model = genai.GenerativeModel("gemini-1.5-flash")

settings = {
    'RETURN_AS_TIMEZONE_AWARE': True,
    'PREFER_DAY_OF_MONTH': 'first',
    'PREFER_DATES_FROM': 'future',
    # 'RELATIVE_BASE': None,
}

def parse_date(user_time_input) -> datetime | None:
    parsed_date = dateparser.parse(user_time_input, settings=settings, languages=["de"])
    if parsed_date:
        return parsed_date

    now = datetime.now()

    # Generate text
    response = model.generate_content(f"Jetzt ist {now}. Welches Datum und Uhrzeit ist {user_time_input}? Prüfe das Ergebnis nochmal nach! Gib mir nur das Datum mit Uhrzeit.")
    response_date = response.text.strip()
    parsed_date = dateparser.parse(response_date, settings=settings , languages=["de"])

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