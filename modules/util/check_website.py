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
                        for author_el in element.select(".css-l31Oj"):
                            author = {
                                "avatar": author_el.select_one(".css-UZpTh > img")["src"] if author_el.select_one(".css-UZpTh > img") else None,
                                "name": author_el.select_one(".css-Z5ZSx").get_text(strip=True) if author_el.select_one(".css-Z5ZSx") else None,
                                "link": author_el.select_one(".css-Z5ZSx")["href"] if author_el.select_one(".css-Z5ZSx") else None,
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
        "title": "h3.css-9f4rq",
        "authors": ".css-l31Oj",  # Special handling in code above
        "type": ".css-kId4u",
        "type_url": (".css-kId4u", "href"),
        "url": (".css-3qxBv > a", "href"),
        "description": ".css-p4BJO > p",
        # "img": ("picture img", "src")
    }
    result = request_website("https://magic.wizards.com/en/news", "article", selectors)
    print(result)