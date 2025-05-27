import os
from PIL import Image, ImageDraw, ImageFont
from datetime import date, datetime, timedelta
import locale
import calendar

import pytz

# Set locale to German for month and weekday names
locale.setlocale(locale.LC_TIME, "de_DE")
timezone = pytz.timezone("Europe/Berlin")

MARGIN = 50
ROW_ROW_HIGHT = 20
ROW_HEIGHT = ROW_ROW_HIGHT*4
HEADER_HEIGHT = 30
FONT_SIZE = 18
COLUMN_WIDTH = 27  # Width of each day column
DAYS_IN_MONTH = 31  # Maximum number of days in a month
FONT_PATH = "assets/beleren.ttf"

# Colors
BG_COLOR = "white"
TEXT_COLOR = "black"
WEEKEND_COLOR = "#ffcccc"
HEADER_COLOR = "#d0e0ff"
HIGHLIGHT_COLOR = "#ffcc66"
DARKER_HIGHLIGHT_COLOR = "#e6b800"

# Get abbreviated weekday name
def get_weekday_abbr(d):
    return d.strftime('%a')[:2]

def draw_dashed_line(draw, start, end, dash_length=4, gap_length=5, fill="black", width=1):
    """
    Draw a dashed line on the image.

    :param draw: The ImageDraw object.
    :param start: Tuple (x1, y1) for the start of the line.
    :param end: Tuple (x2, y2) for the end of the line.
    :param dash_length: Length of each dash.
    :param gap_length: Length of the gap between dashes.
    :param fill: Color of the line.
    :param width: Width of the line.
    """
    x1, y1 = start
    x2, y2 = end

    # Calculate the total length of the line
    total_length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    # Calculate the direction vector for the line
    dx = (x2 - x1) / total_length
    dy = (y2 - y1) / total_length

    # Draw dashes along the line
    current_length = 0
    while current_length < total_length:
        # Start of the dash
        dash_start = (
            x1 + dx * current_length,
            y1 + dy * current_length,
        )
        # End of the dash
        dash_end = (
            x1 + dx * min(current_length + dash_length, total_length),
            y1 + dy * min(current_length + dash_length, total_length),
        )
        # Draw the dash
        draw.line([dash_start, dash_end], fill=fill, width=width)
        # Move to the next dash position
        current_length += dash_length + gap_length

def calculate_required_rows(tournaments, year, month):
    """
    Calculate the number of rows needed for overlapping tournaments in a given month.
    :param tournaments: List of tournaments.
    :param year: The year of the calendar.
    :param month: The month of the calendar.
    :return: The number of rows needed.
    """
    days_in_month = calendar.monthrange(year, month)[1]
    day_occupancy = [0] * days_in_month  # Track how many tournaments occupy each day

    for tournament in tournaments:
        start_date = tournament.time
        end_date = tournament.calc_end()

        # Check if the tournament overlaps with the current month
        if start_date.year == year and start_date.month == month:
            start_day = max(1, start_date.day)
        else:
            start_day = 1

        if end_date.year == year and end_date.month == month:
            end_day = min(days_in_month, end_date.day)
        else:
            end_day = days_in_month

        # Increment occupancy for each day the tournament spans
        for day in range(start_day, end_day + 1):
            day_occupancy[day - 1] += 1

    # The number of rows needed is the maximum occupancy for any day
    return max(day_occupancy, default=0) + 1  # Add 1 for the weekday row

