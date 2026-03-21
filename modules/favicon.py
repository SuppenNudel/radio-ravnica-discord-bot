import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from PIL import Image
from io import BytesIO
from ezcord import log

def get_favicon_url(website_url):
    try:
        # Fetch the website content
        response = requests.get(website_url)
        response.raise_for_status()

        # Parse the HTML using BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find all <link> tags with a rel attribute containing "icon"
        icon_links = soup.find_all('link', rel=lambda rel: rel and 'icon' in rel.lower())
        favicons = []

        for link in icon_links:
            href = link.get('href')
            if href:
                # Get full URL of the favicon
                favicon_url = urljoin(website_url, href)

                # Get sizes attribute (if available)
                sizes = link.get('sizes')
                if sizes:
                    # Parse width and height from sizes attribute (e.g., "32x32")
                    try:
                        width, height = map(int, sizes.split('x'))
                        resolution = width * height
                    except ValueError:
                        resolution = 0
                else:
                    resolution = 0  # Default resolution if sizes attribute is missing

                favicons.append((favicon_url, resolution))

        if not favicons:
            # If no <link> tags found, try the default location
            default_favicon = urljoin(website_url, '/favicon.ico')
            favicons.append((default_favicon, 16 * 16))  # Default size assumed

        # Sort favicons by resolution in descending order
        favicons.sort(key=lambda x: x[1], reverse=True)

        # Return the favicon with the highest resolution
        return favicons[0][0] if favicons else None

    except Exception as e:
        log.error(f"Error: {e}")
        return None
    
def convert_ico_to_png(ico_url, output_path="tmp/icon.png"):
    # Download the ICO file
    response = requests.get(ico_url)
    response.raise_for_status()  # Ensure the request was successful
    
    # Open the ICO file as an image
    ico_image = Image.open(BytesIO(response.content))
    
    # Save the image as PNG
    ico_image.save(output_path, format="PNG")
    log.debug(f"Converted ICO file to PNG: {output_path}")

    return output_path

if __name__ == "__main__":
    urls = {
        "taschengeld-dieb": "https://taschengelddieb.de/Community-Laden-Dueren-Taschengelddieb",
        "southside": 'https://www.42southside.de/veranstaltungskalender/besondere-veranstaltungen/',
        "battlebear": "https://www.battle-bear.de/",
        "spiele-pyramide": "https://fans.spiele-pyrami.de/",
        "fanfinity": "https://www.fanfinity.gg/event/spotlight-series-utrecht/"
    }

    for store, url in urls.items():
        # Example usage
        favicon = get_favicon_url(url)
        if favicon:
            new_image = convert_ico_to_png(favicon)
            log.debug(f"Favicon URL: {favicon}")
        else:
            log.debug("Favicon is None")
