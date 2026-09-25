"""
Suylios Downloader - Mega.nz Extractor.

Downloads single files, folders and collections (albums/sets) from Mega.nz.

Strategy:
  1. PRIMARY  – megadl CLI  (megatools package; supports ALL URL types including
                /collection/).  Installed via: sudo dnf install megatools
  2. FALLBACK – async-mega-py  (Python, works for /file/ and /folder/ only)

Supported URL formats:
  * https://mega.nz/file/HANDLE#KEY        – single encrypted file
  * https://mega.nz/folder/HANDLE#KEY      – shared folder
  * https://mega.nz/collection/HANDLE#KEY  – shared album / collection
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from src.extractors.base_extractor import (
    BaseExtractor,
    ExtractionError,
    ExtractionCancelled,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FILE_PATTERN = re.compile(
    r"https?://(?:www\.)?mega\.(?:nz|co\.nz)/file/"
    r"(?P<handle>[A-Za-z0-9_-]+)#(?P<key>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_FOLDER_PATTERN = re.compile(
    r"https?://(?:www\.)?mega\.(?:nz|co\.nz)/(?:folder|collection)/"
    r"(?P<handle>[A-Za-z0-9_-]+)(?:#(?P<key>[A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)
_MEGA_PATTERN = re.compile(
    r"https?://(?:www\.)?mega\.(?:nz|co\.nz)/",
    re.IGNORECASE,
)

_MAX_RETRIES    = 3
_RETRY_BACKOFF  = 5   # seconds (multiplied per attempt)

# megadl progress line:  "Downloaded X.XX MB/Y.YY MB (ZZ.ZZ%) at ..."
_MEGADL_PROGRESS = re.compile(
    r"Downloaded\s+([\d.]+)\s+(\w+)/([\d.]+)\s+(\w+)\s+\(([\d.]+)%\)",
    re.IGNORECASE,
)

_UNIT_TO_BYTES = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}

# ---------------------------------------------------------------------------
# Helper: async → sync bridge
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an asyncio coroutine safely from any thread."""
    import asyncio
    result    = [None]
    exc_holder = [None]

    def _thread():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            result[0] = new_loop.run_until_complete(coro)
        except Exception as e:
            exc_holder[0] = e
        finally:
            new_loop.close()

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    t.join()
    if exc_holder[0]:
        raise exc_holder[0]
    return result[0]


# ---------------------------------------------------------------------------
# Helper: megadl availability
# ---------------------------------------------------------------------------

def _megadl_path() -> Optional[str]:
    return shutil.which("megadl")


def _to_bytes(value: float, unit: str) -> int:
    return int(value * _UNIT_TO_BYTES.get(unit.upper(), 1))


# ---------------------------------------------------------------------------
# Extractor class
# ---------------------------------------------------------------------------

