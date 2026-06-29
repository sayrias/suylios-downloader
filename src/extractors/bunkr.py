"""
Suylios Downloader - Bunkr / balbums.st Extractor.

Downloads media from Bunkr and its mirror domains.  Bunkr has retired its
``/api/vs`` endpoint; download URLs are now signed CDN links that are
obtained by scraping album / media pages and, when required, POSTing to
an API-sign endpoint to receive a temporary access token.

Strategy
--------
1. Fetch the album / media page HTML with browser-like headers.
2. Parse with BeautifulSoup to locate media elements (``<video>``,
   ``<img>``, download links).
3. If a CDN signing endpoint is detected in the page source, POST to
   it to obtain a signed token.
4. Construct the final download URL with the signed token.
5. Download with rate limiting (per-domain semaphore) and exponential
   back-off on 403 / 429 / timeout errors.
6. Use ``curl_cffi`` when Cloudflare challenge pages are detected.
"""

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.extractors.base_extractor import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8192
_MAX_RETRIES = 5
_RETRY_BACKOFF = 3  # seconds, doubles per retry
_CONCURRENT_PER_DOMAIN = 2

# Known Bunkr domain patterns (updated dynamically at runtime).
_BUNKR_DOMAINS: set[str] = {
    "bunkr.fi", "bunkr.si", "bunkr.la", "bunkr.su", "bunkr.is",
    "bunkr.ru", "bunkr.ph", "bunkr.ac", "bunkr.ws", "bunkr.cr",
    "bunkr.sk", "bunkr.black", "bunkr.red", "bunkr.site", "bunkr.cat",
    "bunkrr.su", "bunkrr.ru",
}

_DOMAIN_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:bunkrr?|balbums)\.\w+"
    r"(?:/.*)?$",
    re.IGNORECASE,
)

# Per-domain semaphores for rate-limiting
_domain_semaphores: dict[str, threading.Semaphore] = {}
_sem_lock = threading.Lock()


def _get_semaphore(domain: str) -> threading.Semaphore:
    with _sem_lock:
        if domain not in _domain_semaphores:
            _domain_semaphores[domain] = threading.Semaphore(_CONCURRENT_PER_DOMAIN)
        return _domain_semaphores[domain]


