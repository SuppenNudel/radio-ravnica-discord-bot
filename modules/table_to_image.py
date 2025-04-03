from PIL import Image, ImageDraw, ImageFont

def generate_image(data, filename):
    # Load font (fallback to default if unavailable)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except IOError:
        font = ImageFont.load_default()

    # Calculate dynamic width for Name column
    name_text_widths = [font.getbbox(row[1][0])[2] for row in data["rows"]]
    name_column_width = max(name_text_widths) + 20  # Adding padding

    # Image properties
    cell_widths = [60, name_column_width, 80, 100, 120, 120, 120]
    cell_height = 40
    padding = 10
    num_columns = len(data["headers"])
    num_rows = len(data["rows"]) + 1  # +1 for headers

    # Calculate image size
    img_width = sum(cell_widths) + 2 * padding
    img_height = cell_height * num_rows + 2 * padding

    # Create image
    img = Image.new("RGB", (img_width, img_height), "white")
    draw = ImageDraw.Draw(img)

    # Function to draw a cell
    def draw_cell(x, y, text, width, is_header=False, align="center", strike_through=False):
        rect_x1, rect_y1 = x, y
        rect_x2, rect_y2 = x + width, y + cell_height
        draw.rectangle([rect_x1, rect_y1, rect_x2, rect_y2], outline="black", fill="lightgray" if is_header else "white")
        
        if align == "left":
            text_x = x + 5
            anchor = "lm"
        else:
            text_x = x + width // 2
            anchor = "mm"
        
        text_y = y + cell_height // 2
        draw.text((text_x, text_y), text, fill="black", anchor=anchor, font=font)

        # Draw strikethrough if enabled
        if strike_through:
            text_width, _ = font.getbbox(text)[2:4]
            line_y = text_y  # Middle of the text
            line_x1 = text_x - (text_width // 2) if align == "center" else text_x
            line_x2 = line_x1 + text_width
            draw.line([(line_x1, line_y), (line_x2, line_y)], fill="black", width=2)

    # Draw headers
    y_offset = padding
    x_offset = padding
    for col, header in enumerate(data["headers"]):
        draw_cell(x_offset, y_offset, header, cell_widths[col], is_header=True)
        x_offset += cell_widths[col]

    # Draw rows
    y_offset += cell_height
    for row in data["rows"]:
        x_offset = padding
        for col, value in enumerate(row):
            align = "left" if col == 1 else "center"  # Left-align Name column
            text, strike_through = value if isinstance(value, tuple) else (value, False)
            draw_cell(x_offset, y_offset, str(text), cell_widths[col], align=align, strike_through=strike_through)
            x_offset += cell_widths[col]
        y_offset += cell_height

    # Save or show the image
    # img.show()
    img.save(filename)

if __name__ == "__main__":
    players = ["Spieler 1", "NudelForce"]
    rows = [[
        rank+1,
        player,
    ] for rank, player in enumerate(players)]

    data = {
        "headers": ["Rang", "Name"],
        "rows": rows
    }
    generate_image(data, "testimage.png")
    