import requests
from bs4 import BeautifulSoup

def request_website(url, list_selector: str = None, selectors: dict[str, str|tuple[str, str]] = None):
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        elements = soup.select(list_selector)

        result = []
        if elements and selectors:
            for element in elements:
                item = {}
                for key, selector in selectors.items():
                    if key == "authors":
                        # Collect all authors under .css-l31Oj
                        authors = []
                        for author_el in element.select(selector):
                            author = {
                                "avatar": author_el.select_one(".css-UJQPz > img")["src"] if author_el.select_one(".css-UJQPz > img") else None,
                                "name": author_el.select_one(".css-VxlIl").get_text(strip=True) if author_el.select_one(".css-VxlIl") else None,
                                "link": author_el.select_one(".css-VxlIl")["href"] if author_el.select_one(".css-VxlIl") else None,
                            }
                            authors.append(author)
                        item[key] = authors
                    elif isinstance(selector, tuple):
                        sub_element = element.select_one(selector[0])
                        item[key] = sub_element[selector[1]] if sub_element else None
                    else:
                        sub_element = element.select_one(selector)
                        if sub_element:
                            if key == "description":
                                item[key] = sub_element.decode_contents()  # Preserve HTML
                            else:
                                item[key] = sub_element.get_text(strip=True)
                        else:
                            item[key] = None
                result.append(item)
        elif elements:
            for element in elements:
                result.append(element.get_text(strip=True))


        if selectors or list_selector:
            return result

        return response.text
    except requests.RequestException as e:
        print(f"An error occurred: {e}")
        return None


if __name__ == "__main__":
    selectors = {
        "title": "h3.css-mbeyN",
        "authors": ".css-IERJH",  # Special handling in code above
        "type": ".css-e7rj5",
        "type_url": (".css-e7rj5", "href"),
        "url": (".css-0c84N > a", "href"),
        "description": ".css-RvQUy > p",
        # "img": ("picture img", "src") # not needed, because the url will already generate a previrew in discord
    }
    result = request_website("https://magic.wizards.com/en/news", "article", selectors)
    print(result)