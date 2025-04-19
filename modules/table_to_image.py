from PIL import Image, ImageDraw, ImageFont

def generate_image(data, filename, font_path="arial.ttf"):
    # Load font (fallback to default if unavailable)
    # try:
    #     font = ImageFont.truetype("arial.ttf", 20)
    # except IOError:
    #     font = ImageFont.load_default()

    try:
        font = ImageFont.truetype(font_path, 20)
    except IOError:
        print(f"Warning: Couldn't load font '{font_path}', using default font.")
        font = ImageFont.load_default()

    padding = 10
    cell_height = 40

    num_columns = len(data["headers"])
    num_rows = len(data["rows"]) + 1  # +1 for header row

    # Calculate dynamic column widths
    cell_widths = []
    for col in range(num_columns):
        # Collect all texts in this column (including header)
        column_texts = [str(data["headers"][col])]
        for row in data["rows"]:
            value = row[col]
            text = value[0] if isinstance(value, tuple) else str(value)
            column_texts.append(text)

        # Calculate max text width for this column
        text_widths = [font.getbbox(text)[2] for text in column_texts]
        max_width = max(text_widths) + 20  # Add padding
        cell_widths.append(max_width)

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

        if strike_through:
            text_width, _ = font.getbbox(text)[2:4]
            line_y = text_y
            line_x1 = text_x - (text_width // 2) if align == "center" else text_x
            line_x2 = line_x1 + text_width
            draw.line([(line_x1, line_y), (line_x2, line_y)], fill="red", width=2)

    # Draw header row
    y_offset = padding
    x_offset = padding
    for col, header in enumerate(data["headers"]):
        draw_cell(x_offset, y_offset, str(header), cell_widths[col], is_header=True)
        x_offset += cell_widths[col]

    # Draw data rows
    y_offset += cell_height
    for row in data["rows"]:
        x_offset = padding
        for col, value in enumerate(row):
            align = "left" if data["headers"][col].lower() in ["name", "spieler", "player", "spieler 1", "gegner", "spieler 2", "Match Ergebnis (S-N-U)".lower()] else "center"
            text, strike_through = value if isinstance(value, tuple) else (value, False)
            draw_cell(x_offset, y_offset, str(text), cell_widths[col], align=align, strike_through=strike_through)
            x_offset += cell_widths[col]
        y_offset += cell_height

    # Save the image
    img.save(filename)
