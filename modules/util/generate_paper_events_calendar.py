import os
from PIL import Image, ImageDraw, ImageFont
from datetime import date, datetime, timedelta
import calendar
from collections import defaultdict
from modules import env

# Layout constants
MARGIN = 50
DAY_LABEL_WIDTH = 40
NARROW_COLUMN_WIDTH = 60  # Width for months without events
WIDE_COLUMN_WIDTH = 180  # Width for months with events
BASE_ROW_HEIGHT = 25  # Base height for each day row
EVENT_ROW_HEIGHT = 18  # Additional height per event
HEADER_HEIGHT = 35
FONT_SIZE = 14
TITLE_FONT_SIZE = 10
FONT_PATH = "assets/beleren.ttf"

FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)
TITLE_FONT = ImageFont.truetype(FONT_PATH, TITLE_FONT_SIZE)

# Colors
BG_COLOR = "white"
TEXT_COLOR = "black"
WEEKEND_COLOR = "#ffcccc"
TODAY_COLOR = "#003366"
EVENT_COLOR = "#ffcc66"
GRID_COLOR = "#cccccc"

def get_german_month_name(month: int) -> str:
    """Get the German name for a given month."""
    months = {
        1: "Januar", 2: "Februar", 3: "März", 4: "April",
        5: "Mai", 6: "Juni", 7: "Juli", 8: "August",
        9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
    }
    return months.get(month, "Unbekannt")

def get_german_weekday_abbr(weekday: int) -> str:
    """Get the abbreviated German weekday name."""
    weekdays = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}
    return weekdays.get(weekday, "")

