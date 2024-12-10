import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

def get_favicon_url(url):
    try:
        # Make an HTTP request to get the webpage content
        response = requests.get(url)
        response.raise_for_status()  # Check if the request was successful

        # Parse the HTML content of the page
        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for <link> tag with rel="icon" or rel="shortcut icon"
        favicon_link = soup.find('link', rel=lambda rel: rel and 'icon' in rel)

        if favicon_link and 'href' in favicon_link.attrs:
            # Get the href attribute (favicon URL)
            favicon_url = favicon_link['href']
            # Resolve relative URLs to absolute
            return urljoin(url, favicon_url)
        else:
            return None  # No favicon found

    except requests.exceptions.RequestException as e:
        print(f"Error fetching the URL: {e}")
        return None

# Example usage
url = 'https://pauper-spezl.de/'
favicon = get_favicon_url(url)
print(f"Favicon URL: {favicon}")
