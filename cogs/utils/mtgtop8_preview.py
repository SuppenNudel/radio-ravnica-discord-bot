import re
import platform
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
import time

import os
import stat

MTGTOP8_URL_REGEX = r"https?://mtgtop8\.com/event\?e=\d+&d=\d+&f=\w+"

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
            await message.channel.send("🔍 Fetching MTGTop8 deck info...")
            try:
                preview_image = await generate_preview_from_text(url)
                if preview_image:
                    await message.channel.send(file=discord.File(preview_image, filename="preview.png"))
                else:
                    await message.channel.send("⚠️ Could not extract deck info.")
            except Exception as e:
                await message.channel.send(f"❌ Error: {e}")

async def generate_preview_from_visual(url: str):
    pass

def stack_cards(card_image_path, copies):
    """
    Given a card image and number of copies, generate a fanned stacked image effect.
    The front card is vertical; only the cards in the back are rotated.
    The rotation axis is at the center of the bottom-left quarter of the card.
    """
    base_img = Image.open(card_image_path).convert("RGBA")
    width, height = base_img.size

    # Rotation axis: middle of bottom-left quarter
    anchor_x = int(width * 0.25)
    anchor_y = int(height * 0.75)

    angle_step = 10  # degrees between each card
    offset_x = 65
    offset_y = 60

    total_angle = angle_step * copies - angle_step
    # Estimate canvas size (generous, to avoid cropping)
    canvas_width = width + int(abs(offset_x) * (copies - 1)) + 60
    canvas_height = height + int(abs(offset_y) * (copies - 1)) + 60
    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

    # Center position for the anchor of the first card
    base_x = 30
    base_y = canvas_height - 400

    for i in range(copies):
        angle = total_angle - i * angle_step

        # Create a transparent image to paste the card onto, so we can rotate around anchor
        temp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        temp.paste(base_img, (0, 0), base_img)

        # Translate so anchor is at (0,0), rotate, then translate back
        expanded = Image.new("RGBA", (width * 2, height * 2), (0, 0, 0, 0))
        expanded.paste(temp, (width - anchor_x, height - anchor_y), temp)
        rotated = expanded.rotate(-angle, resample=Image.BICUBIC, expand=True)

        # Find where to paste: anchor point for each card
        x_offset = base_x + i * offset_x
        y_offset = base_y + i * offset_y

        paste_x = int(x_offset - rotated.size[0] // 2)
        paste_y = int(y_offset - rotated.size[1] // 2)

        canvas.paste(rotated, (paste_x, paste_y), rotated)

    return canvas

async def generate_preview_from_text(url: str):
    url += "&switch=text"
    response = requests.get(url)
    if not response.ok:
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Grab basic info (example: deck name, player, event name, format)
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "MTGTop8 Deck"

    card_lines = soup.find_all("div", {"class": "deck_line"})
    if not card_lines:
        return None

    # Attempt to extract a few sample cards for display
    cards = []
    for line in card_lines:
        # The text is usually like "4 Lightning Bolt"
        qty = line.contents[0].strip() if line.contents else ""
        card_span = line.find("span")
        card_name = card_span.get_text(strip=True) if card_span else ""
        text = f"{qty} {card_name}".strip()
        cards.append(text)
    # Example: Add a stacked card image to the preview (top-right corner)
    # Download a sample card image from Scryfall


    # Generate image with Pillow
    img = Image.new("RGB", (600, 400), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)

    font_title = ImageFont.truetype("assets/beleren.ttf", 24)
    font_cards = ImageFont.truetype("assets/beleren.ttf", 16)

    draw.text((10, 10), title, font=font_title, fill="white")

    # card_img_url = "https://cards.scryfall.io/large/front/3/5/35952c24-d728-4ec6-b0d1-b8183a18554a.jpg?1562904921"
    # resp = requests.get(card_img_url)
    # if resp.ok:
    #     card_img_bytes = BytesIO(resp.content)
    #     stacked_card_img = stack_cards(card_img_bytes, 3)
    #     # Resize if needed to fit the preview image
    #     stacked_card_img = stacked_card_img.resize((240, 340), Image.LANCZOS)
    #     img.paste(stacked_card_img, (260, 10), stacked_card_img)
        
    for i, line in enumerate(cards):
        draw.text((10, 50 + i * 22), line, font=font_cards, fill="white")

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

def setup(bot):
    bot.add_cog(MTGTop8Preview(bot))


if __name__ == "__main__":
    async def main():
        output = await generate_preview_from_text("https://mtgtop8.com/event?e=69978&d=729958&f=LE&switch=visual")
        if output:
            with open("preview.png", "wb") as f:
                f.write(output.read())
            print("Saved preview.png")
        else:
            print("Failed to generate preview.")

    asyncio.run(main())
