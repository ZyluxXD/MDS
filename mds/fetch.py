from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Sequence
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import DownloadedMeme, Meme

logger = logging.getLogger(__name__)

# the ProgrammerHumor site source extensions for the supported meme feeds
MEME_SOURCE_EXTENSIONS = {
    "home": "/",
    "hot": "/hot",
    "random": "/random",
}
# the CSS selectors for meme cards
MEME_CARD_SELECTORS = (
    "div.post",
    "div.search-results-container > div",
    "div.hot-feed > div",
    "div.home-feed > div",
    "div.random-feed > div",
)
# the HTML attributes that may contain the meme image URL
IMAGE_ATTRIBUTES = ("data-src", "data-lazy-src", "data-original", "src")
# the maximum allowed meme download size, in bytes
DOWNLOAD_LIMIT_BYTES = 10 * 1024 * 1024
# the maximum allowed meme download timeout, in seconds
DOWNLOAD_TIMEOUT_SECONDS = 15
# the chunk size for streaming meme downloads, in bytes
DOWNLOAD_CHUNK_SIZE = 64 * 1024
# mime types to file extensions for downloaded meme images
MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/svg+xml": ".svg",
}


def create_http_session() -> requests.Session:
    """Create a short-lived HTTP session with a retry policy"""
    # create the retry policy
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    # create the session, mount the retry policy for HTTPS requests, and return the session object
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def source_extension(source: str, query: str | None = None) -> str:
    """Transform a ProgrammerHumor source into a site route"""
    # normalize the source string and check if it is a known extension
    normalized_source = source.strip().lower()
    if normalized_source in MEME_SOURCE_EXTENSIONS:
        if query:
            raise ValueError(f"The {normalized_source} source does not accept a search query")
        return MEME_SOURCE_EXTENSIONS[normalized_source]
    # check if the source is a search query and validate the query string
    if normalized_source in frozenset({"query", "search"}):
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValueError("A search topic is required for the query source")
        return f"/search?{urlencode({'q': normalized_query})}"
    # if the source is unknown, raise a ValueError with the list of supported sources
    supported_sources = ", ".join((*MEME_SOURCE_EXTENSIONS, "search"))
    raise ValueError(f"Unknown meme source {source!r}; choose one of: {supported_sources}")


def view_count(views: str) -> float:
    """Convert displayed view counts such as `1.2K` to sortable numbers"""
    # find the view count and optional multiplier suffix (K, M, B) using a regex
    match = re.search(r"([\d,.]+)\s*([KMB])?", views.upper())
    # return 0 views if a match was not found
    if not match:
        return 0.0
    # parse the number and apply the multiplier if present, returning 0 views if there is a parsing error
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return number * multiplier.get(match.group(2), 1)


def _image_url(post) -> str | None:
    """Extract the URL of the image from a post"""
    # search for the image element
    image = post.select_one("a.post-image-link img, img")
    # return None if there is no image element found
    if image is None:
        return None
    # check the image attributes for the image URL and return the first one found
    for attribute in IMAGE_ATTRIBUTES:
        image_url = image.get(attribute)
        if image_url:
            return image_url
    srcset = image.get("srcset")
    if srcset:
        return srcset.split(",", maxsplit=1)[0].strip().split(maxsplit=1)[0]
    return None


