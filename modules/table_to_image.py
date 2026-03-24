from PIL import Image, ImageDraw, ImageFont
import os
import sys


def _load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return None


def _load_fallback_font(size):
    candidates = []
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates = [
            os.path.join(windir, "Fonts", "seguiemj.ttf"),
            os.path.join(windir, "Fonts", "seguisym.ttf"),
            os.path.join(windir, "Fonts", "arial.ttf"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Apple Color Emoji.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    for path in candidates:
        font = _load_font(path, size)
        if font:
            return font
    return ImageFont.load_default()


def _is_emoji_or_special(char):
    """Check if a character is an emoji or special character that needs fallback font."""
    code = ord(char)
    # Emoji ranges
    emoji_ranges = [
        (0x1F300, 0x1F9FF),  # Miscellaneous Symbols and Pictographs, Emoticons, etc.
        (0x2600, 0x26FF),    # Miscellaneous Symbols
        (0x2700, 0x27BF),    # Dingbats
        (0xFE00, 0xFE0F),    # Variation Selectors
        (0x1F000, 0x1F02F),  # Mahjong Tiles
        (0x1F0A0, 0x1F0FF),  # Playing Cards
        (0x1F100, 0x1F64F),  # Enclosed Characters, Emoticons
        (0x1F680, 0x1F6FF),  # Transport and Map Symbols
        (0x1F900, 0x1F9FF),  # Supplemental Symbols and Pictographs
        (0x1FA00, 0x1FA6F),  # Chess Symbols, Symbols and Pictographs Extended-A
        (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
        (0x2300, 0x23FF),    # Miscellaneous Technical
        (0x2B50, 0x2B50),    # Star
        (0xE0000, 0xE007F),  # Tags
    ]
    return any(start <= code <= end for start, end in emoji_ranges)


def _split_runs(text, primary, fallback):
    if not text:
        return []

    runs = []
    current_font = fallback if _is_emoji_or_special(text[0]) else primary
    buffer = text[0]

    for ch in text[1:]:
        font = fallback if _is_emoji_or_special(ch) else primary
        if font is current_font:
            buffer += ch
        else:
            runs.append((buffer, current_font))
            buffer = ch
            current_font = font

    runs.append((buffer, current_font))
    return runs


def _text_width(text, primary, fallback):
    width = 0
    for run_text, run_font in _split_runs(text, primary, fallback):
        bbox = run_font.getbbox(run_text)
        width += bbox[2] - bbox[0]
    return width


def _draw_text(draw, x, y, text, primary, fallback):
    cursor_x = x
    for run_text, run_font in _split_runs(text, primary, fallback):
        draw.text((cursor_x, y), run_text, fill="black", anchor="lm", font=run_font)
        bbox = run_font.getbbox(run_text)
        cursor_x += bbox[2] - bbox[0]


def generate_image(data, filename, font_path="arial.ttf"):
    try:
        font = ImageFont.truetype(font_path, 20)
    except IOError:
        print(f"Warning: Couldn't load font '{font_path}', using default font.")
        font = ImageFont.load_default()

    fallback_font = _load_fallback_font(20)

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
            if isinstance(value, tuple):
                # Support both old (text, strike) and new (text, strike, highlight) formats
                text = value[0]
            else:
                text = value
            column_texts.append(str(text))

        # Calculate max text width for this column
        text_widths = [_text_width(text, font, fallback_font) for text in column_texts]
        max_width = max(text_widths) + 20  # Add padding
        cell_widths.append(max_width)

    # Calculate image size
    img_width = sum(cell_widths) + 2 * padding
    img_height = cell_height * num_rows + 2 * padding

    # Create image
    img = Image.new("RGB", (img_width, img_height), "white")
    draw = ImageDraw.Draw(img)

    # Function to draw a cell
    def draw_cell(x, y, text, width, is_header=False, align="center", strike_through=False, highlight=False):
        rect_x1, rect_y1 = x, y
        rect_x2, rect_y2 = x + width, y + cell_height
        
        # Determine cell background color
        if is_header:
            fill_color = "lightgray"
        elif highlight:
            fill_color = "lightgreen"
        else:
            fill_color = "white"
        
        draw.rectangle([rect_x1, rect_y1, rect_x2, rect_y2], outline="black", fill=fill_color)

        text_y = y + cell_height // 2
        text_width = _text_width(text, font, fallback_font)
        if align == "left":
            text_x = x + 5
        else:
            text_x = x + (width - text_width) // 2

        _draw_text(draw, text_x, text_y, text, font, fallback_font)

        if strike_through:
            line_y = text_y
            line_x1 = text_x if align == "left" else text_x
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
            align = "left" if data["headers"][col].lower() in ["name", "spieler", "player", "spieler 1", "gegner", "spieler 2", "match ergebnis (s-n-u)"] else "center"
            
            # Handle different tuple formats
            if isinstance(value, tuple):
                if len(value) == 3:
                    # (text, strike_through, highlight)
                    text, strike_through, highlight = value
                elif len(value) == 2:
                    # (text, strike_through) - old format
                    text, strike_through = value
                    highlight = False
                else:
                    text = value
                    strike_through = False
                    highlight = False
            else:
                text = value
                strike_through = False
                highlight = False
            
            draw_cell(x_offset, y_offset, str(text), cell_widths[col], align=align, strike_through=strike_through, highlight=highlight)
            x_offset += cell_widths[col]
        y_offset += cell_height

    # Save the image
    img.save(filename)
