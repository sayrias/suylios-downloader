"""
Suylios Downloader - Gofile Extractor.

Downloads files from gofile.io with a multi-layer fallback strategy:

1. Create a guest account token via the API.
2. Try the official API with that token.
3. If ``error-notPremium`` is returned, fall back to web scraping:
   extract the ``websiteToken`` (wt) from the page source and replay the
   content API with browser-like headers.
4. Download files with proper cookie / token headers.
5. Track already-downloaded files to support incremental sync.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from src.extractors.base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8192
_MAX_RETRIES = 4
_RETRY_BACKOFF = 2

_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?gofile\.io/(?:d/|/?c=)(?P<id>[A-Za-z0-9_-]+)",
)

# Known API endpoints
_API_BASE = "https://api.gofile.io"
_WEBSITE = "https://gofile.io"


class GofileExtractor(BaseExtractor):
    """Gofile downloader with API + web-scraping fallback."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._wt: Optional[str] = None  # websiteToken
        self._tree_cache: dict[str, dict[str, Any]] = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": _WEBSITE,
            "Referer": f"{_WEBSITE}/",
        })

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
        content_id = self._parse_url(url)
        tree = self._fetch_content_tree(content_id)
        return self._normalise(tree, content_id)

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
        content_id = self._parse_url(url)
        tree = self._fetch_content_tree(content_id)

        folder_name = self._safe_filename(
            tree.get("name", content_id),
        )
        dest = Path(output_path) / folder_name
        dest.mkdir(parents=True, exist_ok=True)

        # Incremental sync tracker
        tracker_path = dest / f".gofile_tracker_{content_id}.json"
        downloaded_set = self._load_tracker(tracker_path)

        files = self._collect_files(tree)
        if not files:
            raise ExtractionError("No downloadable files found in Gofile content.")

        last_path = ""
        for idx, finfo in enumerate(files, 1):
            fid = finfo.get("id", "")
            fname = self._safe_filename(finfo.get("name", fid))
            if fid in downloaded_set:
                logger.debug("Skipping already-downloaded file: %s", fname)
                continue

            file_url = finfo.get("link", "")
            if not file_url:
                logger.warning("No download link for file %s", fname)
                continue

            file_dest = dest / fname
            file_size = finfo.get("size", 0)

            try:
                def _wrapped_hook(data: dict[str, Any]) -> None:
                    data["item_index"] = idx
                    data["item_count"] = len(files)
                    data["item_title"] = fname
                    if progress_hook:
                        progress_hook(data)

                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "filename": str(file_dest),
                        "item_index": idx,
                        "item_count": len(files),
                        "item_title": fname,
                        "downloaded_bytes": 0,
                        "total_bytes": file_size,
                    })

                self._download_file(file_url, file_dest, file_size, _wrapped_hook)
                downloaded_set.add(fid)
                self._save_tracker(tracker_path, downloaded_set)
                last_path = str(file_dest)
            except Exception as exc:
                logger.error(
                    "Gofile file %d/%d (%s) failed: %s",
                    idx, len(files), fname, exc,
                )

        if progress_hook:
            progress_hook({"status": "finished", "filename": last_path or str(dest)})

        return last_path or str(dest)

    # ------------------------------------------------------------------
    # Content tree fetching (API → web-scrape fallback)
    # ------------------------------------------------------------------

    def _fetch_content_tree(self, content_id: str) -> dict[str, Any]:
        """Fetch the content tree."""
        if hasattr(self, "_tree_cache") and content_id in self._tree_cache:
            return self._tree_cache[content_id]

        # Step 1: obtain guest token
        self._ensure_token()

        # Step 2: try official API
        try:
            tree = self._api_get_contents(content_id)
            if tree is not None:
                if hasattr(self, "_tree_cache"):
                    self._tree_cache[content_id] = tree
                return tree
        except _NotPremiumError:
            logger.info("Gofile API returned notPremium – switching to fallback.")
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower() or "connect" in str(exc).lower():
                raise ExtractionError(f"Connection timed out: {exc}")
            logger.info("Gofile API failed – switching to fallback.")

        # Step 3: web-scraping fallback
        res = self._fallback_fetch(content_id)
        if hasattr(self, "_tree_cache"):
            self._tree_cache[content_id] = res
        return res

    def _ensure_token(self) -> None:
        """Create a guest account and store the token."""
        if self._token:
            return
        site_cfg = getattr(self, "_site_settings", {}).get("gofile", {})
        cookies_val = site_cfg.get("cookies", "").strip()
        if cookies_val:
            if Path(cookies_val).is_file():
                try:
                    txt = Path(cookies_val).read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'accountToken\s+([A-Za-z0-9_-]+)', txt) or re.search(r'token\s+([A-Za-z0-9_-]+)', txt)
                    if m:
                        self._token = m.group(1)
                except Exception:
                    pass
            elif len(cookies_val) > 10:
                self._token = cookies_val

            if self._token:
                self._session.headers["Authorization"] = f"Bearer {self._token}"
                self._session.cookies.set("accountToken", self._token, domain=".gofile.io", path="/")
                return

        cache_file = Path.home() / ".gofile_token"
        if not self._token and cache_file.is_file():
            try:
                cached = cache_file.read_text(encoding="utf-8").strip()
                if len(cached) > 10:
                    self._token = cached
                    self._session.headers["Authorization"] = f"Bearer {self._token}"
                    self._session.cookies.set("accountToken", self._token, domain=".gofile.io", path="/")
                    return
            except Exception:
                pass

        try:
            resp = self._session.post(
                f"{_API_BASE}/accounts",
                timeout=3,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                self._token = data["data"]["token"]
                self._session.headers["Authorization"] = f"Bearer {self._token}"
                self._session.cookies.set(
                    "accountToken", self._token, domain=".gofile.io",
                )
                try:
                    cache_file.write_text(self._token, encoding="utf-8")
                except Exception:
                    pass
                logger.debug("Gofile guest token obtained.")
            else:
                logger.warning("Gofile guest account creation returned: %s", data)
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise ExtractionError(f"Connection timed out: {exc}")
            logger.warning("Failed to create Gofile guest account: %s", exc)

    def _generate_x_wt(self) -> str:
        """Generate X-Website-Token header value required by Gofile API."""
        import hashlib
        token = self._token or ""
        ua = self._session.headers.get("User-Agent", "Mozilla/5.0")
        lang = "en-US"
        tb = str(int(time.time() / 14400))
        salt = "9844d94d963d30"
        raw = f"{ua}::{lang}::{token}::{tb}::{salt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _api_get_contents(self, content_id: str) -> Optional[dict[str, Any]]:
        """Call the official contents API endpoint."""
        wt = self._wt or self._fetch_website_token()
        params: dict[str, str] = {"cache": "true"}
        if wt:
            params["wt"] = wt

        headers = {
            "Authorization": f"Bearer {self._token}" if self._token else "",
            "X-Website-Token": self._generate_x_wt(),
        }

        url = f"{_API_BASE}/contents/{content_id}"
        resp = self._session.get(url, params=params, headers=headers, timeout=3)
        data = resp.json()

        status = data.get("status", "")
        if status == "ok":
            return data.get("data", {})

        # Check for known error states
        error_msg = data.get("status", "") or data.get("message", "")
        if "notPremium" in str(error_msg) or "error-notPremium" in str(error_msg):
            raise _NotPremiumError()
        if "notFound" in str(error_msg):
            raise ExtractionError(f"Gofile content not found: {content_id}")

        raise ExtractionError(f"Gofile API error: {error_msg}")

    # ------------------------------------------------------------------
    # Web-scraping fallback
    # ------------------------------------------------------------------

    def _fallback_fetch(self, content_id: str) -> dict[str, Any]:
        """Scrape the Gofile page and reconstruct the content tree."""
        wt = self._fetch_website_token()
        if not wt:
            raise ExtractionError(
                "Could not extract websiteToken from Gofile page."
            )

        # Try the API one more time with the freshly-scraped wt
        # using curl_cffi for TLS fingerprint bypass if available
        try:
            tree = self._api_with_curl_cffi(content_id, wt)
            if tree is not None:
                return tree
        except Exception as exc:
            logger.debug("curl_cffi attempt failed: %s", exc)

        # Last resort: plain requests with the wt
        self._wt = wt
        try:
            result = self._api_get_contents(content_id)
            if result is not None:
                return result
        except _NotPremiumError:
            pass

        raise ExtractionError(
            "Gofile requires a premium account and web-scraping fallback "
            "could not bypass the restriction.  Consider using a premium "
            "API key in the settings."
        )

    def _fetch_website_token(self) -> Optional[str]:
        """Fetch the Gofile homepage and extract the ``websiteToken``
        from embedded JavaScript."""
        if self._wt:
            return self._wt
        try:
            # The wt is typically in a JS bundle loaded by the page
            resp = self._session.get(f"{_WEBSITE}/", timeout=3)
            html = resp.text

            # Pattern 1: look for wt / websiteToken in scripts
            for pat in (
                r'websiteToken\s*[=:]\s*["\']([a-zA-Z0-9]+)["\']',
                r'wt\s*[=:]\s*["\']([a-zA-Z0-9]+)["\']',
                r'fetchData\s*\([^)]*["\']([a-zA-Z0-9]{26,})["\']',
            ):
                m = re.search(pat, html)
                if m:
                    self._wt = m.group(1)
                    logger.debug("Extracted websiteToken: %s…", self._wt[:8])
                    return self._wt

            # Pattern 2: look in linked JS bundles
            for script_src in re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', html):
                if script_src.startswith("/"):
                    script_src = f"{_WEBSITE}{script_src}"
                try:
                    js_resp = self._session.get(script_src, timeout=3)
                    for pat in (
                        r'websiteToken\s*[=:]\s*["\']([a-zA-Z0-9]+)["\']',
                        r'wt\s*[=:]\s*["\']([a-zA-Z0-9]+)["\']',
                    ):
                        m = re.search(pat, js_resp.text)
                        if m:
                            self._wt = m.group(1)
                            logger.debug("Extracted websiteToken from JS: %s…", self._wt[:8])
                            return self._wt
                except Exception:
                    continue

        except Exception as exc:
            logger.warning("Failed to fetch Gofile page for wt: %s", exc)

        return None

    def _api_with_curl_cffi(
        self, content_id: str, wt: str,
    ) -> Optional[dict[str, Any]]:
        """Try fetching via ``curl_cffi`` for TLS fingerprint spoofing."""
        try:
            from curl_cffi.requests import Session as CffiSession
        except ImportError:
            return None

        with CffiSession(impersonate="chrome") as s:
            headers = {
                "User-Agent": self._session.headers.get("User-Agent", "Mozilla/5.0"),
                "Accept-Language": "en-US",
                "Authorization": f"Bearer {self._token}" if self._token else "",
                "X-Website-Token": self._generate_x_wt(),
                "Origin": _WEBSITE,
                "Referer": f"{_WEBSITE}/",
            }
            cookies = {}
            if self._token:
                cookies["accountToken"] = self._token

            resp = s.get(
                f"{_API_BASE}/contents/{content_id}",
                params={"wt": wt, "cache": "true"},
                headers=headers,
                cookies=cookies,
                timeout=3,
            )
            data = resp.json()
            if data.get("status") == "ok":
                return data.get("data", {})

        return None

    # ------------------------------------------------------------------
    # File tree traversal
    # ------------------------------------------------------------------

    def _collect_files(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        """Recursively collect downloadable file entries from the tree."""
        files: list[dict[str, Any]] = []
        node_type = node.get("type", "")

        if node_type == "file":
            files.append(node)
        elif node_type in ("folder", ""):
            children = node.get("children", {})
            if isinstance(children, dict):
                for child in children.values():
                    files.extend(self._collect_files(child))
            elif isinstance(children, list):
                for child in children:
                    files.extend(self._collect_files(child))
            # Also look in 'contents' key
            contents = node.get("contents", {})
            if isinstance(contents, dict):
                for child in contents.values():
                    files.extend(self._collect_files(child))

        return files

    # ------------------------------------------------------------------
    # File download
    # ------------------------------------------------------------------

    def _download_file(
        self,
        url: str,
        dest: Path,
        total_size: int,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
    ) -> None:
        """Download a single file with retries and progress."""
        headers = {
            "User-Agent": self._session.headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
            "Accept": "*/*",
            "Referer": "https://gofile.io/",
            "Origin": "https://gofile.io",
        }
        cookies = {}
        if self._token:
            cookies["accountToken"] = self._token

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                # Try curl_cffi first to bypass Cloudflare/CDN blocks
                try:
                    from curl_cffi.requests import Session as CffiSession
                    session_ctx = CffiSession(impersonate="chrome")
                except ImportError:
                    session_ctx = self._session

                if hasattr(session_ctx, "impersonate"):
                    resp = session_ctx.get(url, stream=True, headers=headers, cookies=cookies, timeout=(3, 30))
                else:
                    resp = session_ctx.get(url, stream=True, headers=headers, cookies=cookies, timeout=(3, 30))

                if resp.status_code == 429:
                    raise ExtractionError("error-rateLimit: Sunucu IP adresinize hız sınırı uyguladı.")
                if resp.status_code in (401, 403):
                    raise ExtractionError(f"HTTP {resp.status_code}: Erişim engellendi veya link zaman aşımına uğradı.")

                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0)) or total_size
                downloaded = 0
                start_ts = time.monotonic()

                with open(dest, "wb") as fp:
                    if hasattr(session_ctx, "impersonate"):
                        # curl_cffi stream iteration
                        for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                            if chunk:
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
                    else:
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
                if hasattr(session_ctx, "close"):
                    session_ctx.close()
                return  # success
            except Exception as exc:
                if "rateLimit" in str(exc) or "Erişim engellendi" in str(exc):
                    raise
                if attempt == _MAX_RETRIES:
                    raise ExtractionError(
                        f"Gofile download failed after {_MAX_RETRIES} attempts: {exc}"
                    ) from exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Gofile retry %d/%d (waiting %ds): %s",
                    attempt, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)

    # ------------------------------------------------------------------
    # Tracker (incremental sync)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_tracker(path: Path) -> set[str]:
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return set(json.load(f))
        except Exception:
            pass
        return set()

    @staticmethod
    def _save_tracker(path: Path, ids: set[str]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(ids), f)
        except Exception as exc:
            logger.warning("Failed to save tracker: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url: str) -> str:
        m = _URL_PATTERN.match(url)
        if not m:
            raise ExtractionError(f"Invalid Gofile URL: {url}")
        return m.group("id")

    @staticmethod
    def _safe_filename(name: str) -> str:
        # Remove Gofile's decorative prefixes like "⭐NEW FILES"
        name = re.sub(r'^[\s⭐🔥🆕✨💎]+', '', name)
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name.strip(". ") or "gofile_download"

    @staticmethod
    def _normalise(tree: dict[str, Any], content_id: str) -> dict[str, Any]:
        """Convert a Gofile content tree to our canonical info schema."""
        files = GofileExtractor._collect_files_static(tree)
        items: list[dict[str, Any]] = []
        thumb = None
        for f in files:
            t = f.get("thumbnailLink") or f.get("thumb")
            if t:
                thumb = t
                break
        if not thumb:
            thumb = "https://www.google.com/s2/favicons?domain=gofile.io&sz=128"

        for f in files:
            items.append({
                "title": f.get("name", ""),
                "url": f.get("link", ""),
                "duration": None,
                "thumbnail": f.get("thumbnailLink") or f.get("thumb") or thumb,
            })
        return {
            "title": tree.get("name", content_id),
            "thumbnail": thumb,
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": "mixed",
                    "quality": "original",
                    "filesize": sum(f.get("size", 0) for f in files),
                },
            ],
            "is_playlist": len(items) > 1,
            "playlist_items": items,
        }

    @staticmethod
    def _collect_files_static(node: dict[str, Any]) -> list[dict[str, Any]]:
        """Static version of _collect_files for use in _normalise."""
        files: list[dict[str, Any]] = []
        node_type = node.get("type", "")
        if node_type == "file":
            files.append(node)
        elif node_type in ("folder", ""):
            for key in ("children", "contents"):
                children = node.get(key, {})
                if isinstance(children, dict):
                    for child in children.values():
                        files.extend(GofileExtractor._collect_files_static(child))
                elif isinstance(children, list):
                    for child in children:
                        files.extend(GofileExtractor._collect_files_static(child))
        return files


class _NotPremiumError(Exception):
    """Raised when the Gofile API returns an ``error-notPremium`` status."""
