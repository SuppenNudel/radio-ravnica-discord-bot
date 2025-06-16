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

    # Generate image with Pillow
    img = Image.new("RGB", (600, 400), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)

    font_title = ImageFont.truetype("arial.ttf", 24)
    font_cards = ImageFont.truetype("arial.ttf", 16)

    draw.text((10, 10), title, font=font_title, fill="white")

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
        await generate_preview_from_text("https://mtgtop8.com/event?e=69978&d=729958&f=LE&switch=visual")

    asyncio.run(main())
