import re
from ezcord import log, Cog
import discord
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import asyncio
from PIL import Image
from io import BytesIO
from enum import Enum
from modules import env
import io

MTGTOP8_URL_REGEX = r"https?://[w]{0,3}\.?mtgtop8\.com/event\?(?:[^ ]*?&)?d=\d+(?:&[^ ]*)?"

# Enum for card groups
class CardGroup(Enum):
    COMMANDER = "COMMANDER"
    LANDS = "LANDS"
    CREATURES = "CREATURES"
    INSTANTS_AND_SORC = "INSTANTS AND SORC."
    OTHER_SPELLS = "OTHER SPELLS"
    SIDEBOARD = "SIDEBOARD"

    @classmethod
    def from_string(cls, label):
        for group in cls:
            if label.upper() == group.value:
                return group
        raise ValueError(f"{label} is not part of the CardGroup enum")

# Card class
class Card:
    def __init__(self, name: str, quantity: int, group: CardGroup):
        self.name = name
        self.quantity = quantity
        self.group = group
        self.image_url = None

    def __repr__(self):
        return f"{self.quantity}x {self.name} ({self.group.name})"
    
    async def request_card_image(self):
        if self.image_url:
            return self.image_url
        search_url = f"https://api.scryfall.com/cards/search?q=!\"{self.name}\" game:paper"
        response = requests.get(search_url)
        if response.status_code == 200:
            data = response.json()
            data = data['data'][0]
            if 'image_uris' in data:
                self.image_url = data['image_uris']['large']
            else:
                if 'card_faces' in data:
                    # If the card has multiple faces, we can choose the first one
                    self.image_url = data['card_faces'][0]['image_uris']['large']
                else:
                    log.error(f"No image found for {self.name}")
        else:
            log.error(f"Failed to fetch card image for {self.name}: {response.status_code}")
        return self.image_url
    
def shrink_image_if_needed(image_path, output_path, max_size_mb=10):
    max_size_bytes = max_size_mb * 1024 * 1024

    with Image.open(image_path) as img:
        scale_factor = 0.95  # shrink a bit each iteration
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        
        while buffer.tell() > max_size_bytes:
            # Shrink the image
            new_size = (int(img.width * scale_factor), int(img.height * scale_factor))
            img = img.resize(new_size, Image.LANCZOS)
            
            # Recheck size
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')

        # Save the final version
        with open(output_path, 'wb') as f:
            f.write(buffer.getvalue())
    
