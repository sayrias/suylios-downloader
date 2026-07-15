"""
Suylios Downloader - Cyberdrop-DL Extractor (v10+).

Handles bulk asynchronous downloads from file hosts and galleries such as:
Cyberdrop, Bunkr, Gofile, Pixeldrain, Erome, Fapello, Coomer, Kemono, ThotHub,
Rule34, Nudostar, SimpCity, LeakedModels, and 100+ other supported platforms.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional

from src.extractors.base_extractor import BaseExtractor, ExtractionError, ExtractionCancelled
from src.extractors.gallery_ext import _register_proc, _unregister_proc

logger = logging.getLogger("suylios.extractor.cyberdrop")

# Domains explicitly supported by cyberdrop-dl
CDL_SUPPORTED_DOMAINS = {
    "bunkr", "bunkrr", "celebforum", "coomer", "cyberdrop", "cyberfile",
    "e-hentai", "erome", "fapello", "f95zone", "gofile", "hotpic", "ibb.co",
    "imageban", "imgbox", "imgur", "img.kiwi", "jpg.church", "jpg.homes",
    "jpg.fish", "jpg.fishing", "jpg.pet", "jpeg.pet", "jpg1.su", "jpg2.su",
    "jpg3.su", "jpg4.su", "host.church", "kemono", "leakedmodels", "mediafire",
    "nudostar.com", "nudostar.tv", "omegascans", "pimpandhost", "pixeldrain",
    "postimg", "realbooru", "reddit", "redd.it", "redgifs", "rule34.xxx",
    "rule34.xyz", "rule34vault", "saint", "scrolller", "simpcity",
    "socialmediagirls", "toonily", "xbunker", "xbunkr", "thothub", "catbox",
    "fileditch", "1fichier", "4chan", "8muses", "saint", "cyberfile.me",
}


class CyberdropDLExtractor(BaseExtractor):
    """Bulk async downloader wrapper for cyberdrop-dl CLI / library."""

    def __init__(self) -> None:
        self._task_quality = "best"
        self._task_url = ""
        self._site_settings: dict[str, Any] = {}

    @staticmethod
    def can_handle(url: str) -> bool:
        """Return True when the URL domain is supported by cyberdrop-dl."""
        if not url:
            return False
        url_lower = url.lower()
        for domain in CDL_SUPPORTED_DOMAINS:
            if domain in url_lower:
                return True
        return False

    def _get_cdl_bin(self) -> str:
        """Locate the cyberdrop-dl executable or fallback to python module."""
        # First check virtual environment or path
        cdl_exe = shutil.which("cyberdrop-dl")
        if cdl_exe and os.path.exists(cdl_exe):
            return cdl_exe

        # Check in the same directory as sys.executable
        py_dir = os.path.dirname(sys.executable)
        for name in ("cyberdrop-dl.exe", "cyberdrop-dl"):
            candidate = os.path.join(py_dir, name)
            if os.path.exists(candidate):
                return candidate

        return ""

    def extract_info(self, url: str) -> dict[str, Any]:
        """Verify URL compatibility and provide metadata."""
        # cyberdrop-dl is a bulk downloader without a dry-run JSON metadata API,
        # so we return structured gallery/album metadata.
        url_lower = url.lower()
        title = url.split("/")[-1].split("?")[0] or "Cyberdrop Gallery"
        if not title or len(title) < 3:
            title = url

        return {
            "title": f"[CDL Albüm/Galeri] {title}",
            "thumbnail": "",
            "duration": None,
            "is_playlist": True,
            "formats": [
                {
                    "format_id": "best",
                    "ext": "zip/files",
                    "quality": "Orijinal Kalite (cyberdrop-dl)",
                    "filesize": None,
                }
            ],
        }

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
        keep_original: bool = False,
        **kwargs: Any,
    ) -> str:
        """Download items from the gallery using cyberdrop-dl."""
        from src.config import config
        if config.is_portable():
            from src.config import _get_app_root
            cdl_appdata = str(_get_app_root() / "data" / "cdl_data")
        else:
            cdl_appdata = os.path.join(os.path.expanduser("~"), ".suylios_cdl_data")
        os.makedirs(cdl_appdata, exist_ok=True)

        cdl_bin = self._get_cdl_bin()
        base_flags = [
            "--download",
            "--no-ui",
            "--ignore-history",
            "--appdata-folder",
            cdl_appdata,
            "-d",
            output_path,
            url,
        ]
        from src.config import config
        proxy_val = getattr(self, "_app_proxy", "") or config.get("proxy", "")
        if proxy_val:
            base_flags.extend(["--proxy", proxy_val])
        if cdl_bin:
            cmd = [cdl_bin] + base_flags
        else:
            cmd = [sys.executable, "-m", "cyberdrop_dl"] + base_flags

        logger.info("Executing cyberdrop-dl: %s", " ".join(cmd))

        if progress_hook:
            progress_hook(
                {
                    "status": "downloading",
                    "filename": f"[Cyberdrop-DL] {url.split('/')[-1]}",
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                }
            )

        try:
            # cwd olarak output_path vermek Windows'ta klasörü kilitlediği (WinError 32) için temp dizini veriyoruz
            safe_cwd = tempfile.gettempdir()
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=safe_cwd,
            )
            _uid = getattr(self, "_uid", "cdl") or "cdl"
            _register_proc(_uid, process)

            files_before = set(os.listdir(output_path)) if os.path.exists(output_path) else set()
            scrape_failures = 0
            try:
                while True:
                    if cancel_event.is_set():
                        try:
                            process.terminate()
                        except Exception:
                            pass
                        raise ExtractionCancelled("cyberdrop-dl download was cancelled")
                    line = process.stdout.readline() if process.stdout else ""
                    if not line and process.poll() is not None:
                        break
                    if line:
                        line_str = line.strip()
                        logger.debug("CDL: %s", line_str)
                        if "Scrape Failures" in line_str or "401 HTTP Status" in line_str or "404 HTTP Status" in line_str or "PermissionError" in line_str:
                            scrape_failures += 1
                        if progress_hook and ("downloading" in line_str.lower() or "%" in line_str or "file" in line_str.lower()):
                            progress_hook(
                                {
                                    "status": "downloading",
                                    "item_title": line_str[:80],
                                }
                            )

                if cancel_event.is_set():
                    raise ExtractionCancelled("cyberdrop-dl download was cancelled")

                ret = process.poll()
                if ret != 0:
                    logger.warning("cyberdrop-dl finished with return code %s", ret)
            finally:
                _unregister_proc(_uid, process)

            files_after = set(os.listdir(output_path)) if os.path.exists(output_path) else set()
            new_files = [f for f in (files_after - files_before) if f not in ("AppData", ".suylios_cdl_data")]

            if not new_files or (scrape_failures > 0 and len(new_files) == 0):
                raise ExtractionError("cyberdrop-dl downloaded 0 files (scrape, 401 or permission error). Falling back to GalleryDL/YtDlp...")

            if progress_hook:
                progress_hook({"status": "finished", "filename": str(output_path)})

            first_new = os.path.join(output_path, new_files[0])
            return first_new

        except ExtractionCancelled:
            raise
        except Exception as exc:
            logger.error("cyberdrop-dl download error: %s", exc, exc_info=True)
            raise ExtractionError(f"cyberdrop-dl failed: {exc}") from exc
