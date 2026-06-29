"""
Suylios Downloader - gallery-dl Extractor.

Wraps the gallery-dl Python library for downloading image galleries
from hundreds of sites (Danbooru, Gelbooru, e-hentai, Imgur, …).
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from src.extractors.base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class GalleryDLExtractor(BaseExtractor):
    """gallery-dl based extractor for image/gallery sites."""

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        """Return ``True`` when gallery-dl has a matching extractor."""
        try:
            import gallery_dl.extractor
            result = gallery_dl.extractor.find(url)
            return result is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        """Extract gallery metadata without downloading."""
        try:
            import gallery_dl
            from gallery_dl import config as gdl_config
            from gallery_dl.extractor import find as find_extractor
        except ImportError as exc:
            raise ExtractionError(
                "gallery-dl is not installed.  Run: pip install gallery-dl"
            ) from exc

        extractor = find_extractor(url)
        if extractor is None:
            raise ExtractionError(f"gallery-dl cannot handle: {url}")

        # Attempt to gather basic info without downloading everything
        items: list[dict[str, Any]] = []
        title = ""
        try:
            extractor.initialize()
            for msg in extractor:
                msg_type = msg[0] if msg else None
                if msg_type == gallery_dl.extractor.Message.Directory:
                    directory_data = msg[1] if len(msg) > 1 else {}
                    title = directory_data.get("gallery", {}).get("title", "") or \
                            directory_data.get("album", {}).get("title", "") or \
                            directory_data.get("title", "") or \
                            str(directory_data.get("category", "Gallery"))
                elif msg_type == gallery_dl.extractor.Message.Url:
                    file_url = msg[1] if len(msg) > 1 else ""
                    file_meta = msg[2] if len(msg) > 2 else {}
                    items.append({
                        "title": file_meta.get("filename", Path(file_url).stem),
                        "url": file_url,
                        "duration": None,
                        "thumbnail": None,
                    })
                    # Cap preview to 200 items
                    if len(items) >= 200:
                        break
        except Exception as exc:
            logger.warning("gallery-dl metadata scan partial: %s", exc)

        is_playlist = len(items) > 1

        return {
            "title": title or self._title_from_url(url),
            "thumbnail": items[0].get("url") if items else None,
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": "mixed",
                    "quality": "original",
                    "filesize": 0,
                },
            ],
            "is_playlist": is_playlist,
            "playlist_items": items,
        }

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> str:
        """Download gallery contents using gallery-dl."""
        try:
            import gallery_dl
            from gallery_dl import config as gdl_config
            from gallery_dl.job import DownloadJob
        except ImportError as exc:
            raise ExtractionError(
                "gallery-dl is not installed.  Run: pip install gallery-dl"
            ) from exc

        # Configure gallery-dl output
        dest = Path(output_path)
        dest.mkdir(parents=True, exist_ok=True)

        gdl_config.clear()
        gdl_config.set(
            ("extractor",), "base-directory", str(dest),
        )
        gdl_config.set(
            ("extractor",), "directory", ["{category}", "{subcategory|''}"],
        )
        gdl_config.set(
            ("extractor",), "filename", "{filename}.{extension}",
        )
        gdl_config.set(
            ("output",), "mode", "null",
        )

        downloaded_count = 0
        last_file = ""

        try:
            job = DownloadJob(url)

            # Hook into the download process
            original_handle_url = job.handle_url

            def _patched_handle_url(url_tuple):
                nonlocal downloaded_count, last_file
                result = original_handle_url(url_tuple)
                downloaded_count += 1
                # Try to determine filename
                if hasattr(job, 'pathfmt') and job.pathfmt:
                    last_file = getattr(job.pathfmt, 'path', '') or \
                                getattr(job.pathfmt, 'realpath', '')

                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "downloaded_bytes": downloaded_count,
                        "total_bytes": 0,
                        "speed": 0,
                        "eta": 0,
                        "filename": last_file,
                    })
                return result

            job.handle_url = _patched_handle_url
            job.run()

        except Exception as exc:
            raise ExtractionError(f"gallery-dl download failed: {exc}") from exc

        if progress_hook:
            progress_hook({
                "status": "finished",
                "filename": last_file or str(dest),
            })

        return last_file or str(dest)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _title_from_url(url: str) -> str:
        """Derive a title from the URL when metadata is unavailable."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return parts[-1]
        return parsed.hostname or "gallery"
