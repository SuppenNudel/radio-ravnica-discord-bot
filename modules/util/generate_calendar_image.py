from PIL import Image, ImageDraw, ImageFont
from datetime import date, timedelta
import locale

# Set locale to German for month and weekday names
locale.setlocale(locale.LC_TIME, "de_DE")

# Constants
LANDSCAPE_WIDTH = 1600
LANDSCAPE_HEIGHT = 1100
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

# Generate vertical calendar with grids
def generate_vertical_calendar_landscape_with_grids(year):
    img = Image.new("RGB", (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

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
        while current_day.month == month:
            is_weekend = current_day.weekday() >= 5
            day_text = f"{current_day.day:2} {get_weekday_abbr(current_day)}"
            if is_weekend:
                draw.rectangle([x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + ROW_HEIGHT], fill=WEEKEND_COLOR)
            draw.text((x_offset + 5, y_offset + 5), day_text, fill=TEXT_COLOR, font=font)
            y_offset += ROW_HEIGHT
            current_day += timedelta(days=1)

        # Draw vertical grid line for the current month
        draw.line([(x_offset, MARGIN), (x_offset, LANDSCAPE_HEIGHT - MARGIN)], fill=TEXT_COLOR, width=1)

    # Draw horizontal grid lines for each row
    for row in range(0, LANDSCAPE_HEIGHT - MARGIN, ROW_HEIGHT):
        y = MARGIN + HEADER_HEIGHT + row
        draw.line([(MARGIN, y), (LANDSCAPE_WIDTH - MARGIN, y)], fill=TEXT_COLOR, width=1)

    # Draw the outer border
    draw.rectangle([MARGIN, MARGIN, LANDSCAPE_WIDTH - MARGIN, LANDSCAPE_HEIGHT - MARGIN], outline=TEXT_COLOR, width=2)

    return img

# Generate German landscape calendar with grids for 2025
calendar_with_grids = generate_vertical_calendar_landscape_with_grids(2025)
calendar_with_grids.show()
