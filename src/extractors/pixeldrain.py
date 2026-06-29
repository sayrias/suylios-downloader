"""
Suylios Downloader - Pixeldrain Extractor.

Downloads single files and file-lists from Pixeldrain using the public
REST API with chunked streaming and retry logic.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from src.extractors.base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_API_BASE = "https://pixeldrain.com/api"
_CHUNK_SIZE = 8192  # 8 KB
_MAX_RETRIES = 4
_RETRY_BACKOFF = 2  # seconds; doubles each attempt

_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?pixeldrain\.com/(?P<kind>[ul])/(?P<id>[A-Za-z0-9_-]+)",
)


class PixeldrainExtractor(BaseExtractor):
    """Pixeldrain file & list extractor."""

    def __init__(self) -> None:
        self._api_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        return bool(_URL_PATTERN.match(url))

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        kind, item_id = self._parse_url(url)

        if kind == "u":
            return self._single_info(item_id)
        return self._list_info(item_id)

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
        kind, item_id = self._parse_url(url)

        if kind == "u":
            return self._download_file(item_id, output_path, progress_hook)

        # List: download every file
        meta = self._api_get(f"/list/{item_id}")
        files = meta.get("files", [])
        if not files:
            raise ExtractionError("Pixeldrain list is empty.")

        list_title = meta.get("title", item_id)
        dest = Path(output_path) / self._safe_filename(list_title)
        dest.mkdir(parents=True, exist_ok=True)

        last_path = ""
        for idx, fmeta in enumerate(files, 1):
            fid = fmeta.get("id", "")
            if not fid:
                continue
            try:
                def _wrapped_hook(data: dict[str, Any]) -> None:
                    data["item_index"] = idx
                    data["item_count"] = len(files)
                    data["item_title"] = fmeta.get("name", fid)
                    if progress_hook:
                        progress_hook(data)

                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "item_index": idx,
                        "item_count": len(files),
                        "item_title": fmeta.get("name", fid),
                        "downloaded_bytes": 0,
                        "total_bytes": fmeta.get("size", 0),
                    })

                last_path = self._download_file(fid, str(dest), _wrapped_hook)
            except ExtractionError as exc:
                logger.warning("Pixeldrain list item %d/%d failed: %s", idx, len(files), exc)
        return last_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str]:
        m = _URL_PATTERN.match(url)
        if not m:
            raise ExtractionError(f"Invalid Pixeldrain URL: {url}")
        return m.group("kind"), m.group("id")

    def _single_info(self, file_id: str) -> dict[str, Any]:
        meta = self._api_get(f"/file/{file_id}/info")
        return {
            "title": meta.get("name", file_id),
            "thumbnail": f"{_API_BASE}/file/{file_id}/thumbnail",
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": Path(meta.get("name", "file")).suffix.lstrip(".") or "bin",
                    "quality": "original",
                    "filesize": meta.get("size", 0),
                },
            ],
            "is_playlist": False,
            "playlist_items": [],
        }

    def _list_info(self, list_id: str) -> dict[str, Any]:
        meta = self._api_get(f"/list/{list_id}")
        items: list[dict[str, Any]] = []
        for f in meta.get("files", []):
            items.append({
                "title": f.get("name", ""),
                "url": f"https://pixeldrain.com/u/{f.get('id', '')}",
                "duration": None,
                "thumbnail": f"{_API_BASE}/file/{f.get('id', '')}/thumbnail",
            })
        return {
            "title": meta.get("title", list_id),
            "thumbnail": items[0]["thumbnail"] if items else "https://www.google.com/s2/favicons?domain=pixeldrain.com&sz=128",
            "duration": None,
            "formats": [],
            "is_playlist": True,
            "playlist_items": items,
        }

    def _download_file(
        self,
        file_id: str,
        dest_dir: str,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
    ) -> str:
        """Download a single Pixeldrain file with retries."""
        # Fetch metadata first for the filename
        meta = self._api_get(f"/file/{file_id}/info")
        filename = self._safe_filename(meta.get("name", file_id))
        total_size = meta.get("size", 0)

        dest_path = Path(dest_dir) / filename
        download_url = f"{_API_BASE}/file/{file_id}"

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                self._stream_download(
                    download_url, dest_path, total_size, progress_hook,
                )
                return str(dest_path)
            except (requests.RequestException, IOError) as exc:
                if attempt == _MAX_RETRIES:
                    raise ExtractionError(
                        f"Failed to download {filename} after {_MAX_RETRIES} attempts: {exc}"
                    ) from exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Pixeldrain retry %d/%d for %s (waiting %ds): %s",
                    attempt, _MAX_RETRIES, file_id, wait, exc,
                )
                time.sleep(wait)

        # Should never reach here, but keeps type checker happy.
        return str(dest_path)

    @staticmethod
    def _stream_download(
        url: str,
        dest: Path,
        total_size: int,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
    ) -> None:
        """Chunked streaming download with progress reporting."""
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) or total_size
            downloaded = 0
            start_ts = time.monotonic()

            with open(dest, "wb") as fp:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    fp.write(chunk)
                    downloaded += len(chunk)

                    if progress_hook:
                        elapsed = time.monotonic() - start_ts
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        eta = int((total - downloaded) / speed) if speed > 0 else 0
                        progress_hook({
                            "status": "downloading",
                            "downloaded_bytes": downloaded,
                            "total_bytes": total,
                            "speed": speed,
                            "eta": eta,
                            "filename": str(dest),
                        })

        if progress_hook:
            progress_hook({
                "status": "finished",
                "filename": str(dest),
            })

    def _api_get(self, endpoint: str) -> dict[str, Any]:
        """GET request to the Pixeldrain API with error handling."""
        if hasattr(self, "_api_cache") and endpoint in self._api_cache:
            return self._api_cache[endpoint]
        url = f"{_API_BASE}{endpoint}"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            res = resp.json()
            if hasattr(self, "_api_cache"):
                self._api_cache[endpoint] = res
            return res
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            raise ExtractionError(
                f"Pixeldrain API error ({status}): {endpoint}"
            ) from exc
        except requests.RequestException as exc:
            raise ExtractionError(f"Pixeldrain network error: {exc}") from exc

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Sanitise a filename for the local filesystem."""
        # Remove characters illegal on Windows
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name.strip(". ") or "download"