class BunkrExtractor(BaseExtractor):
    """Bunkr / balbums.st media extractor."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        self._cffi_session = None  # lazy curl_cffi session
        self._scrape_cache: dict[str, tuple[str, list[dict[str, Any]]]] = {}

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        return bool(_DOMAIN_RE.match(url))

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        page_type, items = self._scrape_page(url)
        title = self._title_from_url(url)

        playlist_items: list[dict[str, Any]] = []
        for item in items:
            playlist_items.append({
                "title": item.get("filename", ""),
                "url": item.get("url", ""),
                "duration": None,
                "thumbnail": item.get("thumbnail"),
            })

        thumb = None
        for item in items:
            if item.get("thumbnail"):
                thumb = item["thumbnail"]
                break
        if not thumb:
            try:
                html = self._fetch_page(url)
                soup = BeautifulSoup(html, "html.parser")
                for img in soup.select("img[src*='http'], img[src*='thumbs'], img[src*='cdn']"):
                    src = img.get("src", "")
                    if src and not any(k in src.lower() for k in ("icon", "logo", "avatar", "badge")):
                        thumb = src
                        break
            except Exception:
                pass
        if not thumb:
            thumb = "https://www.google.com/s2/favicons?domain=bunkr.si&sz=128"

        return {
            "title": title,
            "thumbnail": thumb,
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": "mixed",
                    "quality": "original",
                    "filesize": 0,
                },
            ],
            "is_playlist": len(playlist_items) > 1,
            "playlist_items": playlist_items,
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
        page_type, items = self._scrape_page(url)

        if not items:
            raise ExtractionError("No downloadable media found on this Bunkr page.")

        folder_name = self._safe_filename(self._title_from_url(url))
        dest = Path(output_path) / folder_name if len(items) > 1 else Path(output_path)
        dest.mkdir(parents=True, exist_ok=True)

        last_path = ""
        for idx, item in enumerate(items, 1):
            dl_url = item.get("url", "")
            if "/file/" in dl_url or "dl.bunkr" in dl_url:
                dl_url = self._resolve_dl_page(dl_url, url)
            filename = self._safe_filename(item.get("filename", f"file_{idx}"))
            if not dl_url:
                continue

            file_dest = dest / filename
            domain = urlparse(dl_url).hostname or ""
            sem = _get_semaphore(domain)

            sem.acquire()
            try:
                def _wrapped_hook(data: dict[str, Any]) -> None:
                    data["item_index"] = idx
                    data["item_count"] = len(items)
                    data["item_title"] = filename
                    if progress_hook:
                        progress_hook(data)

                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "filename": str(file_dest),
                        "item_index": idx,
                        "item_count": len(items),
                        "item_title": filename,
                        "downloaded_bytes": 0,
                        "total_bytes": 0,
                    })

                self._download_file(dl_url, file_dest, url, _wrapped_hook)
                last_path = str(file_dest)
            except Exception as exc:
                logger.error(
                    "Bunkr file %d/%d (%s) failed: %s",
                    idx, len(items), filename, exc,
                )
            finally:
                sem.release()

        if progress_hook:
            progress_hook({"status": "finished", "filename": last_path or str(dest)})

        return last_path or str(dest)

    # ------------------------------------------------------------------
    # Page scraping
    # ------------------------------------------------------------------

    def _scrape_page(self, url: str) -> tuple[str, list[dict[str, Any]]]:
        """Fetch and parse a Bunkr page."""
        if hasattr(self, "_scrape_cache") and url in self._scrape_cache:
            return self._scrape_cache[url]
        html = self._fetch_page(url)
        soup = BeautifulSoup(html, "html.parser")

        # Detect page type
        items: list[dict[str, Any]] = []

        path_part = urlparse(url).path.lower()
        is_single = any(path_part.startswith(prefix) for prefix in ("/f/", "/v/", "/i/", "/d/"))

        # --- Album page: grid/list of media thumbnails with links ---
        album_links = [] if is_single else soup.select(
            'a[href*="/v/"], a[href*="/i/"], a[href*="/d/"], a[href*="/f/"],'
            ' .grid-images a, .grid-images_box a,'
            ' a.grid-images_box-link, a.image-container'
        )
        if album_links:
            seen_urls: set[str] = set()
            for a_tag in album_links:
                href = a_tag.get("href", "")
                if not href or href in seen_urls:
                    continue
                # Resolve relative URLs
                full_url = urljoin(url, href)
                if not _DOMAIN_RE.match(full_url):
                    continue
                path_part = urlparse(full_url).path.strip('/')
                if len(path_part) <= 2:
                    continue
                seen_urls.add(full_url)

                # Try to get thumbnail
                img = a_tag.select_one("img")
                thumb = img.get("src", "") if img else ""

                # Try to get filename from the link text or image alt
                fname = ""
                if img:
                    fname = img.get("alt", "") or ""
                if not fname:
                    fname = a_tag.get_text(strip=True) or Path(urlparse(full_url).path).name

                items.append({
                    "url": full_url,
                    "filename": fname,
                    "thumbnail": thumb,
                    "_needs_resolve": True,
                })

            # Now resolve each media page to get the actual download URL
            resolved: list[dict[str, Any]] = []
            for item in items:
                if item.get("_needs_resolve"):
                    try:
                        _, sub_items = self._parse_media_page(item["url"])
                        if sub_items:
                            sub_items[0]["thumbnail"] = item.get("thumbnail")
                            resolved.extend(sub_items)
                        else:
                            resolved.append(item)
                    except Exception as exc:
                        logger.warning("Failed to resolve %s: %s", item["url"], exc)
                        resolved.append(item)
                else:
                    resolved.append(item)

            res = ("album", resolved)
            if hasattr(self, "_scrape_cache"):
                self._scrape_cache[url] = res
            return res

        # --- Single media page ---
        _, media_items = self._parse_media_page(url, soup=soup)
        res = ("media", media_items)
        if hasattr(self, "_scrape_cache"):
            self._scrape_cache[url] = res
        return res

    def _parse_media_page(
        self, url: str, *, soup: Optional[BeautifulSoup] = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Extract the actual CDN URL from a single media page."""
        if soup is None:
            html = self._fetch_page(url)
            soup = BeautifulSoup(html, "html.parser")
        else:
            html = str(soup)

        items: list[dict[str, Any]] = []

        # Get real filename from h1 or title
        real_fname = ""
        h1 = soup.select_one("h1")
        if h1:
            real_fname = h1.get_text(strip=True)
        if not real_fname and soup.title:
            real_fname = soup.title.get_text(strip=True).split(" | ")[0].strip()

        # 1. <video> <source src="..."> or direct video src
        for source in soup.select("video source[src], video[src]"):
            src = source.get("src", "")
            if src:
                src = self._maybe_sign_url(src, html, url)
                fname = real_fname or Path(urlparse(src).path).name
                items.append({
                    "url": src,
                    "filename": fname,
                })

        # 2. Direct download link / button (Prioritized before images!)
        if not items:
            for a_tag in soup.select('a[href]'):
                href = a_tag.get("href", "")
                text = a_tag.get_text(strip=True).lower()
                if any(k in href.lower() for k in ("dl.bunkr", "/file/", "cdn", "media-files", ".mp4", ".mkv", ".zip")) or "download" in text or "indir" in text:
                    if href.startswith("http") or href.startswith("/"):
                        full_href = urljoin(url, href)
                        full_href = self._maybe_sign_url(full_href, html, url)
                        fname = real_fname or Path(urlparse(full_href).path).name
                        if not any(x["url"] == full_href for x in items):
                            items.append({
                                "url": full_href,
                                "filename": fname,
                            })
                            break

        # 3. <img> with CDN URL (only if no video/download link found!)
        if not items:
            for img in soup.select("img.max-h-full, img.rounded, img[src*='cdn']"):
                src = img.get("src", "")
                if src and ("cdn" in src or "media-files" in src):
                    src = self._maybe_sign_url(src, html, url)
                    fname = real_fname or Path(urlparse(src).path).name
                    items.append({
                        "url": src,
                        "filename": fname,
                    })

        # 4. Regex fallback: look for CDN URLs in inline scripts
        if not items:
            for m in re.finditer(
                r'(?:src|href|url)\s*[=:]\s*["\']'
                r'(https?://[^"\']+(?:cdn|media-files|dl\.bunkr)[^"\']+)["\']',
                html,
            ):
                cdn_url = m.group(1)
                cdn_url = self._maybe_sign_url(cdn_url, html, url)
                fname = real_fname or Path(urlparse(cdn_url).path).name
                items.append({
                    "url": cdn_url,
                    "filename": fname,
                })

        thumb = ""
        meta_og = soup.select_one("meta[property='og:image']") or soup.select_one("meta[name='twitter:image']")
        if meta_og and meta_og.get("content"):
            thumb = meta_og["content"]
        if not thumb:
            v_tag = soup.select_one("video[poster]")
            if v_tag and v_tag.get("poster"):
                thumb = v_tag["poster"]
        if thumb:
            for it in items:
                it["thumbnail"] = thumb

        res = ("media", items)
        if hasattr(self, "_scrape_cache"):
            self._scrape_cache[url] = res
        return res

    def _maybe_sign_url(self, cdn_url: str, page_html: str, page_url: str) -> str:
        """If the page contains a CDN signing endpoint, POST to it and
        return the signed URL.  Otherwise return the original URL."""
        if "/file/" in cdn_url or "dl.bunkr" in cdn_url:
            resolved = self._resolve_dl_page(cdn_url, page_url)
            if resolved != cdn_url:
                return resolved

        # Look for the signing endpoint pattern
        sign_match = re.search(
            r'(?:fetch|axios\.post|url\s*[:=])\s*["\']'
            r'(https?://[^"\']*(?:glb-apisign|api-sign|sign)[^"\']*)["\']',
            page_html,
        )
        if not sign_match:
            # Also try a simpler pattern
            sign_match = re.search(
                r'(https?://[^"\']*apisign[^"\']*)',
                page_html,
            )

        if not sign_match:
            return cdn_url

        sign_endpoint = sign_match.group(1)
        try:
            resp = self._session.post(
                sign_endpoint,
                json={"url": cdn_url},
                headers={
                    "Content-Type": "application/json",
                    "Referer": page_url,
                    "Origin": f"{urlparse(page_url).scheme}://{urlparse(page_url).hostname}",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            # The response may contain the signed URL or a token to append
            if "url" in data:
                return data["url"]
            if "token" in data:
                separator = "&" if "?" in cdn_url else "?"
                return f"{cdn_url}{separator}token={data['token']}"

        except Exception as exc:
            logger.warning("CDN signing failed for %s: %s", cdn_url, exc)

        return cdn_url

    def _resolve_dl_page(self, dl_url: str, page_url: str) -> str:
        """Resolve dl.bunkr.cr/file/ID HTML page to direct signed video URL."""
        m = re.search(r'/file/([a-zA-Z0-9_-]+)', dl_url)
        if not m:
            return dl_url
        file_id = m.group(1)
        origin = f"{urlparse(dl_url).scheme}://{urlparse(dl_url).hostname}"
        api_url = urljoin(origin, "/api/_001_v2")
        try:
            resp = self._session.post(
                api_url,
                json={"id": file_id},
                headers={
                    "Content-Type": "application/json",
                    "Referer": dl_url,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                meta = resp.json()
                mediafiles = meta.get("mediafiles", "")
                path = meta.get("path", "")
                if mediafiles and path:
                    raw_url = mediafiles + path
                    parsed_path = quote(urlparse(raw_url).path)
                    sign_resp = self._session.get(
                        f"https://glb-apisign.cdn.cr/sign?path={parsed_path}",
                        headers={"Referer": dl_url},
                        timeout=15,
                    )
                    if sign_resp.status_code == 200:
                        sdata = sign_resp.json()
                        token = sdata.get("token", "")
                        ex = sdata.get("ex", "")
                        signed = f"{raw_url}?token={token}&ex={ex}"
                        if meta.get("original"):
                            signed += f"&n={quote(meta['original'])}"
                        return signed
        except Exception as exc:
            logger.warning("Failed to resolve Bunkr dl page %s: %s", dl_url, exc)
        return dl_url

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str) -> str:
        """Fetch a page's HTML, using curl_cffi if Cloudflare blocks us."""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=30)

                # Detect Cloudflare challenge
                if resp.status_code == 403 or (
                    resp.status_code == 200
                    and "cf-browser-verification" in resp.text
                ):
                    logger.info("Cloudflare detected on %s – trying curl_cffi.", url)
                    return self._fetch_with_curl_cffi(url)

                resp.raise_for_status()
                return resp.text

            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (403, 429) and attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "Bunkr %d on attempt %d/%d – retrying in %ds.",
                        status, attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue
                raise ExtractionError(
                    f"Bunkr page fetch failed (HTTP {status}): {url}"
                ) from exc
            except requests.Timeout:
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "Bunkr timeout on attempt %d/%d – retrying in %ds.",
                        attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue
                raise ExtractionError(f"Bunkr page timed out: {url}")
            except requests.RequestException as exc:
                raise ExtractionError(f"Bunkr network error: {exc}") from exc

        raise ExtractionError(f"Bunkr page fetch failed after {_MAX_RETRIES} attempts: {url}")

    def _fetch_with_curl_cffi(self, url: str) -> str:
        """Use curl_cffi to bypass Cloudflare's TLS fingerprinting."""
        try:
            from curl_cffi.requests import Session as CffiSession
        except ImportError:
            raise ExtractionError(
                "Cloudflare is blocking requests and curl_cffi is not installed.  "
                "Run: pip install curl_cffi"
            )

        if self._cffi_session is None:
            from curl_cffi.requests import Session as CffiSession
            self._cffi_session = CffiSession(impersonate="chrome")

        resp = self._cffi_session.get(url, timeout=30)
        if resp.status_code != 200:
            raise ExtractionError(
                f"curl_cffi request failed (HTTP {resp.status_code}): {url}"
            )
        return resp.text

    def _download_file(
        self,
        url: str,
        dest: Path,
        referer: str,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
    ) -> None:
        """Download a single file with exponential back-off."""
        headers = {
            "Referer": referer,
            "Origin": f"{urlparse(referer).scheme}://{urlparse(referer).hostname}",
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.get(
                    url, stream=True, timeout=120, headers=headers,
                )

                if resp.status_code in (403, 429):
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "Bunkr download %d (attempt %d/%d) – retrying in %ds.",
                        resp.status_code, attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                start_ts = time.monotonic()

                with open(dest, "wb") as fp:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        fp.write(chunk)
                        downloaded += len(chunk)

                        if progress_hook:
                            elapsed = time.monotonic() - start_ts
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            eta = int((total - downloaded) / speed) if speed > 0 and total > 0 else 0
                            progress_hook({
                                "status": "downloading",
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                                "speed": speed,
                                "eta": eta,
                                "filename": str(dest),
                            })
                return  # success

            except (requests.RequestException, IOError) as exc:
                if attempt == _MAX_RETRIES:
                    raise ExtractionError(
                        f"Bunkr download failed after {_MAX_RETRIES} attempts: {exc}"
                    ) from exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Bunkr download retry %d/%d (waiting %ds): %s",
                    attempt, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _title_from_url(url: str) -> str:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            return parts[-1]
        return parsed.hostname or "bunkr"

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name.strip(". ") or "bunkr_download"
