import logging
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


def view_count(views: str) -> float:
    """convert displayed view counts like "1.2K" to sortable numbers"""
    match = re.search(r"([\d,.]+)\s*([KMB])?", views.upper())
    if not match:
        return 0

    number = float(match.group(1).replace(",", ""))
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return number * multiplier.get(match.group(2), 1)


def get_memes(limit=25, memes_extension="/hot"):
    """scrape memes from programmerhumor.io"""
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
            "views": post.select_one("span.post-views").get_text(strip=True) if post.select_one(
                "span.post-views") else "N/A",
        })

    memes.sort(key=lambda meme: view_count(meme["views"]), reverse=True)
    logger.info("Scraped %d eligible meme(s) from ProgrammerHumor", len(memes))
    return memes[:limit]


def download_memes(
        memes: list[dict[str, str]],
) -> tuple[list[dict[str, str | bytes]], list[dict[str, str]]]:
    """download and prepare meme images for the Slack upload"""
    file_uploads = []
    valid_memes = []

    for index, meme in enumerate(memes, start=1):
        try:
            response = requests.get(meme["image"], timeout=15)
            response.raise_for_status()
            file_uploads.append({
                "file": response.content,
                "filename": f"meme-{index}.jpg",
                "title": meme["title"],
                "alt_txt": meme["title"],
            })
            valid_memes.append(meme)
        except requests.RequestException as error:
            logger.warning("Skipping meme image %r: %s", meme["title"], error)

    return file_uploads, valid_memes
