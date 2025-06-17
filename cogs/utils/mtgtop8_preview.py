import re
from ezcord import log, Cog
import discord
from discord.ext import commands
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import asyncio
from PIL import Image
from io import BytesIO
from collections import defaultdict, deque
from enum import Enum

MTGTOP8_URL_REGEX = r"https?://mtgtop8\.com/event\?(?:[^ ]*?&)?d=\d+(?:&[^ ]*)?"

# Enum for card groups
class CardGroup(Enum):
    LANDS = "LANDS"
    CREATURES = "CREATURES"
    INSTANTS_AND_SORC = "INSTANTS and SORC."
    OTHER_SPELLS = "OTHER SPELLS"
    SIDEBOARD = "SIDEBOARD"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_string(cls, label):
        for group in cls:
            if label.upper() == group.value:
                return group
        return cls.UNKNOWN

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
    
async def request_scryfall_card_images(deck_list: list[Card]):
    # Split deck_list into batches of 75 cards (Scryfall API limit)
    batch_size = 75
    for i in range(0, len(deck_list), batch_size):
        batch = deck_list[i:i+batch_size]
        identifiers = [{"name": card.name} for card in batch]
        response = requests.post(
            "https://api.scryfall.com/cards/collection",
            json={"identifiers": identifiers},
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            data = response.json()
            cards_data = data.get("data", [])
            # Map card names to image URLs
            name_to_url = {}
            for card_data in cards_data:
                if "image_uris" in card_data:
                    name_to_url[card_data["name"]] = card_data["image_uris"]["large"]
                elif "card_faces" in card_data:
                    name_to_url[card_data["name"]] = card_data["card_faces"][0]["image_uris"]["large"]
            # Assign image_url to each Card in the batch
            for card in batch:
                if card.name in name_to_url:
                    card.image_url = name_to_url[card.name]
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
            try:
                deck_list, title = await request_deck_list(url)
                if deck_list:
                    preview_image = await stack_by_4(deck_list, title)
                    if preview_image:
                        await sent_message.edit(content="", file=discord.File(preview_image, filename="preview.png"))
                    else:
                        await sent_message.edit(content="⚠️ Konnte deck infos nicht extrahieren.")
                else:
                    await sent_message.edit(content="⚠️ Keine deckliste unter der URL gefunden.")
            except requests.exceptions.ReadTimeout as e:
                host = e.args[0].pool.host if hasattr(e.args[0], 'pool') else "unknown"
                if host:
                    await sent_message.edit(content=f"⏳ Anfrage zu {host} ist ausgelaufen. Bitte versuch es später nochmal.")
                else:
                    await sent_message.edit(content="⏳ Anfrage ist ausgelaufen. Bitte versuch es später nochmal.")
            except Exception as e:
                await sent_message.edit(content=f"❌ Fehler: {e}")

async def stack_by_4(deck_list: list[Card], title: str = "MTGTop8 Deck Preview"):
    # Constants
    CARD_WIDTH, CARD_HEIGHT = 140, 195
    STACK_OFFSET = 25
    H_SPACING = 20
    V_MARGIN = 10
    BG_COLOR = (0, 0, 0, 0)

    # Separate main deck and sideboard
    main_deck = [card for card in deck_list if card.group != CardGroup.SIDEBOARD]
    sideboard = [card for card in deck_list if card.group == CardGroup.SIDEBOARD]

    # Sort expanded card images so that lands are in the first stacks (reading order)
    # Expand all main deck cards into (name, card) tuples first
    expanded = []
    image_cache = {}

    await request_scryfall_card_images(deck_list)
    
    # Fetch image and resize
    async def fetch_card_image(card: Card):
        image_url: str = await card.request_card_image()
        response = requests.get(image_url)
        return Image.open(BytesIO(response.content)).resize((CARD_WIDTH, CARD_HEIGHT))

    for card in main_deck:
        if card.image_url not in image_cache:
            image_cache[card.image_url] = await fetch_card_image(card)
        for _ in range(card.quantity):
            expanded.append((card, image_cache[card.image_url]))

    # Now, sort expanded so that all lands come first (preserving order within lands/non-lands)
    expanded_lands = [(c, img) for (c, img) in expanded if c.group == CardGroup.LANDS]
    expanded_non_lands = [(c, img) for (c, img) in expanded if c.group != CardGroup.LANDS]
    expanded = expanded_lands + expanded_non_lands
    lands = [card for card in main_deck if card.group == CardGroup.LANDS]
    non_lands = [card for card in main_deck if card.group != CardGroup.LANDS]
    main_deck = lands + non_lands

    # Step 1: Expand all main deck cards
    expanded = []
    image_cache = {}

    for card in main_deck:
        if card.image_url not in image_cache:
            image_cache[card.image_url] = await fetch_card_image(card)
        for _ in range(card.quantity):
            expanded.append((card.name, image_cache[card.image_url]))

    # Step 2: Build stacks of exactly 4 cards
    stacks = []
    grouped = defaultdict(deque)
    for name, img in expanded:
        grouped[name].append(img)

    # 2a: Take all lands first (regardless of quantity)
    for name in list(grouped):
        # Find the card object for this name
        card_obj = next((c for c in main_deck if c.name == name), None)
        if card_obj and card_obj.group == CardGroup.LANDS:
            stack = []
            while grouped[name]:
                stack.append((name, grouped[name].popleft()))
                if len(stack) == 4:
                    stacks.append(stack)
                    stack = []
            if stack:
                stacks.append(stack)
            del grouped[name]

    # 2a: Take all full 4-ofs first
    for name in list(grouped):
        if len(grouped[name]) >= 4:
            stack = [(name, grouped[name].popleft()) for _ in range(4)]
            stacks.append(stack)
            if not grouped[name]:
                del grouped[name]

    # 2b: Mix remaining cards into 4-card stacks
    remaining = []
    for name, imgs in grouped.items():
        for img in imgs:
            remaining.append((name, img))

    for i in range(0, len(remaining), 4):
        chunk = remaining[i:i+4]
        stacks.append(chunk)

    # Step 3: Prepare sideboard stack (all sideboard cards in one stack)
    sideboard_images = []
    # Add a "Sideboard" header image at the top of the sideboard stack (only once)
    if sideboard:
        HEADER_WIDTH, HEADER_HEIGHT = 140, 40
        header_img = Image.new("RGBA", (HEADER_WIDTH, HEADER_HEIGHT), (30, 30, 30, 255))
        draw = ImageDraw.Draw(header_img)
        try:
            font = ImageFont.truetype("assets/beleren.ttf", 24)
        except Exception:
            font = ImageFont.load_default()
        text = "Sideboard"
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        # Move the text a bit higher (e.g., 4 pixels from the top)
        draw.text(
            ((HEADER_WIDTH - text_width) // 2, 4),
            text,
            font=font,
            fill=(255, 255, 255, 255)
        )
        sideboard_images.append(("Sideboard", header_img))
        # Add each sideboard card according to its quantity
        for card in sideboard:
            if card.image_url not in image_cache:
                image_cache[card.image_url] = await fetch_card_image(card)
            for _ in range(card.quantity):
                sideboard_images.append((card.name, image_cache[card.image_url]))

    # Step 4: Create canvas with row logic
    STACKS_PER_ROW = 5
    num_rows = (len(stacks) + STACKS_PER_ROW - 1) // STACKS_PER_ROW

    # --- Title rendering ---
    # Prepare title font and size
    try:
        title_font = ImageFont.truetype("assets/beleren.ttf", 32)
    except Exception:
        title_font = ImageFont.load_default()
    title_text = title
    # Estimate title height
    title_bbox = title_font.getbbox(title_text)
    title_width = title_bbox[2] - title_bbox[0]
    title_height = title_bbox[3] - title_bbox[1]
    TITLE_MARGIN = 20
    TITLE_AREA_HEIGHT = title_height + 2 * TITLE_MARGIN

    # Calculate canvas size (add space for sideboard stack and title)
    canvas_width = H_SPACING + (STACKS_PER_ROW + 1) * (CARD_WIDTH + H_SPACING)
    canvas_height = TITLE_AREA_HEIGHT + num_rows * (V_MARGIN * 2 + STACK_OFFSET * 3 + CARD_HEIGHT)

    img = Image.new("RGBA", (canvas_width, canvas_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Draw the title at the top center
    draw.text(
        ((canvas_width - title_width) // 2, TITLE_MARGIN),
        title_text,
        font=title_font,
        fill=(255, 255, 255, 255)
    )

    # Step 5: Draw main deck stacks in rows
    for index, stack in enumerate(stacks):
        row = index // STACKS_PER_ROW
        col = index % STACKS_PER_ROW

        x = H_SPACING + col * (CARD_WIDTH + H_SPACING)
        y_offset = TITLE_AREA_HEIGHT + row * (V_MARGIN * 2 + STACK_OFFSET * 3 + CARD_HEIGHT)

        for i, (name, card_img) in enumerate(stack):
            y = y_offset + V_MARGIN + i * STACK_OFFSET
            img.paste(card_img, (x, y))

    # Step 6: Draw sideboard stack to the right of main deck stacks, vertically centered
    if sideboard_images:
        # Stack all sideboard cards vertically with offset
        sb_stack_height = V_MARGIN * 2 + STACK_OFFSET * (len(sideboard_images) - 1) + CARD_HEIGHT
        sb_y = TITLE_AREA_HEIGHT + (canvas_height - TITLE_AREA_HEIGHT - sb_stack_height) // 2
        sb_x = H_SPACING + STACKS_PER_ROW * (CARD_WIDTH + H_SPACING)
        for i, (name, card_img) in enumerate(sideboard_images):
            y = sb_y + V_MARGIN + i * STACK_OFFSET
            img.paste(card_img, (sb_x, y))

    # Save
    img.save("deck_preview.png")
    return "deck_preview.png"

async def request_deck_list(url: str) -> list[Card]:
    url += "&switch=text"  # Ensure we get the text version

    response = requests.get(url, timeout=5)
    if not response.ok:
        raise IOError(f"Failed to fetch deck list from {url}: {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")

    cards = []
    
    for column in soup.find_all('div', style=lambda v: v and v.startswith('margin:3px')):
        current_group = CardGroup.UNKNOWN

        for child in column.find_all('div', recursive=False):
            classes = child.get('class', [])

            if 'O14' in classes:
                label = child.get_text(strip=True).upper()
                match = re.search(r'\d+\s+(.*)', label)
                group_label = match.group(1) if match else label
                current_group = CardGroup.from_string(group_label)
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

    return cards, title

def setup(bot):
    bot.add_cog(MTGTop8Preview(bot))

if __name__ == "__main__":
    async def main():
        deck_list, title = await request_deck_list("https://mtgtop8.com/event?e=69978&d=729958&f=LE&switch=visual")
        await stack_by_4(deck_list, title)
        # screenshot_element_before_card_div("https://mtgtop8.com/event?e=69978&d=729958&f=LE&switch=visual")

    asyncio.run(main())
