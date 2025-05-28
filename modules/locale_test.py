import locale
from datetime import datetime

try:
    # Set the locale to German
    locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")  # Use "de_DE.UTF-8" for Linux/Mac or "de_DE" for Windows

    # Example: Get the German names for months and weekdays
    now = datetime.now()

    # Get the full month name
    month_name = now.strftime("%B")  # e.g., "März" for March
    print(f"Month: {month_name}")

    # Get the abbreviated month name
    month_abbr = now.strftime("%b")  # e.g., "Mär" for March
    print(f"Abbreviated Month: {month_abbr}")

    # Get the full weekday name
    weekday_name = now.strftime("%A")  # e.g., "Montag" for Monday
    print(f"Weekday: {weekday_name}")

    # Get the abbreviated weekday name
    weekday_abbr = now.strftime("%a")  # e.g., "Mo" for Monday
    print(f"Abbreviated Weekday: {weekday_abbr}")
except Exception as e:
    print(e)
    e.with_traceback()