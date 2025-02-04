import dateparser
from datetime import datetime
import google.generativeai as genai
import os

# Set up API key
genai.configure(api_key=os.getenv("GEMINI_KEY"), transport="rest")
# Initialize the model
model = genai.GenerativeModel("gemini-1.5-flash")

def parse_date(user_time_input) -> datetime | None:
    parsed_date = dateparser.parse(user_time_input, settings={'RETURN_AS_TIMEZONE_AWARE': True})
    if parsed_date:
        return parsed_date

    now = datetime.now()

    # Generate text
    response = model.generate_content(f"Jetzt ist {now}. Welches Datum und Uhrzeit ist {user_time_input}? Prüfe das Ergebnis nochmal nach! Gib mir nur das Datum mit Uhrzeit.")
    response_date = response.text.strip()
    parsed_date = dateparser.parse(response_date, settings={'RETURN_AS_TIMEZONE_AWARE': True})

    return parsed_date