def get_months_between(start_date: date, end_date: date) -> list[date]:
    """Get all months between two dates."""
    months = []
    current_date = start_date.replace(day=1)
    while current_date <= end_date:
        months.append(current_date)
        next_month = current_date.month % 12 + 1
        next_year = current_date.year + (current_date.month // 12)
        current_date = current_date.replace(year=next_year, month=next_month)
    return months

def generate_paper_events_calendar(events: list = []):
    """
    Generate a calendar image for paper events with months as columns and days as rows.
    Events should have: title, time (datetime), calc_end() method
    """
    if not events:
        return None
    
    events = events or []
    events.sort(key=lambda e: e.time)
    
    today = datetime.now(tz=env.TIMEZONE).date()
    earliest_date = min(events[0].time.date(), today)
    latest_date = max(e.calc_end().date() for e in events)
    
    # Get all months to display
    months = get_months_between(earliest_date, latest_date)
    
    # Group events by date
    events_by_date = defaultdict(list)
    for event in events:
        event_date = event.time.date()
        # For multi-day events, we only show them on the start date
        events_by_date[event_date].append(event)
    
    # Determine which months have events
    months_with_events = set()
    for event_date in events_by_date.keys():
        months_with_events.add((event_date.year, event_date.month))
    
    # Calculate column widths for each month
    column_widths = []
    for month in months:
        if (month.year, month.month) in months_with_events:
            column_widths.append(WIDE_COLUMN_WIDTH)
        else:
            column_widths.append(NARROW_COLUMN_WIDTH)
    
    # Calculate row heights based on max events per day
    row_heights = []
    for day in range(1, 32):
        max_events = 0
        for month in months:
            days_in_month = calendar.monthrange(month.year, month.month)[1]
            if day <= days_in_month:
                current_date = date(month.year, month.month, day)
                event_count = len(events_by_date.get(current_date, []))
                max_events = max(max_events, event_count)
        
        # Row height = base height + (event count * event row height)
        if max_events > 0:
            row_heights.append(BASE_ROW_HEIGHT + (max_events * EVENT_ROW_HEIGHT))
        else:
            row_heights.append(BASE_ROW_HEIGHT)
    
    # Calculate dimensions
    total_width = MARGIN * 2 + DAY_LABEL_WIDTH + sum(column_widths)
    total_height = MARGIN * 2 + HEADER_HEIGHT + sum(row_heights)
    
    # Create image
    img = Image.new("RGB", (total_width, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Draw header (month names)
    x_offset = MARGIN + DAY_LABEL_WIDTH
    for month_idx, month in enumerate(months):
        x = x_offset
        y = MARGIN
        col_width = column_widths[month_idx]
        
        month_name = get_german_month_name(month.month)
        year_str = str(month.year) if month_idx == 0 or month.month == 1 else ""
        
        # Center the text in the column
        bbox = draw.textbbox((0, 0), month_name, font=FONT)
        text_width = bbox[2] - bbox[0]
        text_x = x + (col_width - text_width) // 2
        draw.text((text_x, y + 5), month_name, fill=TEXT_COLOR, font=FONT)
        
        if year_str:
            bbox = draw.textbbox((0, 0), year_str, font=TITLE_FONT)
            text_width = bbox[2] - bbox[0]
            text_x = x + (col_width - text_width) // 2
            draw.text((text_x, y + 20), year_str, fill=TEXT_COLOR, font=TITLE_FONT)
        
        x_offset += col_width
    
    # Draw horizontal line below header
    y = MARGIN + HEADER_HEIGHT
    draw.line([(MARGIN, y), (total_width - MARGIN, y)], fill=TEXT_COLOR, width=2)
    
    # Draw each day (row)
    y_offset = MARGIN + HEADER_HEIGHT
    for day in range(1, 32):
        y = y_offset
        row_height = row_heights[day - 1]
        
        # Draw day label
        draw.text((MARGIN + 5, y + 5), f"{day:2}", fill=TEXT_COLOR, font=FONT)
        
        # Draw vertical line after day label
        x = MARGIN + DAY_LABEL_WIDTH
        draw.line([(x, y), (x, y + row_height)], fill=GRID_COLOR, width=1)
        
        # Draw each month column for this day
        x_offset_col = MARGIN + DAY_LABEL_WIDTH
        for month_idx, month in enumerate(months):
            x = x_offset_col
            col_width = column_widths[month_idx]
            
            # Check if this day exists in this month
            days_in_month = calendar.monthrange(month.year, month.month)[1]
            if day > days_in_month:
                # Gray out invalid days
                draw.rectangle(
                    [x, y, x + col_width, y + row_height],
                    fill="#e0e0e0"
                )
                x_offset_col += col_width
                # Draw vertical grid line
                draw.line([(x + col_width, y), (x + col_width, y + row_height)], fill=GRID_COLOR, width=1)
                continue
            
            current_date = date(month.year, month.month, day)
            weekday = current_date.weekday()
            
            # Color weekends
            cell_color = None
            if weekday >= 5:
                cell_color = WEEKEND_COLOR
            
            # Highlight today
            if current_date == today:
                cell_color = TODAY_COLOR
            
            if cell_color:
                draw.rectangle(
                    [x, y, x + col_width, y + row_height],
                    fill=cell_color
                )
            
            # Draw events for this date
            if current_date in events_by_date:
                # Highlight cell with event color
                draw.rectangle(
                    [x + 2, y + 2, x + col_width - 2, y + row_height - 2],
                    fill=EVENT_COLOR
                )
                
                # Draw weekday abbreviation at top
                weekday_abbr = get_german_weekday_abbr(weekday)
                draw.text((x + 3, y + 3), weekday_abbr, fill=TEXT_COLOR, font=TITLE_FONT)
                
                # Draw event titles stacked vertically
                event_list = events_by_date[current_date]
                event_y = y + 16  # Start below weekday abbreviation
                for event in event_list:
                    title = event.title
                    # Truncate title to fit
                    max_chars = int(col_width / 8)  # Approximate chars based on column width
                    if len(title) > max_chars:
                        title = title[:max_chars-1] + "…"
                    draw.text((x + 5, event_y), title, fill=TEXT_COLOR, font=TITLE_FONT)
                    event_y += EVENT_ROW_HEIGHT
            else:
                # Draw weekday abbreviation even if no events
                weekday_abbr = get_german_weekday_abbr(weekday)
                text_color = "white" if current_date == today else TEXT_COLOR
                draw.text((x + 3, y + 3), weekday_abbr, fill=text_color, font=TITLE_FONT)
            
            x_offset_col += col_width
            # Draw vertical grid line
            draw.line([(x + col_width, y), (x + col_width, y + row_height)], fill=GRID_COLOR, width=1)
        
        # Draw horizontal grid line
        draw.line([(MARGIN + DAY_LABEL_WIDTH, y + row_height), (total_width - MARGIN, y + row_height)], fill=GRID_COLOR, width=1)
        
        y_offset += row_height
    
    # Save the image
    os.makedirs("tmp", exist_ok=True)
    file_path = "tmp/paper_events_calendar.png"
    img.save(file_path)
    
    return file_path


if __name__ == "__main__":
    # Test with dummy events
    class DummyEvent:
        def __init__(self, title, time_str):
            self.title = title
            self.time = datetime.fromisoformat(time_str).replace(tzinfo=env.TIMEZONE)
        
        def calc_end(self):
            return self.time
    
    events = [
        DummyEvent("PreRelease", "2026-03-15T18:00:00"),
        DummyEvent("FNM Modern", "2026-03-21T19:00:00"),
        DummyEvent("Commander Night", "2026-03-28T18:30:00"),
        DummyEvent("Draft", "2026-04-04T19:00:00"),
        DummyEvent("RCQ", "2026-04-12T10:00:00"),
    ]
    
    result = generate_paper_events_calendar(events)
    print(f"Calendar saved at: {result}")