class MegaExtractor(BaseExtractor):
    """Mega.nz file, folder and collection extractor."""

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        u = url.lower()
        return bool(_MEGA_PATTERN.match(u)) and (
            "/file/" in u or "/folder/" in u or "/collection/" in u
        )

    # ------------------------------------------------------------------
    # Information extraction
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        url = _normalise_url(url)

        # --- megadl path: use it for metadata too if available ---
        if _megadl_path():
            return self._megadl_extract_info(url)

        # --- fallback: async-mega-py ---
        try:
            return _run_async(self._async_extract_info(url))
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(_friendly_error(exc)) from exc

    def _megadl_extract_info(self, url: str) -> dict[str, Any]:
        """Return minimal metadata via megadl --print-names (fast, no download)."""
        is_collection = "/collection/" in url.lower()
        is_folder     = "/folder/" in url.lower() or is_collection
        is_file       = "/file/" in url.lower()

        # For a file we can get info from async-mega-py as well (no megadl needed)
        if is_file and not _megadl_path():
            return _run_async(self._async_extract_info(url))

        # Use megadl --print-names with speed limit 1 KB/s – just grab filenames
        try:
            proc = subprocess.run(
                [_megadl_path(), "--no-progress", "--print-names",
                 "--limit-speed=1", url],
                capture_output=True, text=True, timeout=30,
            )
            raw = (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired:
            raw = ""

        # Parse filenames from output (megadl prints them before actual download)
        filenames = [
            line.strip() for line in raw.splitlines()
            if line.strip()
            and not line.lower().startswith(("error", "warning", "downloaded", "transferring"))
        ]

        if not filenames and is_file:
            # Single file – derive name from URL
            filenames = [url.split("/")[-1].split("#")[0] or "mega_file"]

        title = filenames[0] if filenames else url.split("/")[-1].split("#")[0]

        if is_folder or is_collection:
            items = [{"title": f, "url": url, "duration": None, "thumbnail": None}
                     for f in filenames]
            return {
                "title": title,
                "thumbnail": _mega_favicon(),
                "duration": None,
                "formats": [],
                "is_playlist": True,
                "playlist_items": items,
            }

        ext = Path(filenames[0]).suffix.lstrip(".") if filenames else "bin"
        return {
            "title": title,
            "thumbnail": _mega_favicon(),
            "duration": None,
            "formats": [{"format_id": "original", "ext": ext,
                          "quality": "original", "filesize": 0}],
            "is_playlist": False,
            "playlist_items": [],
        }

    async def _async_extract_info(self, url: str) -> dict[str, Any]:
        from mega.client import MegaNzClient

        if _FILE_PATTERN.match(url):
            return await self._async_file_info(url)
        return await self._async_folder_info(url)

    async def _async_file_info(self, url: str) -> dict[str, Any]:
        from mega.client import MegaNzClient
        handle, key = MegaNzClient.parse_file_url(url)
        async with MegaNzClient() as m:
            await m.login()
            info = await m.get_public_file_info(handle, key)
        name = getattr(info, "name", str(handle))
        size = getattr(info, "size", 0)
        ext  = Path(name).suffix.lstrip(".") or "bin"
        return {
            "title": name,
            "thumbnail": _mega_favicon(),
            "duration": None,
            "formats": [{"format_id": "original", "ext": ext,
                          "quality": "original", "filesize": size}],
            "is_playlist": False,
            "playlist_items": [],
        }

    async def _async_folder_info(self, url: str) -> dict[str, Any]:
        from mega.client import MegaNzClient
        handle, key, _ = MegaNzClient.parse_folder_url(url)
        async with MegaNzClient() as m:
            await m.login()
            fs = await m.get_public_filesystem(handle, key)

        folder_name = getattr(getattr(fs, "root", None), "name", handle) or handle
        items = _collect_files(getattr(fs, "root", None))

        return {
            "title": folder_name,
            "thumbnail": _mega_favicon(),
            "duration": None,
            "formats": [],
            "is_playlist": True,
            "playlist_items": [
                {"title": f["name"], "url": url,
                 "duration": None, "thumbnail": None,
                 "filesize": f["size"]}
                for f in items
            ],
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
        url = _normalise_url(url)
        last_exc: Optional[Exception] = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if _megadl_path():
                    return self._megadl_download(url, output_path, progress_hook)
                return _run_async(
                    self._async_download(url, output_path, progress_hook)
                )
            except (ExtractionError, ExtractionCancelled):
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF * attempt
                    logger.warning(
                        "Mega download attempt %d/%d failed (%s), retrying in %ds…",
                        attempt, _MAX_RETRIES, exc, wait,
                    )
                    time.sleep(wait)

        msg = (
            f"Mega download failed after {_MAX_RETRIES} attempts: {last_exc}\n\n"
            + _install_hint(url)
        )
        raise ExtractionError(msg) from last_exc

    # ------------------------------------------------------------------
    # megadl backend
    # ------------------------------------------------------------------

    def _megadl_download(
        self,
        url: str,
        output_path: str,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
    ) -> str:
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            _megadl_path(),
            "--path", str(out_dir),
            "--no-progress",    # raw output – we parse it ourselves
            "--print-names",    # emit filenames as they complete
            url,
        ]
        logger.info("Mega: running %s", " ".join(cmd))

        if progress_hook:
            progress_hook({"status": "downloading", "filename": str(out_dir)})

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        downloaded_path = str(out_dir)
        last_filename   = ""
        total_bytes     = 0
        done_bytes      = 0

        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            logger.debug("megadl: %s", line)

            # Progress line
            m = _MEGADL_PROGRESS.search(line)
            if m:
                done_val  = float(m.group(1))
                done_unit = m.group(2)
                tot_val   = float(m.group(3))
                tot_unit  = m.group(4)
                pct       = float(m.group(5))
                done_bytes  = _to_bytes(done_val, done_unit)
                total_bytes = _to_bytes(tot_val,  tot_unit)
                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "downloaded_bytes": done_bytes,
                        "total_bytes": total_bytes,
                        "filename": last_filename or str(out_dir),
                    })
                continue

            # Filename line (printed after each file completes)
            if line and not line.lower().startswith(("error", "warning", "downloaded")):
                candidate = out_dir / line
                if candidate.exists():
                    last_filename   = str(candidate)
                    downloaded_path = last_filename
                    if progress_hook:
                        progress_hook({
                            "status": "downloading",
                            "filename": last_filename,
                            "downloaded_bytes": 0,
                            "total_bytes": 0,
                        })

            # Error lines
            if "error" in line.lower():
                logger.warning("megadl output: %s", line)
                if "not found" in line.lower() or "no such" in line.lower():
                    raise ExtractionError(
                        "Bu Mega linki bulunamadı veya süresi dolmuş.\n"
                        "Linkin hâlâ geçerli olduğunu kontrol edin."
                    )
                if "quota" in line.lower() or "transfer" in line.lower():
                    raise ExtractionError(
                        "Mega indirme kotası aşıldı. "
                        "Bir süre bekleyin veya Mega hesabı kullanın."
                    )

        proc.wait()
        rc = proc.returncode

        if rc != 0:
            raise ExtractionError(
                f"megadl başarısız oldu (exit code {rc}). "
                + _install_hint(url)
            )

        if progress_hook:
            progress_hook({"status": "finished", "filename": downloaded_path})

        return downloaded_path

    # ------------------------------------------------------------------
    # async-mega-py backend (file / folder only)
    # ------------------------------------------------------------------

    async def _async_download(
        self,
        url: str,
        output_path: str,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
    ) -> str:
        from mega.client import MegaNzClient

        # async-mega-py only understands /folder/ — rewrite /collection/ if megadl wasn't used
        url = url.replace("/collection/", "/folder/")

        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)

        if progress_hook:
            progress_hook({"status": "downloading", "filename": str(out_dir)})

        if _FILE_PATTERN.match(url):
            handle, key = MegaNzClient.parse_file_url(url)
            async with MegaNzClient() as m:
                await m.login()
                dest = await m.download_public_file(handle, key, out_dir)
            dest_path = str(dest) if dest else str(out_dir)
        else:
            handle, key, _ = MegaNzClient.parse_folder_url(url)
            async with MegaNzClient() as m:
                await m.login()
                fs = await m.get_public_filesystem(handle, key)
                folder_name = getattr(getattr(fs, "root", None), "name", handle) or handle
                dest_folder = out_dir / _safe_filename(folder_name)
                dest_folder.mkdir(parents=True, exist_ok=True)
                await m.download_public_folder(handle, key, dest_folder)
            dest_path = str(dest_folder)

        if progress_hook:
            progress_hook({"status": "finished", "filename": dest_path})

        return dest_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    """Keep the original URL intact — megadl handles /collection/ natively."""
    return url


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip(". ") or "Mega_Download"


