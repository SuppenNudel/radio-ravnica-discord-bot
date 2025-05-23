from PIL import Image, ImageDraw
from datetime import date, timedelta

# Image and layout config
output_path = "tmp/highlighted_calendar.png"
# image_width, image_height = 1086, 768

# Calendar layout config (manually calibrated from the image)
months_per_row = 6
cell_width = 166 // 6  # ~27 px per day cell
cell_height = 25       # ~25 px per row

start_x = 65
start_y = 130
month_width = 166
month_height = 460

def highlight_date_range(start_date, end_date, image_path, color=(255, 0, 0, 100)):
    img = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size)
    draw = ImageDraw.Draw(overlay)

    current_date = start_date
    while current_date <= end_date:
        month = current_date.month
        day = current_date.day
        dow = current_date.weekday()  # Monday = 0, Sunday = 6

        # Calculate month position
        month_col = (month - 1) % months_per_row
        month_row = (month - 1) // months_per_row

        base_x = start_x + month_col * month_width
        base_y = start_y + month_row * month_height

        # Find week row within the month
        first_of_month = date(current_date.year, month, 1)
        week_row = (current_date.day + first_of_month.weekday() - 1) // 7

        x = base_x + dow * cell_width
        y = base_y + week_row * cell_height

        # Draw highlight
        draw.rectangle([x, y, x + cell_width, y + cell_height], fill=color)

        current_date += timedelta(days=1)

    # Composite the highlight over the original image
    combined = Image.alpha_composite(img, overlay)
    combined.convert("RGB").save(output_path)
    print(f"Saved to {output_path}")

if __name__ == "__main__":
    import pdf_to_image
    image_path = pdf_to_image.calendar_image(2025)
    # Example: Highlight from June 10 to July 14, 2025
    highlight_date_range(date(2025, 6, 10), date(2025, 7, 14), image_path)