async def request_scryfall_card_images(deck_list: list[Card]):
    IMAGE_TYPE = "border_crop"

    # Replace all "/" in card names with "//"
    for card in deck_list:
        card.name = card.name.replace("/", "//")

    # Prepare identifiers, including names before "//" for split cards
    identifiers = []
    for card in deck_list:
        identifiers.append({"name": card.name})
        if "//" in card.name:
            front_name = card.name.split(" // ")[0].strip()
            if not any(iden["name"] == front_name for iden in identifiers):
                identifiers.append({"name": front_name})

    # Split identifiers into batches of 75 cards (Scryfall API limit)
    batch_size = 75
    for i in range(0, len(identifiers), batch_size):
        batch = identifiers[i:i+batch_size]
        response = requests.post(
            "https://api.scryfall.com/cards/collection",
            json={"identifiers": batch},
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("not_found"):
                print(f"WARNING: Cards not found: {data['not_found']}")
            cards_data = data.get("data", [])
            # Map card names to image URLs
            name_to_url = {}
            for card_data in cards_data:
                image_url = None
                if "image_uris" in card_data:
                    image_url = card_data["image_uris"].get(IMAGE_TYPE) or card_data["image_uris"].get("large")
                elif "card_faces" in card_data:
                    image_url = card_data["card_faces"][0]["image_uris"].get(IMAGE_TYPE) or card_data["card_faces"][0]["image_uris"].get("large")
                if image_url:
                    name_to_url[card_data["name"]] = image_url
                    if "//" in card_data["name"]:
                        name_to_url[card_data["name"].split(" // ")[0].strip()] = image_url
                else:
                    log.error(f"No image found for {card_data['name']} in Scryfall response")
            # Assign image_url to each Card in the deck_list
            for card in deck_list:
                if card.name in name_to_url:
                    card.image_url = name_to_url[card.name]
                elif "//" in card.name:
                    front_name = card.name.split(" // ")[0].strip()
                    if front_name in name_to_url:
                        card.image_url = name_to_url[front_name]
        else:
            log.error(f"Failed to fetch card images from Scryfall: {response.status_code}")

class MTGTop8Preview(Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @Cog.listener()
    async def on_ready(self):
        log.debug(self.__class__.__name__ + " is ready")

    @Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        match = re.search(MTGTOP8_URL_REGEX, message.content)
        if match:
            url = match.group(0)
            sent_message = await message.reply("🔍 MTGTop8-Deck-Infos werden abgerufen und Vorschau wird erstellt...")
            deck_list = None
            try:
                deck_list, title, deck_id = await request_deck_list(url)
            except requests.exceptions.ReadTimeout as e:
                host = e.args[0].pool.host if hasattr(e.args[0], 'pool') else "unknown"
                if host:
                    await sent_message.edit(content=f"⏳ Anfrage zu {host} ist ausgelaufen. Bitte versuch es später nochmal.")
                else:
                    await sent_message.edit(content="⏳ Anfrage ist ausgelaufen. Bitte versuch es später nochmal.")
            except discord.errors.HTTPException as e:
                if e.code == 413:
                    await sent_message.edit(content=f"🖼 das generierte Bild ist leider zu groß zum Hochladen. <@356120044754698252> guck mal bitte nach! :)")
            except Exception as e:
                await sent_message.edit(content=f"❌ Fehler: {e}")

            if deck_list:
                preview_image = await stack_cards(deck_id, deck_list, title)
                if preview_image:
                    link=None # uncomment if upload to imgbb is wanted
                    # link = upload_to_imgbb(preview_image, env.API_KEY_IMGBB)
                    if link:
                        await sent_message.edit(content=link)
                    else:                        
                        # Example usage
                        shrink_image_if_needed(preview_image, preview_image)
                        await sent_message.edit(content="", file=discord.File(preview_image, filename="preview.png"))
                else:
                    await sent_message.edit(content="⚠️ Konnte deck infos nicht extrahieren.")
            else:
                await sent_message.edit(content="⚠️ Keine deckliste unter der URL gefunden.")

async def stack_cards(deck_id, deck_list: list[Card], title: str = "MTGTop8 Deck Preview"):
    # Constants
    CARD_WIDTH = 200
    CARD_HEIGHT = int(CARD_WIDTH * 1.4) # mtg card ratio
    H_SPACING = 10
    V_MARGIN = 10
    STACK_OVERLAP = 27  # Amount of vertical overlap between stacked cards
    STACK_OVERLAP_SIDEBOARD = STACK_OVERLAP * 2

    # Separate main deck and sideboard
    main_deck = [card for card in deck_list if card.group != CardGroup.SIDEBOARD]
    sideboard = [card for card in deck_list if card.group == CardGroup.SIDEBOARD]

    card_group_list = list(CardGroup)

    # Sort main deck by group order, then by name
    def group_sort_key(card:Card):
        return (card_group_list.index(card.group), card.name.lower())
        
    main_deck = sorted(main_deck, key=group_sort_key)

    # Fetch all images
    await request_scryfall_card_images(deck_list)

    # Prepare image cache
    image_cache = {}

    async def fetch_card_image(card: Card):
        if card.image_url not in image_cache:
            response = requests.get(card.image_url)
            img = Image.open(BytesIO(response.content)).resize((CARD_WIDTH, CARD_HEIGHT))
            image_cache[card.image_url] = img
        return image_cache[card.image_url]

    # Prepare stacks: group by card, stack up to 4, show number if >4
    stacks = []
    for card in main_deck:
        img = await fetch_card_image(card)
        stacks.append((card, img, card.quantity))

    # Prepare sideboard stack (all sideboard cards overlapped)
    sideboard_images = []
    if sideboard:
        HEADER_WIDTH, HEADER_HEIGHT = 140, 40
        header_img = Image.new("RGB", (HEADER_WIDTH, HEADER_HEIGHT))
        draw_sb = ImageDraw.Draw(header_img)
        try:
            font = ImageFont.truetype("assets/beleren.ttf", 24)
        except Exception:
            font = ImageFont.load_default()
        text = "Sideboard"
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        # Get bounding box of the text
        bbox = font.getbbox(text)
        left, top, right, bottom = bbox
        text_width = right - left
        text_height = bottom - top

        draw_sb.text(
            ((HEADER_WIDTH - text_width) // 2, 4),
            text,
            font=font,
            fill=(255, 255, 255, 255)
        )
        sideboard_images.append(("Sideboard", header_img))
        # Overlap all sideboard cards
        sb_imgs = []
        sb_cards = []
        for card in sideboard:
            img = await fetch_card_image(card)
            for _ in range(card.quantity):
                sb_imgs.append(img)
                sb_cards.append(card)
        if sb_imgs:
            # Create one image with all sideboard cards overlapped
            sb_stack_height = CARD_HEIGHT + (len(sb_imgs) - 1) * STACK_OVERLAP_SIDEBOARD
            sb_stack_img = Image.new("RGB", (CARD_WIDTH, sb_stack_height))
            for i, img in enumerate(sb_imgs):
                sb_stack_img.paste(img, (0, i * STACK_OVERLAP_SIDEBOARD))
            sideboard_images.append(("SideboardStack", sb_stack_img, len(sb_imgs)))

    # --- Title rendering ---
    try:
        title_font = ImageFont.truetype("assets/beleren.ttf", 45)
    except Exception:
        title_font = ImageFont.load_default()
    title_text = title
    title_bbox = title_font.getbbox(title_text)
    title_width = title_bbox[2] - title_bbox[0]
    title_height = title_bbox[3] - title_bbox[1]
    TITLE_MARGIN = 20
    TITLE_AREA_HEIGHT = title_height + 2 * TITLE_MARGIN

    # Layout
    if len(main_deck) <= 24:
        STACKS_PER_ROW = 6
    else:
        STACKS_PER_ROW = 10

    # Calculate max stack height for vertical stacking with overlap
    def stack_height(qty):
        if qty <= 4:
            return CARD_HEIGHT + (qty - 1) * STACK_OVERLAP if qty > 0 else 0
        else:
            return CARD_HEIGHT

    max_stack_height = max(stack_height(card.quantity) for card, _, _ in stacks) if stacks else CARD_HEIGHT
    num_rows = (len(stacks) + STACKS_PER_ROW - 1) // STACKS_PER_ROW
    # Calculate sideboard stack height for canvas
    sb_stack_img_height = 0
    if sideboard_images and len(sideboard_images) > 1:
        sb_stack_img_height = sideboard_images[1][1].height
    sb_total_height = (sideboard_images[0][1].height if sideboard_images else 0) + sb_stack_img_height

    canvas_width = H_SPACING + (STACKS_PER_ROW + 1) * (CARD_WIDTH + H_SPACING)
    canvas_height = max(
        TITLE_AREA_HEIGHT + num_rows * (V_MARGIN * 2 + max_stack_height),
        TITLE_AREA_HEIGHT + sb_total_height + V_MARGIN * 2
    )

    img = Image.new("RGB", (canvas_width, canvas_height))
    draw = ImageDraw.Draw(img)

    # Draw the title at the top center
    draw.text(
        ((canvas_width - title_width) // 2, TITLE_MARGIN),
        title_text,
        font=title_font,
        fill=(255, 255, 255, 255)
    )

    # Draw main deck stacks (cards overlapped under each other)
    try:
        qty_font = ImageFont.truetype("assets/beleren.ttf", 35)
    except Exception:
        qty_font = ImageFont.load_default()

    for index, (card, card_img, quantity) in enumerate(stacks):
        row = index // STACKS_PER_ROW
        col = index % STACKS_PER_ROW
        x = H_SPACING + col * (CARD_WIDTH + H_SPACING)
        y = TITLE_AREA_HEIGHT + row * (V_MARGIN * 2 + max_stack_height)

        if quantity <= 4:
            # Overlap the cards vertically
            for i in range(quantity):
                img.paste(card_img, (x, y + i * STACK_OVERLAP))
        else:
            # Paste one card and write the quantity
            img.paste(card_img, (x, y))
            qty_text = f"x{quantity}"
            bbox = qty_font.getbbox(qty_text)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            text_x = x + CARD_WIDTH - text_width - 8
            text_y = y + CARD_HEIGHT / 2 - text_height - 8
            # Draw a semi-transparent rectangle behind the text for readability
            rect_x0 = text_x - 4
            rect_y0 = text_y - 2
            rect_x1 = text_x + text_width + 4
            rect_y1 = text_y + text_height + 2
            draw.rectangle([rect_x0, rect_y0, rect_x1, rect_y1], fill=(0, 0, 0, 180))
            draw.text((text_x, text_y), qty_text, font=qty_font, fill=(255, 255, 0, 255))

    # Draw sideboard stack to the right of main deck stacks, vertically centered
    if sideboard_images:
        sb_x = H_SPACING + STACKS_PER_ROW * (CARD_WIDTH + H_SPACING)
        # Draw header
        header_img = sideboard_images[0][1]
        sb_y = TITLE_AREA_HEIGHT + V_MARGIN
        img.paste(header_img, (sb_x, sb_y))
        y_offset = sb_y + header_img.height
        # Draw overlapped sideboard stack
        if len(sideboard_images) > 1:
            _, sb_stack_img, sb_qty = sideboard_images[1]
            img.paste(sb_stack_img, (sb_x, y_offset))
            # Draw quantity if more than 1
            # if sb_qty > 1:
            #     qty_text = f"x{sb_qty}"
            #     bbox = qty_font.getbbox(qty_text)
            #     text_width = bbox[2] - bbox[0]
            #     text_height = bbox[3] - bbox[1]
            #     text_x = sb_x + CARD_WIDTH - text_width - 8
            #     text_y = y_offset + sb_stack_img.height - text_height - 8
            #     rect_x0 = text_x - 4
            #     rect_y0 = text_y - 2
            #     rect_x1 = text_x + text_width + 4
            #     rect_y1 = text_y + text_height + 2
            #     draw.rectangle([rect_x0, rect_y0, rect_x1, rect_y1], fill=(0, 0, 0, 180))
                # draw.text((text_x, text_y), qty_text, font=qty_font, fill=(255, 255, 0, 255))

    file_location = f"tmp/deck_preview_{deck_id}.png"
    img.save(file_location)
    return file_location

import urllib.parse

async def request_deck_list(url: str) -> tuple[list[Card], str, str]:
    url += "&switch=text"  # Ensure we get the text version

    # Extract deck_id from the URL (d= parameter)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    deck_id = query.get("d", [None])[0]
    if not deck_id:
        raise ValueError("No deck_id (d= parameter) found in the URL.")

    response = requests.get(url, timeout=5)
    if not response.ok:
        raise IOError(f"Failed to fetch deck list from {url}: {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")

    cards = []
    current_group = None  # Persist across columns

    for column in soup.find_all('div', style=lambda v: v and v.startswith('margin:3px')):
        for child in column.find_all('div', recursive=False):
            classes = child.get('class', [])

            if 'O14' in classes:
                label = child.get_text(strip=True).upper()
                match = re.search(r'\d+\s+([A-Z\s\.&]+)(\(\d+\))?', label)
                group_label = match.group(1) if match else label
                current_group = CardGroup.from_string(group_label.strip())
                continue

            if 'deck_line' in classes:
                qty_text = child.text.strip().split(' ', 1)[0]
                try:
                    qty = int(qty_text)
                except ValueError:
                    qty = 1

                name_tag = child.find('span', class_='L14')
                card_name = name_tag.get_text(strip=True) if name_tag else None

                if card_name:
                    cards.append(Card(card_name, qty, current_group))

    # Grab basic info (example: deck name, player, event name, format)
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "MTGTop8 Deck"

    return cards, title, deck_id

def upload_to_imgbb(image_path, api_key):
    with open(image_path, 'rb') as f:
        encoded_image = f.read()

    response = requests.post(
        'https://api.imgbb.com/1/upload',
        params={
            'key': api_key,
        },
        files={
            'image': encoded_image,
        }
    )

    if response.status_code == 200:
        link = response.json()['data']['url']
        print("Uploaded:", link)
        return link
    else:
        print("Error:", response.status_code, response.text)


def setup(bot):
    bot.add_cog(MTGTop8Preview(bot))

if __name__ == "__main__":
    async def main():
        deck_list, title, deck_id = await request_deck_list("https://mtgtop8.com/event?d=729438")
        await stack_cards(deck_id, deck_list, title)

    asyncio.run(main())
