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
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image
from io import BytesIO
import time

import os
import stat

gecko_path = "assets/geckodriver" if platform.system() == "Linux" else "assets/geckodriver.exe"
PORTABLE_FIREFOX_PATH = "assets/firefox/firefox"

# Add execute permission for owner, group, others
st = os.stat(gecko_path)
os.chmod(gecko_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
st = os.stat(PORTABLE_FIREFOX_PATH)
os.chmod(PORTABLE_FIREFOX_PATH, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

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
                preview_image = screenshot_element_before_card_div(url)
                if preview_image:
                    await message.channel.send(file=discord.File(preview_image, filename="preview.png"))
                else:
                    await message.channel.send("⚠️ Could not extract deck info.")
            except Exception as e:
                await message.channel.send(f"❌ Error: {e}")

async def generate_preview_from_visual(url: str):
    pass

def screenshot_element_before_card_div(url):
    url += "&switch=visual"  # Ensure we are in visual mode
    options = Options()
    options.add_argument('--headless')
    options.binary_location = PORTABLE_FIREFOX_PATH
    service = Service(executable_path=gecko_path)
    driver = webdriver.Firefox(
        options=options,
        service=service
    )

    driver.set_window_size(1920, 2000)

    try:
        driver.get(url)

        wait = WebDriverWait(driver, 10)

        # ✅ Try to accept the cookie popup
        try:
            accept_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[.//span[text()='Alle akzeptieren']]")
                )
            )
            accept_button.click()
            print("✅ Accepted cookies.")
            time.sleep(1)  # Give time for dialog to disappear
        except Exception:
            print("⚠️ No cookie dialog found (or already accepted).")

        # Locate the target element
        card_div = wait.until(EC.presence_of_element_located((By.ID, "card_div")))
        previous_element = card_div.find_element(By.XPATH, "preceding-sibling::*[1]")

        # Scroll into view
        driver.execute_script("arguments[0].scrollIntoView();", previous_element)
        time.sleep(0.5)

        # Screenshot
        png = driver.get_screenshot_as_png()
        image = Image.open(BytesIO(png))

        location = previous_element.location_once_scrolled_into_view
        size = previous_element.size
        left = int(location['x'])
        top = int(location['y'])
        right = left + int(size['width'])
        bottom = top + int(size['height'])

        cropped_image = image.crop((left, top, right, bottom))

        output = BytesIO()
        cropped_image.save(output, format="PNG")
        output.seek(0)
        return output

    finally:
        driver.quit()

async def generate_preview_from_text(url: str):
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
        screenshot_element_before_card_div("https://mtgtop8.com/event?e=69978&d=729958&f=LE&switch=visual")

    asyncio.run(main())
