import logging
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlencode, urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

MEME_SOURCE_EXTENSIONS = {
    "home": "/",
    "hot": "/hot",
    "random": "/random",
}
SEARCH_SOURCES = frozenset({"query", "search"})
MEME_CARD_SELECTORS = (
    "div.post",
    "div.search-results-container > div",
    "div.hot-feed > div",
    "div.home-feed > div",
    "div.random-feed > div",
)
IMAGE_ATTRIBUTES = ("data-src", "data-lazy-src", "data-original", "src")

session = requests.Session()
retry = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
)
session.mount("https://", HTTPAdapter(max_retries=retry))


def source_extension(source: str, query: str | None = None) -> str:
    """transform a ProgrammerHumor source into a site route"""
    normalized_source = source.strip().lower()
    if normalized_source in MEME_SOURCE_EXTENSIONS:
        if query:
            raise ValueError(f"The {normalized_source} source does not accept a search query")
        return MEME_SOURCE_EXTENSIONS[normalized_source]

    if normalized_source in SEARCH_SOURCES:
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValueError("A search topic is required for the query source")
        return f"/search?{urlencode({'q': normalized_query})}"

    supported_sources = ", ".join((*MEME_SOURCE_EXTENSIONS, "search"))
    raise ValueError(f"Unknown meme source {source!r}; choose one of: {supported_sources}")


def view_count(views: str) -> float:
    """convert displayed view counts like "1.2K" to sortable numbers"""
    match = re.search(r"([\d,.]+)\s*([KMB])?", views.upper())
    if not match:
        return 0

    number = float(match.group(1).replace(",", ""))
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return number * multiplier.get(match.group(2), 1)


def _image_url(post) -> str | None:
    image = post.select_one("a.post-image-link img, img")
    if image is None:
        return None

    for attribute in IMAGE_ATTRIBUTES:
        image_url = image.get(attribute)
        if image_url:
            return image_url

    srcset = image.get("srcset")
    if srcset:
        return srcset.split(",", maxsplit=1)[0].strip().split(maxsplit=1)[0]
    return None


def _views_text(post) -> str:
    view_element = post.select_one("span.post-views, [class*='views']")
    if view_element:
        return view_element.get_text(" ", strip=True)

    match = re.search(
        r"([\d,.]+\s*[KMB])\s+views\b",
        post.get_text(" ", strip=True),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else "N/A"


def get_memes(limit=25, memes_extension="/hot"):
    """scrape memes from programmerhumor.io"""
    base_url = "https://programmerhumor.io"
    memes_url = f"{base_url}{memes_extension}"
    memes = []

    try:
        response = session.get(memes_url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as error:
        logger.error("Could not fetch memes from %s: %s", memes_url, error)
        return []

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    posts = soup.select(", ".join(MEME_CARD_SELECTORS))
    if not posts:
        posts = soup.select("article")

    for post in posts:
        title_link = post.select_one("a.post-title-link, h1 a, h2 a, h3 a")
        image_url = _image_url(post)
        post_url = title_link.get("href") if title_link else None

        if not title_link or not post_url or not image_url or post_url.startswith("/go/"):
            continue

        memes.append({
            "title": title_link.get_text(strip=True),
            "image": urljoin(base_url, image_url),
            "url": urljoin(base_url, post_url),
            "views": _views_text(post),
        })

    memes.sort(key=lambda meme: view_count(meme["views"]), reverse=True)
    logger.info("Parsed %d eligible meme(s) from ProgrammerHumor", len(memes))
    if not memes:
        logger.warning("No eligible meme cards found at %s", memes_url)
    return memes[:limit]


def download_memes(
        memes: list[dict[str, str]],
) -> tuple[list[dict[str, str | bytes]], list[dict[str, str]]]:
    """download and prepare meme images for the Slack upload"""
    file_uploads = []
    valid_memes = []

    for index, meme in enumerate(memes, start=1):
        try:
            response = session.get(meme["image"], timeout=15)
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
