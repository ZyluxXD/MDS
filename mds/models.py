from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Meme:
    """A meme candidate from ProgrammerHumor"""

    # the meme title, image, source, and view count
    title: str
    image_url: str
    source_url: str
    views: float


@dataclass(frozen=True, slots=True)
class DownloadedMeme:
    """A meme image downloaded in memory for a Slack upload"""

    # the downloaded image and its file information
    meme: Meme
    content: bytes
    filename: str
    content_type: str
