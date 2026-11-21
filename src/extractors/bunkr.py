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

import concurrent.futures
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urljoin, urlparse, urlunparse, parse_qs, urlencode

import requests
import urllib3
from bs4 import BeautifulSoup

from src.config import config
from src.extractors.base_extractor import BaseExtractor, ExtractionCancelled, ExtractionError
from src.extractors.fast_downloader import (
    SlidingSpeedTracker,
    _sleep_cancelable,
    _wait_if_paused,
    min_split_bytes,
    ranged_work_stealing_download,
    should_start_direct_segmented,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

_CHUNK_SIZE = 524288  # 512 KB for high-throughput downloads
_MAX_RETRIES = 5
_RETRY_BACKOFF = 3  # seconds, doubles per retry
_CONCURRENT_PER_DOMAIN = 2

# Known Bunkr domain patterns (updated dynamically at runtime).
_BUNKR_DOMAINS: set[str] = {
    "bunkr.lv", "bunkr.la", "bunkr.ws", "bunkr.red",
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
        self._session.verify = False
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
        self._tl = threading.local()
        self._scrape_cache: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        self._cdn_pools: dict[tuple[str, bool], dict[int, Any]] = {}
        self._cdn_pool_lock = threading.Lock()
        self._host_use_cffi: dict[str, bool] = {}

    @staticmethod
    def _new_cffi_session(impersonate: str = "chrome") -> Any:
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session as CffiSession
        return CffiSession(
            impersonate=impersonate,
            verify=False,
            curl_options={CurlOpt.IPRESOLVE: 1},
        )

    def _get_req_session(self) -> requests.Session:
        sess = getattr(self._tl, "req_sess", None)
        if sess is None:
            sess = requests.Session()
            sess.verify = False
            sess.headers.update(self._session.headers)
            self._tl.req_sess = sess
        return sess

    def _get_cffi_session(self, impersonate: str = "chrome120") -> Any:
        sess = getattr(self._tl, "cffi_sess", None)
        if sess is None:
            sess = self._new_cffi_session(impersonate=impersonate)
            self._tl.cffi_sess = sess
        return sess

    def _get_cdn_pool(self, cdn_host: str, use_cffi: bool) -> dict[int, Any]:
        with self._cdn_pool_lock:
            key = (cdn_host, use_cffi)
            if key not in self._cdn_pools:
                self._cdn_pools[key] = {}
            return self._cdn_pools[key]

    def _close_cdn_pools(self) -> None:
        with self._cdn_pool_lock:
            for pool in self._cdn_pools.values():
                for s in pool.values():
                    try:
                        s.close()
                    except Exception:
                        pass
                pool.clear()
            self._cdn_pools.clear()

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
                html = self._last_html if getattr(self, "_last_html_url", None) == url else self._fetch_page(url)
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
        cancel_event = kwargs.get("cancel_event")
        pause_event = kwargs.get("pause_event")
        _wait_if_paused(pause_event, cancel_event)

        page_type, items = self._scrape_page(url)

        if not items:
            raise ExtractionError("No downloadable media found on this Bunkr page.")

        dest = Path(output_path)
        dest.mkdir(parents=True, exist_ok=True)

        def _resolve_item(it: dict[str, Any]) -> tuple[str, str]:
            r_url = it.get("url", "")
            r_name = it.get("filename", "")
            if it.get("_needs_resolve") and r_url:
                _, sub_items = self._scrape_page(r_url)
                if sub_items and sub_items[0].get("url"):
                    r_url = sub_items[0]["url"]
                    r_name = sub_items[0].get("filename", r_name)
            if r_url and ("/file/" in r_url or "dl.bunkr" in r_url):
                r_url = self._resolve_dl_page(r_url, url)
            return r_url, r_name

        last_path = ""
        lookahead_exec = concurrent.futures.ThreadPoolExecutor(max_workers=2) if len(items) > 1 else None
        future_map: dict[int, concurrent.futures.Future] = {}
        try:
            for idx, item in enumerate(items, 1):
                _wait_if_paused(pause_event, cancel_event)

                # En fazla 2 sonraki öğeyi arka planda önceden çöz & imzala
                if lookahead_exec is not None and not (cancel_event is not None and cancel_event.is_set()):
                    for ahead in range(idx + 1, min(len(items), idx + 2) + 1):
                        if ahead not in future_map:
                            future_map[ahead] = lookahead_exec.submit(_resolve_item, items[ahead - 1])

                fut = future_map.pop(idx, None)
                if fut is not None:
                    try:
                        dl_url, resolved_name = fut.result()
                        if resolved_name:
                            item["filename"] = resolved_name
                    except Exception as exc:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExtractionCancelled("İndirme iptal edildi.") from exc
                        logger.warning("Failed to resolve album item %s: %s", item.get("url", ""), exc)
                        dl_url = ""
                else:
                    if progress_hook and idx == 1:
                        progress_hook({
                            "status": "downloading",
                            "status_detail": "Adres çözülüyor & imzalanıyor...",
                            "filename": str(dest / self._safe_filename(item.get("filename", f"file_{idx}"))),
                            "item_index": idx,
                            "item_count": len(items),
                            "item_title": item.get("filename", f"file_{idx}"),
                        })
                    try:
                        dl_url, resolved_name = _resolve_item(item)
                        if resolved_name:
                            item["filename"] = resolved_name
                    except Exception as exc:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExtractionCancelled("İndirme iptal edildi.") from exc
                        logger.warning("Failed to resolve album item %s: %s", item.get("url", ""), exc)
                        dl_url = ""

                # İlk dosya çözülürken sıradaki 2 öğeyi kuyruğa al
                if lookahead_exec is not None and not (cancel_event is not None and cancel_event.is_set()):
                    for ahead in range(idx + 1, min(len(items), idx + 2) + 1):
                        if ahead not in future_map:
                            future_map[ahead] = lookahead_exec.submit(_resolve_item, items[ahead - 1])

                filename = self._safe_filename(item.get("filename", f"file_{idx}"))
                if not dl_url:
                    continue

                file_dest = dest / filename
                domain = urlparse(dl_url).hostname or ""
                sem = _get_semaphore(domain)

                sem.acquire()
                try:
                    _wait_if_paused(pause_event, cancel_event)
                    def _wrapped_hook(data: dict[str, Any]) -> None:
                        data["item_index"] = idx
                        data["item_count"] = len(items)
                        data["item_title"] = filename
                        if progress_hook:
                            progress_hook(data)

                    if progress_hook and idx == 1:
                        progress_hook({
                            "status": "downloading",
                            "status_detail": "Bağlanılıyor...",
                            "filename": str(file_dest),
                            "item_index": idx,
                            "item_count": len(items),
                            "item_title": filename,
                            "downloaded_bytes": 0,
                            "total_bytes": 0,
                        })

                    self._download_file(
                        dl_url, file_dest, url, _wrapped_hook,
                        cancel_event=cancel_event, pause_event=pause_event,
                    )
                    last_path = str(file_dest)
                except ExtractionCancelled:
                    raise
                except BaseException as exc:
                    if (
                        type(exc).__name__ == "_CancelledError"
                        or (cancel_event is not None and cancel_event.is_set())
                    ):
                        raise ExtractionCancelled("İndirme iptal edildi.") from exc
                    if len(items) == 1:
                        raise  # Let DownloadManager handle and mark as error
                    logger.error(
                        "Bunkr file %d/%d (%s) failed: %s",
                        idx, len(items), filename, exc,
                    )
                finally:
                    sem.release()
        finally:
            for fut in future_map.values():
                fut.cancel()
            future_map.clear()
            if lookahead_exec is not None:
                lookahead_exec.shutdown(wait=False, cancel_futures=True)
            self._close_cdn_pools()

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
        self._last_html_url = url
        self._last_html = html
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

            res = ("album", items)
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

        # 0. jsCDN from inline scripts (Highest priority for new Bunkr API)
        m_cdn = re.search(r'var\s+jsCDN\s*=\s*["\']([^"\']+)["\']', html)
        if m_cdn:
            cdn_url = m_cdn.group(1).replace("\\/", "/")
            cdn_url = self._maybe_sign_url(cdn_url, html, url)
            fname = real_fname or Path(urlparse(cdn_url).path).name
            items.append({
                "url": cdn_url,
                "filename": fname,
            })

        # 1. <video> <source src="..."> or direct video src
        if not items:
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
        """If the page contains a CDN signing endpoint, GET the token and
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
        origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).hostname}"
        parsed_path = quote(urlparse(cdn_url).path, safe='')
        sign_url = f"{sign_endpoint}?path={parsed_path}"
        headers = {
            "Referer": origin + "/",
        }

        req_sess = self._get_req_session()
        resp = None
        for _try in range(2):
            try:
                resp = req_sess.get(sign_url, headers=headers, timeout=(8, 12))
                if resp.status_code == 200:
                    break
            except Exception:
                resp = None

            try:
                cffi_sess = self._get_cffi_session("chrome120")
                resp = cffi_sess.get(sign_url, headers=headers, timeout=(8, 12))
                if resp.status_code == 200:
                    break
            except Exception as exc:
                if _try == 1:
                    logger.warning("Failed to sign url (cffi fallback): %s", exc)
                    return cdn_url
            time.sleep(0.4)

        try:
            if resp and resp.status_code == 200:
                data = resp.json()
                if "url" in data:
                    return data["url"]
                if "token" in data:
                    separator = "&" if "?" in cdn_url else "?"
                    ex_param = f"&ex={data['ex']}" if "ex" in data else ""
                    return f"{cdn_url}{separator}token={data['token']}{ex_param}"
        except Exception:
            pass

        return cdn_url

    def _resolve_dl_page(self, dl_url: str, page_url: str) -> str:
        """Resolve dl.bunkr.cr/file/ID HTML page to direct signed video URL."""
        m = re.search(r'/file/([a-zA-Z0-9_-]+)', dl_url)
        if not m:
            return dl_url
        file_id = m.group(1)
        origin = f"{urlparse(dl_url).scheme}://{urlparse(dl_url).hostname}"
        api_url = urljoin(origin, "/api/_001_v2")
        headers = {
            "Content-Type": "application/json",
            "Referer": origin + "/",
        }
        json_data = {"id": file_id}
        
        req_sess = self._get_req_session()
        try:
            resp = req_sess.post(api_url, json=json_data, headers=headers, timeout=(8, 12))
        except Exception:
            resp = None

        if not resp or resp.status_code != 200:
            try:
                cffi_sess = self._get_cffi_session("chrome120")
                resp = cffi_sess.post(api_url, json=json_data, headers=headers, timeout=(8, 12))
            except Exception as exc:
                logger.warning("Failed to resolve Bunkr dl page (cffi fallback) %s: %s", dl_url, exc)
                return dl_url

        try:
            if resp and resp.status_code == 200:
                meta = resp.json()
                mediafiles = meta.get("mediafiles", "")
                path = meta.get("path", "")
                if mediafiles and path:
                    raw_url = mediafiles + path
                    parsed_path = quote(urlparse(raw_url).path, safe='')
                    sign_url = f"https://glb-apisign.cdn.cr/sign?path={parsed_path}"
                    
                    try:
                        sign_resp = req_sess.get(sign_url, headers={"Referer": origin + "/"}, timeout=(8, 12))
                    except Exception:
                        sign_resp = None
                        
                    if not sign_resp or sign_resp.status_code != 200:
                        sign_resp = self._get_cffi_session("chrome120").get(
                            sign_url, headers={"Referer": origin + "/"}, timeout=(8, 12)
                        )
                        
                    if sign_resp and sign_resp.status_code == 200:
                        sdata = sign_resp.json()
                        token = sdata.get("token", "")
                        ex = sdata.get("ex", "")
                        signed = f"{raw_url}?token={token}&ex={ex}"
                        return signed
        except Exception as exc:
            logger.warning("Failed to resolve Bunkr dl page %s: %s", dl_url, exc)
        return dl_url

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str) -> str:
        """Fetch a page's HTML, trying alternative Bunkr domains on connection failures."""
        parsed = urlparse(url)
        original_host = parsed.hostname or ""

        # Ordered list of fallback domains to try (live hotfix manifest first, then static defaults)
        _DEFAULT_FALLBACK_DOMAINS = [
            "bunkr.lv", "bunkr.la", "bunkr.ws", "bunkr.red",
            "bunkr.cr", "bunkr.si", "bunkr.fi", "bunkr.ph",
            "bunkr.ac", "bunkr.sk", "bunkr.black", "bunkr.site",
        ]
        try:
            from src.engine.site_registry import site_registry
            _FALLBACK_DOMAINS = site_registry.get_domains_for("bunkr", _DEFAULT_FALLBACK_DOMAINS)
        except Exception:
            _FALLBACK_DOMAINS = _DEFAULT_FALLBACK_DOMAINS

        # Build candidate URLs: original domain first, then alternatives
        candidates: list[str] = [url]
        for domain in _FALLBACK_DOMAINS:
            if domain != original_host:
                alt = parsed._replace(netloc=domain).geturl()
                candidates.append(alt)

        last_exc: Exception = Exception("No candidates tried")

        req_sess = self._get_req_session()
        for candidate_url in candidates:
            for attempt in range(1, 3):  # 2 attempts per domain
                try:
                    resp = req_sess.get(candidate_url, timeout=15)

                    # Detect Cloudflare challenge
                    if resp.status_code == 403 or (
                        resp.status_code == 200
                        and "cf-browser-verification" in resp.text
                    ):
                        logger.info("Cloudflare detected on %s – trying curl_cffi.", candidate_url)
                        return self._fetch_with_curl_cffi(candidate_url)

                    resp.raise_for_status()
                    return resp.text

                except (requests.Timeout, requests.ConnectionError) as exc:
                    last_exc = exc
                    logger.info(
                        "Connection error on %s (%s) – trying curl_cffi.",
                        candidate_url, exc,
                    )
                    try:
                        return self._fetch_with_curl_cffi(candidate_url)
                    except Exception as cffi_exc:
                        last_exc = cffi_exc
                        exc_str = str(cffi_exc).lower()
                        is_reset = any(k in exc_str for k in (
                            "connection aborted", "bağlantı karşıdan kesildi",
                            "connection reset", "(35) recv failure",
                        ))
                        if is_reset:
                            logger.warning(
                                "Domain %s blocked/rate-limited – trying next domain.",
                                candidate_url,
                            )
                            break  # try next domain immediately
                        if attempt < 2:
                            time.sleep(2)
                            continue
                        break  # try next domain

                except requests.HTTPError as exc:
                    last_exc = exc
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in (403, 429):
                        logger.info("Bunkr HTTP %d on %s – trying curl_cffi.", status, candidate_url)
                        try:
                            return self._fetch_with_curl_cffi(candidate_url)
                        except Exception:
                            pass
                        break  # try next domain
                    raise ExtractionError(f"Bunkr page fetch failed (HTTP {status}): {candidate_url}") from exc

                except requests.RequestException as exc:
                    last_exc = exc
                    if attempt < 2:
                        time.sleep(2)
                        continue
                    break

        raise ExtractionError(f"Bunkr page fetch failed on all domains: {last_exc}")


    def _fetch_with_curl_cffi(self, url: str) -> str:
        """Use curl_cffi to bypass Cloudflare's TLS fingerprinting."""
        try:
            from curl_cffi.requests import Session as CffiSession
        except ImportError:
            raise ExtractionError(
                "Cloudflare is blocking requests and curl_cffi is not installed.  "
                "Run: pip install curl_cffi"
            )

        cffi_sess = self._get_cffi_session("chrome120")
        resp = cffi_sess.get(url, timeout=30, verify=False)
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
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> None:
        """Download a single file with exponential back-off."""
        ext = Path(urlparse(url).path).suffix.lower()
        is_video = ext in (".mp4", ".mkv", ".webm")
        headers = {
            "Referer": f"{urlparse(referer).scheme}://{urlparse(referer).hostname}/",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "video" if is_video else "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        cdn_host = urlparse(url).hostname or ""
        use_cffi = self._host_use_cffi.get(cdn_host, False)
        seg_enabled, seg_conns, seg_mode = config.get_segmented_settings()
        # Bunkr CDN sunucuları aynı IP'den >2 paralel sokette HTTP 503 döndürüyor (2x en hızlı güvenli sınır)
        bunkr_workers = min(seg_conns, 2) if (seg_enabled and seg_conns >= 2) else 1

        def _make_bunkr_cffi(_gen: int = 0) -> Any:
            return self._new_cffi_session("chrome")

        def _make_bunkr_req(_gen: int = 0) -> Any:
            s = requests.Session()
            s.verify = False
            s.headers.update(self._session.headers)
            return s

        need_resign = False
        for attempt in range(1, _MAX_RETRIES + 1):
            _wait_if_paused(pause_event, cancel_event)
            resp = None
            tmp_path = dest.with_suffix(dest.suffix + ".downloading")
            cdn_pool = self._get_cdn_pool(cdn_host, use_cffi)
            probe_sess = None
            probe_kept = False
            try:
                if use_cffi or need_resign:
                    need_resign = False
                    # Re-sign URL whenever missing token or retried via curl_cffi
                    try:
                        parsed_cdn = urlparse(url)
                        qs = parse_qs(parsed_cdn.query)
                        if "token" in qs or parsed_cdn.path.startswith("/storage/"):
                            path_encoded = quote(parsed_cdn.path, safe='')
                            sign_headers = {"Referer": f"{urlparse(referer).scheme}://{urlparse(referer).hostname}/"}
                            cffi_s = self._get_cffi_session("chrome")
                            r_sign = cffi_s.get(
                                f"https://glb-apisign.cdn.cr/sign?path={path_encoded}",
                                headers=sign_headers,
                                timeout=(8, 12),
                            )
                            if r_sign.status_code == 200:
                                new_sign_data = r_sign.json()
                                qs["token"] = [new_sign_data["token"]]
                                qs["ex"] = [str(new_sign_data["ex"])]
                                qs.pop("n", None)
                                new_query = urlencode(qs, doseq=True)
                                url = urlunparse(parsed_cdn._replace(query=new_query))
                    except Exception as sign_e:
                        logger.warning("Failed to re-sign with curl_cffi: %s", sign_e)

                probe_sess = cdn_pool.pop(0, None) or (_make_bunkr_cffi() if use_cffi else _make_bunkr_req())
                req_headers = dict(headers)
                probed_range = bunkr_workers >= 2
                if probed_range:
                    req_headers["Range"] = "bytes=0-0"

                resp = probe_sess.get(
                    url, stream=True, timeout=(12, 25), headers=req_headers, verify=False,
                )

                if resp.status_code in (403, 429):
                    status_code = resp.status_code
                    content_type = resp.headers.get("Content-Type", "").lower()
                    is_json_error = "application/json" in content_type
                    if not is_json_error:
                        try:
                            peek = next(resp.iter_content(chunk_size=128), b"").decode("utf-8", "ignore").strip()
                            if peek.startswith("{") and "forbidden" in peek.lower():
                                is_json_error = True
                        except Exception:
                            pass

                    if hasattr(resp, "close"):
                        try:
                            resp.close()
                        except Exception:
                            pass
                    resp = None

                    if is_json_error:
                        logger.warning("Bunkr file is deleted or forbidden by server (JSON response). Skipping.")
                        raise Exception("Bunkr sunucusunda bu dosya bulunamadı veya silinmiş (403 Forbidden).")

                    try:
                        probe_sess.close()
                    except Exception:
                        pass
                    probe_sess = None

                    if not use_cffi:
                        if "token=" not in url and urlparse(url).path.startswith("/storage/"):
                            need_resign = True
                        logger.info(
                            "Bunkr CDN returned %d with requests – switching to curl_cffi (attempt %d/%d).",
                            status_code, attempt, _MAX_RETRIES,
                        )
                        use_cffi = True
                        continue
                    need_resign = True
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "Bunkr download %d (attempt %d/%d) – retrying in %ds.",
                        status_code, attempt, _MAX_RETRIES, wait,
                    )
                    if _sleep_cancelable(wait, cancel_event):
                        raise ExtractionCancelled("İndirme iptal edildi.")
                    continue

                if resp.status_code not in (200, 206):
                    resp.raise_for_status()

                dest.parent.mkdir(parents=True, exist_ok=True)
                total_size = 0
                resumable = False
                if resp.status_code == 206:
                    resumable = True
                    cr = resp.headers.get("Content-Range", "")
                    if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                        total_size = int(cr.rsplit("/", 1)[1])
                    if probed_range:
                        # 1 baytlık probe gövdesini tüket ki urllib3 keep-alive soketini canlı tutsun
                        try:
                            _ = resp.content
                        except Exception:
                            pass
                    if hasattr(resp, "close"):
                        try:
                            resp.close()
                        except Exception:
                            pass
                    resp = None

                    # Sıcak probe oturumunu Worker 0'a geri ver (sıfırdan el sıkışmayı önler)
                    cdn_pool[0] = probe_sess
                    probe_kept = True

                    direct_bunkr = (
                        should_start_direct_segmented(total_size, seg_mode)
                        or (seg_mode == "adaptive" and total_size >= 2 * 1024 * 1024)
                    )
                    if bunkr_workers >= 2 and total_size > 0 and direct_bunkr:
                        logger.info(
                            "Bunkr parçalı indirme başlatılıyor (%d bağlantı, %s): %s (%.2f MB)",
                            bunkr_workers, seg_mode, dest.name, total_size / 1048576,
                        )
                        ranged_work_stealing_download(
                            url=url,
                            dest=tmp_path,
                            total_size=total_size,
                            num_workers=bunkr_workers,
                            make_session=_make_bunkr_cffi if use_cffi else _make_bunkr_req,
                            headers=headers,
                            start=0,
                            allow_redirects=True,
                            progress_hook=progress_hook,
                            cancel_event=cancel_event,
                            pause_event=pause_event,
                            mode=seg_mode,
                            session_pool=cdn_pool,
                            keep_sessions=True,
                        )
                        self._host_use_cffi[cdn_host] = use_cffi
                        tmp_path.replace(dest)
                        if progress_hook:
                            progress_hook({
                                "status": "finished",
                                "filename": str(dest),
                                "downloaded_bytes": total_size,
                                "total_bytes": total_size,
                            })
                        return

                    # Small file (< threshold) with Range verified: fetch whole file using warm session
                    probe_sess = cdn_pool.pop(0, None) or (_make_bunkr_cffi() if use_cffi else _make_bunkr_req())
                    probe_kept = False
                    resp = probe_sess.get(
                        url, stream=True, timeout=(12, 25), headers=headers, verify=False,
                    )
                    if resp.status_code not in (200, 206):
                        resp.raise_for_status()
                else:
                    total_size = int(resp.headers.get("Content-Length", 0))
                    if resp.headers.get("Accept-Ranges", "").lower() == "bytes":
                        resumable = True

                downloaded = 0
                tracker = SlidingSpeedTracker(window=3.0)
                last_emit = 0.0
                start_ts = time.monotonic()
                switch_at = -1

                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=262144):
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExtractionCancelled("İndirme iptal edildi.")
                        _wait_if_paused(pause_event, cancel_event)
                        if not chunk:
                            break
                        f.write(chunk)
                        clen = len(chunk)
                        downloaded += clen
                        now = time.monotonic()
                        tracker.add(clen, now)

                        el = now - start_ts
                        if (
                            resumable
                            and bunkr_workers >= 2
                            and total_size > 0
                            and el >= 3.5
                            and (total_size - downloaded) >= min_split_bytes(seg_mode)
                            and (downloaded / el) < 350 * 1024
                        ):
                            switch_at = downloaded
                            break

                        if progress_hook and (now - last_emit >= 0.15):
                            last_emit = now
                            sp = tracker.rate(now)
                            progress_hook({
                                "status": "downloading",
                                "status_detail": "Tek bağlantı iniyor",
                                "filename": str(dest),
                                "downloaded_bytes": downloaded,
                                "total_bytes": total_size,
                                "speed": sp,
                                "eta": int((total_size - downloaded) / sp) if sp > 0 and total_size > 0 else 0,
                            })

                if hasattr(resp, "close"):
                    try:
                        resp.close()
                    except Exception:
                        pass
                resp = None

                if probe_sess is not None:
                    cdn_pool[0] = probe_sess
                    probe_kept = True

                if switch_at >= 0:
                    logger.info(
                        "Bunkr yavaş soket (%s), kalan kısım %d parçalıya geçiriliyor.",
                        dest.name, bunkr_workers,
                    )
                    ranged_work_stealing_download(
                        url=url,
                        dest=tmp_path,
                        total_size=total_size,
                        num_workers=bunkr_workers,
                        make_session=_make_bunkr_cffi if use_cffi else _make_bunkr_req,
                        headers=headers,
                        start=switch_at,
                        allow_redirects=True,
                        progress_hook=progress_hook,
                        cancel_event=cancel_event,
                        pause_event=pause_event,
                        mode=seg_mode,
                        session_pool=cdn_pool,
                        keep_sessions=True,
                    )
                    downloaded = total_size

                tmp_path.replace(dest)
                self._host_use_cffi[cdn_host] = use_cffi
                if progress_hook:
                    progress_hook({
                        "status": "finished",
                        "filename": str(dest),
                        "downloaded_bytes": downloaded,
                        "total_bytes": total_size or downloaded,
                    })
                return  # success

            except BaseException as exc:
                if not probe_kept and probe_sess is not None:
                    try:
                        probe_sess.close()
                    except Exception:
                        pass
                    probe_sess = None
                if resp is not None and hasattr(resp, "close"):
                    try:
                        resp.close()
                    except Exception:
                        pass
                if (
                    isinstance(exc, ExtractionCancelled)
                    or type(exc).__name__ == "_CancelledError"
                    or (cancel_event is not None and cancel_event.is_set())
                ):
                    tmp_path.unlink(missing_ok=True)
                    if isinstance(exc, ExtractionCancelled):
                        raise
                    raise ExtractionCancelled("İndirme iptal edildi.") from exc

                exc_str = str(exc).lower()

                if "403 forbidden" in exc_str and "silinmiş" in exc_str:
                    raise

                is_reset = any(k in exc_str for k in (
                    "connection aborted", "bağlantı karşıdan kesildi",
                    "connection reset", "(35) recv failure",
                ))
                if is_reset and use_cffi:
                    logger.warning(
                        "curl_cffi connection reset (possible ByeDPI conflict) on attempt %d/%d – switching back to requests.",
                        attempt, _MAX_RETRIES,
                    )
                    use_cffi = False
                    if _sleep_cancelable(2, cancel_event):
                        raise ExtractionCancelled("İndirme iptal edildi.")
                    continue

                if not use_cffi:
                    logger.info("Switching to curl_cffi for download due to: %s", exc)
                    use_cffi = True
                    continue

                if attempt == _MAX_RETRIES:
                    raise ExtractionError(f"Bunkr download failed after {_MAX_RETRIES} attempts: {exc}") from exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Bunkr download retry %d/%d (waiting %ds): %s",
                    attempt, _MAX_RETRIES, wait, exc,
                )
                if _sleep_cancelable(wait, cancel_event):
                    raise ExtractionCancelled("İndirme iptal edildi.")

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