def _views_text(post) -> str:
    """Extract the text of the view count from a post"""
    # get the view element from the post HTML
    view_element = post.select_one("span.post-views, [class*='views']")
    # return the text of the view element if it exists, otherwise search for a view count pattern in the post text
    if view_element:
        return view_element.get_text(" ", strip=True)
    match = re.search(
        r"([\d,.]+\s*[KMB])\s+views\b",
        post.get_text(" ", strip=True),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else "N/A"


def get_memes(limit: int = 25, memes_extension: str = "/hot") -> list[Meme]:
    """Scrape and rank meme candidates from ProgrammerHumor"""
    base_url = "https://programmerhumor.io"
    memes_url = f"{base_url}{memes_extension}"
    memes: list[Meme] = []
    session = create_http_session()
    # fetch the memes page
    try:
        response = session.get(memes_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        try:
            response.raise_for_status()
            html = response.text
        finally:
            response.close()
    except requests.RequestException as error:
        logger.error("Could not fetch memes from %s: %s", memes_url, error)
        return []
    finally:
        session.close()

    soup = BeautifulSoup(html, "html.parser")
    # extract the meme posts, title links, image urls, and view counts
    posts = soup.select("main article") or soup.select(", ".join(MEME_CARD_SELECTORS))
    for post in posts:
        title_link = post.select_one("a.post-title-link, h1 a, h2 a, h3 a")
        image_url = _image_url(post)
        post_url = title_link.get("href") if title_link else None
        if not title_link or not post_url or not image_url or post_url.startswith("/go/"):
            continue
        views_text = _views_text(post)
        memes.append(
            Meme(
                title=title_link.get_text(strip=True),
                image_url=urljoin(base_url, image_url),
                source_url=urljoin(base_url, post_url),
                views=view_count(views_text),
            )
        )
    # sort the memes in descending order by view count
    memes.sort(key=lambda meme: meme.views, reverse=True)
    logger.info(
        "Parsed %d eligible meme(s) from ProgrammerHumor from source %s",
        len(memes),
        memes_extension,
    )
    if not memes:
        logger.warning("No eligible meme cards found at %s", memes_url)
    return memes[:limit]


def fetch_meme_candidates(
        meme_extensions: Sequence[str],
        fetch_limit: int,
) -> list[Meme]:
    """Fetch ranked feeds, interleave them, and deduplicate source URLs"""
    logger.info("Fetching memes from ProgrammerHumor...")
    # limit each source to the requested candidate count
    # fetch memes
    fetched = [
        get_memes(limit=fetch_limit, memes_extension=extension)
        for extension in meme_extensions
    ]
    # interleave the feeds and deduplicate by source URL
    # the reason the interleaving is done is because since the /home and /hot feeds are mixed & sorted by view count
    # you will always see memes from /hot feed, so you will never see memes from /home feed
    # so instead this interleaves them from the top of each feed, so you will see memes from both feeds
    interleaved: list[Meme] = []
    seen_urls: set[str] = set()
    for rank in range(max((len(feed) for feed in fetched), default=0)):
        for feed in fetched:
            if rank >= len(feed):
                continue
            meme = feed[rank]
            if meme.source_url in seen_urls:
                continue
            seen_urls.add(meme.source_url)
            interleaved.append(meme)
    if len(interleaved) > fetch_limit:
        interleaved = interleaved[:fetch_limit]
    logger.info("Scraped %d eligible meme(s) from ProgrammerHumor", len(interleaved))
    return interleaved


def _download_one(index: int, meme: Meme) -> DownloadedMeme | None:
    """Download a single meme image"""
    session = create_http_session()
    response = None
    try:
        # get the image URL and download it with a timeout and streaming
        response = session.get(
            meme.image_url,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            stream=True,
        )
        response.raise_for_status()
        # get the content type and check if the response is an image
        raw_content_type = response.headers.get("Content-Type", "")
        content_type = raw_content_type.split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise ValueError(f"unexpected content type {raw_content_type!r}")

        # check if the declared image size exceeds the download limit
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                # ignore an invalid content length value
                logger.debug("Ignoring invalid Content-Length for %s", meme.image_url)
            else:
                if declared_length > DOWNLOAD_LIMIT_BYTES:
                    raise ValueError("image exceeds the 10 MiB download limit")

        content = bytearray()
        # stream the image in chunks and enforce the download limit
        for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > DOWNLOAD_LIMIT_BYTES:
                raise ValueError("image exceeds the 10 MiB download limit")

        # check if the image response was empty
        if not content:
            raise ValueError("image response was empty")
        # create and return the downloaded meme object
        extension = MIME_EXTENSIONS.get(content_type, ".img")
        return DownloadedMeme(
            meme=meme,
            content=bytes(content),
            filename=f"meme-{index}{extension}",
            content_type=content_type,
        )
    except (requests.RequestException, ValueError) as error:
        # skip the meme if its image could not be downloaded
        logger.warning("Skipping meme image %r: %s", meme.title, error)
        return None
    finally:
        # close the response and session
        if response is not None:
            response.close()
        session.close()


def download_memes(memes: Sequence[Meme]) -> list[DownloadedMeme]:
    """Download meme images concurrently"""
    logger.info("Downloading selected memes...")
    if not memes:
        # quite unfortunate
        logger.info("Downloaded 0 meme images")
        return []
    # download meme images in parallel
    worker_count = min(4, len(memes))
    downloaded: dict[int, DownloadedMeme] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="meme-download") as executor:
        futures = {
            executor.submit(_download_one, index, meme): index
            for index, meme in enumerate(memes, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except Exception as error:
                # log and skip unexpected download errors
                logger.exception(
                    "Meme download worker failed for candidate %d: %s",
                    index,
                    error,
                )
                continue
            if result is not None:
                # save successful downloads by their original index
                downloaded[index] = result
    # return the downloaded memes in their original order
    result = [downloaded[index] for index in sorted(downloaded)]
    logger.info("Downloaded %d meme images", len(result))
    return result