def _mega_favicon() -> str:
    return "https://www.google.com/s2/favicons?domain=mega.nz&sz=128"


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if "ENOENT" in msg or "not found" in msg.lower():
        return (
            "Bu Mega linki bulunamadı veya süresi dolmuş. "
            "Linkin hâlâ geçerli olduğunu kontrol edin."
        )
    return f"Mega hatası: {msg}"


def _install_hint(url: str) -> str:
    if "/collection/" in url.lower():
        return (
            "Mega Collection (albüm) indirmek için megatools gereklidir.\n"
            "Tek seferlik kurulum: sudo dnf install megatools\n"
            "Kurulumdan sonra tekrar deneyin."
        )
    return (
        "megatools yüklenirse daha iyi Mega desteği alırsınız: "
        "sudo dnf install megatools"
    )


def _collect_files(node, _prefix: str = "") -> list[dict[str, Any]]:
    """Recursively collect file nodes from async-mega-py FileSystem tree."""
    results: list[dict[str, Any]] = []
    if node is None:
        return results

    node_type = getattr(node, "type", None) or getattr(node, "kind", None)
    children  = getattr(node, "children", []) or []

    if node_type == 0 or (not children and getattr(node, "name", None)):
        results.append({
            "name": getattr(node, "name", ""),
            "size": getattr(node, "size", 0),
        })
    else:
        for child in children:
            results.extend(_collect_files(child))

    return results
