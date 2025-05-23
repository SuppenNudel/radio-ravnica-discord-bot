from PIL import Image, ImageDraw, ImageFont
from datetime import date, timedelta
import locale

# Set locale to German for month and weekday names
locale.setlocale(locale.LC_TIME, "de_DE")

# Constants
LANDSCAPE_WIDTH = 1600
# LANDSCAPE_HEIGHT = 1200 doesn't do anything anymore
MARGIN = 50
COLUMN_WIDTH = (LANDSCAPE_WIDTH - 2 * MARGIN) // 12
ROW_HEIGHT = 30
HEADER_HEIGHT = 40
FONT_SIZE = 18
FONT_PATH = "assets/beleren.ttf"

# Colors
BG_COLOR = "white"
TEXT_COLOR = "black"
WEEKEND_COLOR = "#ffcccc"
HEADER_COLOR = "#d0e0ff"

# Get abbreviated weekday name
def get_weekday_abbr(d):
    return d.strftime('%a')[:2]

def calculate_image_height():
    """
    Calculate the required height of the image dynamically based on the number of rows
    and ensure consistent padding around the calendar grid.
    """
    max_rows = 31  # Maximum number of rows for any month (31 days max)
    grid_height = HEADER_HEIGHT + max_rows * ROW_HEIGHT  # Height of the calendar grid
    total_height = 2 * MARGIN + grid_height  # Add padding (MARGIN) to the top and bottom
    return total_height

def generate_vertical_calendar_landscape_with_grids(year, events=None, highlight_style="full"):
    """
    Generate a vertical calendar with grids for the given year.
    Optionally highlight multiple events with date ranges and titles.

    :param year: The year for the calendar.
    :param events: A list of events, where each event is a dictionary with 'start_date', 'end_date', and 'title'.
    :param highlight_style: The style of highlighting ("full" for full-day highlight, "line" for vertical line).
    :return: An Image object of the calendar.
    """
    # Dynamically calculate the required image height
    dynamic_height = calculate_image_height()

    # Create the image with the calculated height
    img = Image.new("RGB", (LANDSCAPE_WIDTH, dynamic_height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

    # Parse the events
    events = events or []

    # Calculate the bottom of the frame
    frame_bottom = MARGIN + HEADER_HEIGHT + 31 * ROW_HEIGHT

    # Draw vertical grid lines for each month
    for month in range(1, 13):
        x_offset = MARGIN + (month - 1) * COLUMN_WIDTH
        y_offset = MARGIN

        # Month name header
        month_name = date(year, month, 1).strftime("%B")
        draw.rectangle([x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + HEADER_HEIGHT], fill=HEADER_COLOR)
        draw.text((x_offset + 5, y_offset + 5), month_name, fill=TEXT_COLOR, font=font)
        y_offset += HEADER_HEIGHT

        # Draw each day
        current_day = date(year, month, 1)
        row_count = 0
        while current_day.month == month:
            is_weekend = current_day.weekday() >= 5
            day_text = f"{current_day.day:2} {get_weekday_abbr(current_day)}"

            # Highlight weekends (draw this first to avoid overwriting event highlights)
            if is_weekend:
                draw.rectangle([x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + ROW_HEIGHT], fill=WEEKEND_COLOR)

            # Check if the current day is part of any event
            for event in events:
                start_date = event["start_date"]
                end_date = event["end_date"]
                title = event["title"]

                if start_date <= current_day <= end_date:
                    if highlight_style == "full":
                        # Highlight the event range
                        color = "#ffcc66" if is_weekend else "#ffff99"  # Darker color for weekends in the range
                        draw.rectangle([x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + ROW_HEIGHT], fill=color)
                    elif highlight_style == "line":
                        # Highlight with a vertical line
                        draw.line(
                            [(x_offset + COLUMN_WIDTH // 2, y_offset), (x_offset + COLUMN_WIDTH // 2, y_offset + ROW_HEIGHT)],
                            fill="#666666",  # Darker color for line mode
                            width=2,
                        )

                    # Add the event title on the first day of the event
                    if current_day == start_date:
                        draw.text((x_offset + 5, y_offset + 15), title, fill=TEXT_COLOR, font=font)

            # Draw the day text
            draw.text((x_offset + 5, y_offset + 5), day_text, fill=TEXT_COLOR, font=font)
            y_offset += ROW_HEIGHT
            current_day += timedelta(days=1)
            row_count += 1

        # Draw vertical grid line for the current month
        draw.line([(x_offset, MARGIN), (x_offset, MARGIN + HEADER_HEIGHT + row_count * ROW_HEIGHT)], fill=TEXT_COLOR, width=1)

    # Draw horizontal grid lines for each row
    for row in range(31 + 1):  # Include one extra line for the bottom border
        y = MARGIN + HEADER_HEIGHT + row * ROW_HEIGHT
        if y > frame_bottom:
            break  # Stop drawing rows if they exceed the calculated frame height
        draw.line([(MARGIN, y), (LANDSCAPE_WIDTH - MARGIN, y)], fill=TEXT_COLOR, width=1)

    # Draw vertical grid lines for the outer border
    for month in range(13):  # Include one extra line for the right border
        x = MARGIN + month * COLUMN_WIDTH
        if x > LANDSCAPE_WIDTH - MARGIN:
            break  # Stop drawing columns if they exceed the available width
        draw.line([(x, MARGIN), (x, frame_bottom)], fill=TEXT_COLOR, width=1)

    # Draw the outer border
    draw.rectangle([MARGIN, MARGIN, LANDSCAPE_WIDTH - MARGIN, frame_bottom], outline=TEXT_COLOR, width=2)

    return img

# Define a list of events
events = [
    {"start_date": date(2025, 3, 17), "end_date": date(2025, 3, 28), "title": "Event 1"},
    {"start_date": date(2025, 4, 5), "end_date": date(2025, 4, 14), "title": "Event 2"},
    {"start_date": date(2025, 10, 24), "end_date": date(2025, 11, 10), "title": "Event 3"},
]

# Generate the calendar with highlighted events
calendar_with_highlights = generate_vertical_calendar_landscape_with_grids(2025, events=events, highlight_style="full")
calendar_with_highlights.show()

calendar_with_highlights = generate_vertical_calendar_landscape_with_grids(2025, events=events, highlight_style="line")
calendar_with_highlights.show()