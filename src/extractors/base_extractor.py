"""
Suylios Downloader - Base Extractor.

Abstract base class that every site-specific extractor must implement.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class BaseExtractor(ABC):
    """Contract for all download extractors.

    Each concrete subclass handles one (or more) hosting services and
    exposes a uniform interface for information extraction and file
    download.
    """

    # ------------------------------------------------------------------
    # Capability check
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def can_handle(url: str) -> bool:
        """Return ``True`` when this extractor can process *url*.

        This must be a fast, synchronous check (e.g. regex on the
        hostname) – no network I/O allowed.
        """
        ...

    # ------------------------------------------------------------------
    # Information extraction
    # ------------------------------------------------------------------

    @abstractmethod
    def extract_info(self, url: str) -> dict[str, Any]:
        """Fetch metadata for *url* **without** downloading.

        Returns a dictionary with at least the following keys:

        * ``title``          – human-readable title (``str``)
        * ``thumbnail``      – URL of a thumbnail image (``str | None``)
        * ``duration``       – duration in seconds (``int | None``)
        * ``formats``        – list of available formats, each a dict with
          ``format_id``, ``ext``, ``quality``, ``filesize``
        * ``is_playlist``    – ``True`` when the URL points to a playlist / album
        * ``playlist_items`` – list of per-item info dicts (same schema)

        Raises :class:`ExtractionError` on failure.
        """
        ...

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    @abstractmethod
    def download(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook=None,
        start_time: str = "",
        end_time: str = "",
        embed_metadata: bool = True,
        download_subtitles: bool = False,
        subtitle_langs: str = "tr,en",
    ) -> str:
        """Download the media from *url* into *output_path*."""
        ...


class ExtractionError(Exception):
    """Raised when extraction or download fails."""


class ExtractionCancelled(Exception):
    """Raised when extraction or download is cancelled by user."""