def generate_calendar(tournaments: list["SpelltableTournament"] = []):
    """
    Generate a horizontal calendar where each row represents a month and each column represents a day.
    """
    tournaments = tournaments or []
    img = Image.new("RGB", (1000, 1000), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    weekday_font = ImageFont.truetype(FONT_PATH, FONT_SIZE - 4)
    wd_font_text_size = draw.textbbox((0, 0), "Mo", font=weekday_font)

    max_width_months = 0
    for month in range(1, 13):
        month_name = date(2025, month, 1).strftime("%B")
        bbox = draw.textbbox((0, 0), month_name, font=font)
        width = bbox[2]
        if width > max_width_months:
            max_width_months = width

    max_width_months += 10 # Add some padding for the month name

    # Calculate image dimensions
    total_width = (MARGIN * 2 + COLUMN_WIDTH * DAYS_IN_MONTH) + max_width_months
    total_height = MARGIN * 2 + HEADER_HEIGHT + ROW_HEIGHT * 12

    # # Calculate the required rows for each month
    # row_heights = []
    # for month in range(1, 13):
    #     required_rows = calculate_required_rows(tournaments, 2025, month)
    #     row_heights.append(required_rows)
    #     total_height += ROW_ROW_HIGHT * required_rows

    # Resize the image to fit the calendar
    img = img.resize((total_width, total_height))
    draw = ImageDraw.Draw(img)

    # Draw the header row (days of the month)
    for day in range(1, DAYS_IN_MONTH + 1):
        x_offset = MARGIN + max_width_months + (day - 1) * COLUMN_WIDTH
        draw.text((x_offset + 5, MARGIN), f"{day:2}", fill=TEXT_COLOR, font=font)

    # Draw each month row
    for month in range(1, 13):
        y_offset = MARGIN + HEADER_HEIGHT + (month - 1) * ROW_HEIGHT -10

        # Draw the month name
        month_name = date(2025, month, 1).strftime("%B")
        draw.text((MARGIN, y_offset + ROW_HEIGHT/2-5), month_name, fill=TEXT_COLOR, font=font)

        # Draw each day in the month
        for day in range(1, DAYS_IN_MONTH + 1):
            x_offset = MARGIN + max_width_months + (day - 1) * COLUMN_WIDTH
            # Check if the day exists in the current month
            if day > calendar.monthrange(2025, month)[1]:  # Get the number of days in the month
                draw.rectangle(
                    [x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + ROW_HEIGHT],
                    fill="#d3d3d3",  # Light gray color
                )
                continue
            
            current_day = datetime(2025, month, day, tzinfo=timezone)

            # Highlight weekends
            if current_day.weekday() >= 5:  # Saturday or Sunday
                draw.rectangle(
                    [x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + ROW_ROW_HIGHT * 1],
                    fill=WEEKEND_COLOR,
                )
            
            # Add the first two letters of the weekday in the top-left corner of the cell
            weekday_abbr = current_day.strftime("%a")[:2]  # Get the first two letters of the weekday
            draw.text(
                (x_offset + 2, y_offset + 2),  # Add a small padding from the top-left corner
                weekday_abbr,
                fill=TEXT_COLOR,
                font=weekday_font,
            )

            # Check if the current day is part of any tournament
            for tournament in tournaments:
                row = tournament.row if hasattr(tournament, 'row') else 1
                start_date = tournament.time
                end_date = tournament.calc_end()
                title = tournament.title

                if start_date.date() <= current_day.date() <= end_date.date():
                    highlight_color = (
                        DARKER_HIGHLIGHT_COLOR if current_day.weekday() >= 5 else HIGHLIGHT_COLOR
                    )

                    # Highlight the tournament range
                    draw.rectangle(
                        [x_offset, y_offset+ROW_ROW_HIGHT*(row), x_offset + COLUMN_WIDTH, y_offset + +ROW_ROW_HIGHT*(row+1)],
                        fill=highlight_color,
                    )
                    draw_dashed_line(draw, (x_offset, y_offset+ROW_ROW_HIGHT*(row)), (x_offset + COLUMN_WIDTH, y_offset+ROW_ROW_HIGHT*(row)))

                    # Add the event title on the first day of the event
                    if current_day.date() == start_date.date():
                        # Ensure the title is drawn after the highlight
                        tournament.title_x_offset = x_offset + 5  # Add padding inside the cell
                        tournament.title_y_offset = y_offset+ROW_ROW_HIGHT*(row)  # Add padding from the top

    # Draw grid lines
    for day in range(DAYS_IN_MONTH + 1):  # Vertical lines
        x = MARGIN + max_width_months + day * COLUMN_WIDTH
        draw.line([(x, MARGIN), (x, total_height - MARGIN-10)], fill=TEXT_COLOR, width=1)

    for month in range(13):  # Horizontal lines
        y = MARGIN + HEADER_HEIGHT + month * ROW_HEIGHT -10
        draw.line([(MARGIN, y), (total_width - MARGIN, y)], fill=TEXT_COLOR, width=1)

    outline_color = "white"
    glow_offset_size = 1
    y_text_offset = 4
    glow_offset = [(-glow_offset_size, -glow_offset_size), (-glow_offset_size, glow_offset_size), (glow_offset_size, -glow_offset_size), (glow_offset_size, glow_offset_size)]  # Offsets for the outline
    for tournament in tournaments:
        title = tournament.title
        title_x_offset = tournament.title_x_offset #if hasattr(tournament, 'title_x_offset') else 100
        title_y_offset = tournament.title_y_offset #if hasattr(tournament, 'title_y_offset') else 100

        wd_font_text_size = draw.textbbox((0, 0), title, font=weekday_font)
        text_length = wd_font_text_size[2] - wd_font_text_size[0]
        
        # override grid behind text
        draw.rectangle(
            [
                (title_x_offset - 5 + 1, title_y_offset),
                (title_x_offset + text_length, title_y_offset+ROW_ROW_HIGHT-1),
            ],
            fill=HIGHLIGHT_COLOR,
        )

        for x_offset, y_offset in glow_offset:
            draw.text(
                (title_x_offset + x_offset, title_y_offset + y_offset + y_text_offset),
                title,
                fill=outline_color,
                font=weekday_font,
            )


        draw.text(
            (title_x_offset, title_y_offset+y_text_offset),
            title,
            fill=TEXT_COLOR,
            font=weekday_font,
        )

        draw_dashed_line(draw, (title_x_offset-5, title_y_offset), (title_x_offset+text_length, title_y_offset))

    # Save the image to the tmp/ directory
    os.makedirs("tmp", exist_ok=True)  # Ensure the tmp/ directory exists
    file_path = f"tmp/calendar.png"
    img.save(file_path)

    return file_path

if __name__ == "__main__":
    class SpelltableTournament():
        def __init__(self, title, time, row=1):
            self.title = title
            self.description = None
            self.time:datetime = time
            self.days_per_match = 7
            self.row = row

        def calc_end(self):
            start = self.time
            days_per_match = self.days_per_match
            if days_per_match:
                round_count = 5
                return start + timedelta(days=days_per_match*round_count)
            else:
                return start

    # Create a list of tournaments
    tournaments = [
        SpelltableTournament("Turnier 1 mit einem ganz langen Titel", datetime(2025, 3, 20, tzinfo=timezone)),
        SpelltableTournament("Noch ein Turnier - Pauper", datetime(2025, 4, 5, tzinfo=timezone), row=2),
        SpelltableTournament("Drittes Turnier", datetime(2025, 4, 20, tzinfo=timezone), row=3),
    ]

    calendar_new = generate_calendar(tournaments)
    print(f"Calendar saved at: {calendar_new}")
