import ics
from datetime import datetime

def create_ics_file(file_name, event_name, start_datetime, end_datetime:datetime, description=None, location=None):
    """
    Creates a fully compliant .ics file with DTSTAMP and consistent CRLF line endings.
    """
    # Create a new calendar and event
    calendar = ics.Calendar()
    event = ics.Event()

    # Set required event properties
    event.name = event_name
    event.begin = start_datetime
    event.end = end_datetime
    if end_datetime and end_datetime == start_datetime:
        event.end = end_datetime.replace(hour=end_datetime.hour + 1)
    if start_datetime and not end_datetime:
        event.end = start_datetime.replace(hour=start_datetime.hour + 1)

    # Add DTSTAMP (current UTC time)
    event.created = datetime.now()

    # Optional properties
    if description:
        event.description = description
    if location:
        event.location = location

    # Add the event to the calendar
    calendar.events.add(event)

    # Serialize the calendar
    ics_content = calendar.serialize()

    # Save to file
    with open(file_name, "wb") as file:
        file.write(ics_content.encode("utf-8"))
    return file_name