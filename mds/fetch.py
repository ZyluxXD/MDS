import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


def get_memes(limit=25, memes_extension="/hot"):
    base_url = "https://programmerhumor.io"
    memes_url = f"{base_url}{memes_extension}"
    memes = []

    html = requests.get(memes_url, timeout=15).text
    soup = BeautifulSoup(html, "html.parser")

    for post in soup.select("div.post"):
        title_link = post.select_one("a.post-title-link")
        image = post.select_one("a.post-image-link img")

        if not title_link or not image or title_link["href"].startswith("/go/"):
            continue

        memes.append({
            "title": title_link.get_text(strip=True),
            "image": urljoin(base_url, image["src"]),
            "url": urljoin(base_url, title_link["href"]),
        })

        if len(memes) >= limit:
            break

    return memes
