import os
from PIL import Image, ImageDraw, ImageFont
from datetime import date, datetime, timedelta
import calendar
from collections import defaultdict

MARGIN = 50
ROW_HEIGHT = 20
HEADER_HEIGHT = 30
FONT_SIZE = 18
COLUMN_WIDTH = 42  # Width of each day column
DAYS_IN_MONTH = 31  # Maximum number of days in a month
FONT_PATH = "assets/beleren.ttf"

FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)

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

def calculate_required_rows(tournaments:list["SpelltableTournament"], year, month):
    days_in_month = calendar.monthrange(year, month)[1]
    day_occupancy = [0] * days_in_month  # Track how many tournaments occupy each day

    for tournament in tournaments:
        start_date = tournament.time.date()
        end_date = tournament.calc_end().date()

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

def get_months_between(start_date:date, end_date:date) -> list[date]:
    months:list[date] = []
    current_date = start_date.replace(day=1)  # Start at the first day of the start month
    while current_date <= end_date:
        months.append(current_date)
        # Move to the next month
        next_month = current_date.month % 12 + 1
        next_year = current_date.year + (current_date.month // 12)
        current_date = current_date.replace(year=next_year, month=next_month)
    return months

def rotated_text(text, draw:ImageDraw.ImageDraw, img:ImageDraw.Image.Image, x, y):
    bbox = draw.textbbox((0, 0), text, font=FONT)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    # Create a transparent image for the text
    text_img = Image.new("RGBA", (text_width, text_height+20), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_img)
    text_draw.text((0, 0), text, fill="black", font=FONT)

    # Rotate the text image 90 degrees
    rotated_text = text_img.rotate(90, expand=True)

    # Paste it onto the base image at desired position
    img.paste(rotated_text, (x, y), rotated_text)  # Third argument is mask for transparency

def generate_calendar(tournaments: list["SpelltableTournament"] = []):
    tournaments = tournaments or []
    tournaments.sort(key=lambda t: t.time)  # Sort tournaments by start time

    earliest_date:date = tournaments[0].time.date()
    last_date:date = tournaments[-1].calc_end().date()

    months:list[date] = []
    print(f"from {earliest_date.month}/{earliest_date.year} to {last_date.month}/{last_date.year}")
    current_date = earliest_date.replace(day=1)  # Start at the first day of the earliest month
    while current_date <= last_date:
        months.append(current_date)
        next_month = current_date.month % 12 + 1
        next_year = current_date.year + (current_date.month // 12)
        current_date = current_date.replace(year=next_year, month=next_month)

    img = Image.new("RGB", (1, 1), BG_COLOR)
    draw = ImageDraw.Draw(img)
    weekday_font = ImageFont.truetype(FONT_PATH, FONT_SIZE - 4)
    wd_font_text_size = draw.textbbox((0, 0), "Mo", font=weekday_font)

    max_width_months:int = 0
    for month in months:
        month_name = month.strftime("%B")
        bbox = draw.textbbox((0, 0), month_name, font=FONT)
        width:int = int(bbox[2])
        if width > max_width_months:
            max_width_months = width

    max_width_months += 10 # Add some padding for the month name

    months_max_rows:dict[date, int] = defaultdict(int)

    occupancy:dict[date, dict[int, SpelltableTournament]] = {}
    for tournament in tournaments:
        start = tournament.time.date()
        end = tournament.calc_end().date()

        # check if start is occupied by any rows
        row = 1
        if start in occupancy:
            while row in occupancy[start]:
                row += 1
        tournament.row = row

        months_between = get_months_between(start, end)
        for month in months_between:
            if not month in months_max_rows:
                months_max_rows[month] = row
            
            if row > months_max_rows[month]:
                months_max_rows[month] = row

        current_date:date = start
        while current_date <= end:
            if not current_date in occupancy:
                occupancy[current_date] = {}
            occupancy[current_date][row] = tournament
            current_date += timedelta(days=1)

    tournament_rows = sum(months_max_rows.values())
    
    # Calculate image dimensions
    total_width:int = (MARGIN * 2 + COLUMN_WIDTH * DAYS_IN_MONTH) + max_width_months
    total_height = MARGIN * 2 + HEADER_HEIGHT + ROW_HEIGHT * (tournament_rows + len(months))

    # Resize the image to fit the calendar
    img = img.resize((total_width, total_height))
    draw = ImageDraw.Draw(img)

    # Draw the header row (days of the month)
    for day in range(1, DAYS_IN_MONTH + 1):
        x_offset = MARGIN + max_width_months + (day - 1) * COLUMN_WIDTH
        draw.text((x_offset + 5, MARGIN-10), f"{day:2}", fill=TEXT_COLOR, font=FONT)

    previous_offset = 0

    # Draw each month row
    for month_idx, month in enumerate(months, start=1):
        row_height = ROW_HEIGHT * (months_max_rows[month.replace(day=1)])
        y_offset = MARGIN + HEADER_HEIGHT + (month_idx - 1) * ROW_HEIGHT - 10 + previous_offset
        previous_offset += row_height

        # Draw the month name
        month_name = month.strftime("%B")
        draw.text((MARGIN, y_offset + row_height / 2 + 2), month_name, fill=TEXT_COLOR, font=FONT)
        
        if month_idx == 1 or month.month == 1:
            rotated_text(str(month.year), draw, img, 20, y_offset + ROW_HEIGHT)

        # Track occupied rows for each day in the month
        days_in_month = calendar.monthrange(month.year, month.month)[1]

        # Draw each day in the month
        for day in range(1, DAYS_IN_MONTH + 1):
            x_offset = MARGIN + max_width_months + (day - 1) * COLUMN_WIDTH

            # Check if the day exists in the current month
            if day > days_in_month:
                draw.rectangle(
                    [x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + row_height],
                    fill="#d3d3d3",  # Light gray color
                )
                continue

            current_day = date(month.year, month.month, day)

            # Highlight weekends
            if current_day.weekday() >= 5:  # Saturday or Sunday
                draw.rectangle(
                    [x_offset, y_offset, x_offset + COLUMN_WIDTH, y_offset + ROW_HEIGHT],
                    fill=WEEKEND_COLOR,
                )

            # Add the first two letters of the weekday in the top-left corner of the cell
            weekday_abbr = get_weekday_abbr(current_day)
            draw.text(
                (x_offset + 2, y_offset + 2),
                f"{current_day.day} {weekday_abbr}",
                fill=TEXT_COLOR,
                font=weekday_font,
            )

            # Check if the current day is part of any tournament
            for tournament in tournaments:
                start_date = tournament.time.date()
                end_date = tournament.calc_end().date()

                if start_date <= current_day <= end_date:
                    # Calculate the y-offset for the tournament
                    title_y_offset = y_offset + ROW_HEIGHT * tournament.row

                    # Highlight the tournament range
                    highlight_color = (
                        DARKER_HIGHLIGHT_COLOR if current_day.weekday() >= 5 else HIGHLIGHT_COLOR
                    )
                    draw.rectangle(
                        [
                            x_offset,
                            title_y_offset,
                            x_offset + COLUMN_WIDTH,
                            title_y_offset + ROW_HEIGHT,
                        ],
                        fill=highlight_color,
                    )
                    draw_dashed_line(draw, (x_offset, title_y_offset), (x_offset + COLUMN_WIDTH, title_y_offset))
                    title_x_offset = x_offset + 5  # Add padding inside the cell

                    # Add the event title on the first day of the event
                    if current_day == start_date:
                        # Ensure the title is drawn after the highlight
                        tournament.title_x_offset = title_x_offset  # Add padding inside the cell
                        tournament.title_y_offset = title_y_offset  # Add padding from the top

    # Draw grid lines
    for day in range(DAYS_IN_MONTH + 1):  # Vertical lines
        x = MARGIN + max_width_months + day * COLUMN_WIDTH
        draw.line([(x, MARGIN-10), (x, total_height - MARGIN-10)], fill=TEXT_COLOR, width=1)

    y = MARGIN + HEADER_HEIGHT - 10
    draw.line([(MARGIN-40, y), (total_width - MARGIN, y)], fill=TEXT_COLOR, width=1)
    previous_offset = 0
    for month_idx, month in enumerate(months):  # Horizontal lines
        new_year_offset = 0
        if month.month == 12:
            new_year_offset = -40
        row_height = ROW_HEIGHT * (months_max_rows[month.replace(day=1)])
        y = MARGIN + HEADER_HEIGHT + ROW_HEIGHT * (month_idx+1) + row_height + previous_offset - 10
        previous_offset += row_height
        draw.line([(MARGIN+new_year_offset, y), (total_width - MARGIN, y)], fill=TEXT_COLOR, width=1)

    # draw tournament stuff
    outline_color = "white"
    glow_offset_size = 1
    y_text_offset = 4
    glow_offset = [(-glow_offset_size, -glow_offset_size), (-glow_offset_size, glow_offset_size), (glow_offset_size, -glow_offset_size), (glow_offset_size, glow_offset_size)]  # Offsets for the outline
    for tournament in tournaments:
        title = tournament.title
        title_x_offset = tournament.title_x_offset
        title_y_offset = tournament.title_y_offset

        wd_font_text_size = draw.textbbox((0, 0), title, font=weekday_font)
        text_length = wd_font_text_size[2] - wd_font_text_size[0]
        
        # override grid behind text
        draw.rectangle(
            [
                (title_x_offset - 5 + 1, title_y_offset),
                (title_x_offset + text_length, title_y_offset+ROW_HEIGHT-1),
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
        def __init__(self, title, time):
            self.title = title
            self.description = None
            self.time:datetime = time
            self.days_per_match = 7

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
        SpelltableTournament("Turnier 1 mit einem ganz langen Titel", date(2025, 3, 20)),
        SpelltableTournament("Nach Turnier 1", date(2025, 4, 26)),
        SpelltableTournament("Noch ein Turnier - Pauper", date(2025, 4, 5)),
        SpelltableTournament("Drittes Turnier", date(2025, 4, 20)),
        SpelltableTournament("Turnier nächstes Jahr", date(2026, 2, 17)),
    ]

    calendar_new = generate_calendar(tournaments)
    print(f"Calendar saved at: {calendar_new}")
