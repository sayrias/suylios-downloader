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
from urllib.parse import quote, urljoin, urlparse, urlunparse, parse_qs, urlencode

import requests
import urllib3
from bs4 import BeautifulSoup

from src.extractors.base_extractor import BaseExtractor, ExtractionError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

_CHUNK_SIZE = 524288  # 512 KB for high-throughput downloads
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

        dest = Path(output_path)
        dest.mkdir(parents=True, exist_ok=True)

        last_path = ""
        for idx, item in enumerate(items, 1):
            dl_url = item.get("url", "")
            
            if item.get("_needs_resolve"):
                try:
                    _, sub_items = self._scrape_page(dl_url)
                    if sub_items and sub_items[0].get("url"):
                        dl_url = sub_items[0]["url"]
                        item["filename"] = sub_items[0].get("filename", item.get("filename"))
                except Exception as exc:
                    logger.warning("Failed to resolve album item %s: %s", dl_url, exc)
                    continue

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
                if len(items) == 1:
                    raise  # Let DownloadManager handle and mark as error
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
        
        try:
            resp = self._session.get(sign_url, headers=headers, timeout=15)
        except Exception:
            resp = None
            
        if not resp or resp.status_code != 200:
            try:
                if getattr(self, "_cffi_session", None) is None:
                    from curl_cffi.requests import Session as CffiSession
                    self._cffi_session = CffiSession(impersonate="chrome120", verify=False)
                resp = self._cffi_session.get(sign_url, headers=headers, timeout=15)
            except Exception as exc:
                logger.warning("Failed to sign url (cffi fallback): %s", exc)
                return cdn_url

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
        
        try:
            resp = self._session.post(api_url, json=json_data, headers=headers, timeout=15)
        except Exception:
            resp = None

        if not resp or resp.status_code != 200:
            try:
                if getattr(self, "_cffi_session", None) is None:
                    from curl_cffi.requests import Session as CffiSession
                    self._cffi_session = CffiSession(impersonate="chrome120", verify=False)
                resp = self._cffi_session.post(api_url, json=json_data, headers=headers, timeout=15)
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
                    parsed_path = quote(urlparse(raw_url).path)
                    sign_url = f"https://glb-apisign.cdn.cr/sign?path={parsed_path}"
                    
                    try:
                        sign_resp = self._session.get(sign_url, headers={"Referer": origin + "/"}, timeout=15)
                    except Exception:
                        sign_resp = None
                        
                    if not sign_resp or sign_resp.status_code != 200:
                        if getattr(self, "_cffi_session", None) is None:
                            from curl_cffi.requests import Session as CffiSession
                            self._cffi_session = CffiSession(impersonate="chrome120", verify=False)
                        sign_resp = self._cffi_session.get(sign_url, headers={"Referer": origin + "/"}, timeout=15)
                        
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

        # Ordered list of fallback domains to try
        _FALLBACK_DOMAINS = [
            "bunkr.cr", "bunkr.si", "bunkr.fi", "bunkr.ph",
            "bunkr.ac", "bunkr.ws", "bunkr.sk", "bunkr.black",
            "bunkr.red", "bunkr.site",
        ]

        # Build candidate URLs: original domain first, then alternatives
        candidates: list[str] = [url]
        for domain in _FALLBACK_DOMAINS:
            if domain != original_host:
                alt = parsed._replace(netloc=domain).geturl()
                candidates.append(alt)

        last_exc: Exception = Exception("No candidates tried")

        for candidate_url in candidates:
            for attempt in range(1, 3):  # 2 attempts per domain
                try:
                    resp = self._session.get(candidate_url, timeout=15)

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

        if self._cffi_session is None:
            from curl_cffi.requests import Session as CffiSession
            self._cffi_session = CffiSession(impersonate="chrome120", verify=False)

        resp = self._cffi_session.get(url, timeout=30, verify=False)
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
        """Download a single file with exponential back-off.

        Strategy:
        - Try standard `requests` first: benefits from system-level DPI bypass tools (e.g. ByeDPI).
        - If 403/429, switch to curl_cffi (Chrome TLS impersonation) as fallback.
        """
        # Build download-specific headers that override the session's page-navigation headers.
        # The session has Sec-Fetch-Site:none / Sec-Fetch-Mode:navigate which are WRONG for
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

        use_cffi = False

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if use_cffi:
                    if self._cffi_session is None:
                        from curl_cffi.requests import Session as CffiSession
                        self._cffi_session = CffiSession(impersonate="chrome", verify=False)
                    
                    # Re-sign the URL with curl_cffi to match its JA3 fingerprint!
                    if "token=" in url:
                        try:
                            parsed_cdn = urlparse(url)
                            qs = parse_qs(parsed_cdn.query)
                            if "token" in qs:
                                path_encoded = quote(parsed_cdn.path, safe='')
                                r_sign = self._cffi_session.get(
                                    f"https://glb-apisign.cdn.cr/sign?path={path_encoded}",
                                    headers={"Referer": f"{urlparse(referer).scheme}://{urlparse(referer).hostname}/"},
                                    timeout=15
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

                    # For curl_cffi: pass only non-None headers
                    cffi_headers = {k: v for k, v in headers.items() if v is not None}
                    resp = self._cffi_session.get(
                        url, stream=True, timeout=120, headers=cffi_headers, verify=False,
                    )
                else:
                    resp = self._session.get(url, stream=True, timeout=120, headers=headers)

                if resp.status_code in (403, 429):
                    # Bunkr's CDN returns a JSON {"error":"forbidden"} if the file is deleted or path is invalid.
                    # Cloudflare bot protection returns HTML. So if it's JSON, don't retry!
                    content_type = resp.headers.get("Content-Type", "").lower()
                    
                    is_json_error = "application/json" in content_type
                    if not is_json_error:
                        try:
                            # Peek the first bytes to see if it's a JSON string
                            peek = next(resp.iter_content(chunk_size=128), b"").decode("utf-8", "ignore").strip()
                            if peek.startswith("{") and "forbidden" in peek.lower():
                                is_json_error = True
                        except Exception:
                            pass

                    if is_json_error:
                        logger.warning("Bunkr file is deleted or forbidden by server (JSON response). Skipping.")
                        raise Exception("Bunkr sunucusunda bu dosya bulunamadı veya silinmiş (403 Forbidden).")

                    if not use_cffi:
                        logger.info(
                            "Bunkr CDN returned %d with requests – switching to curl_cffi (attempt %d/%d).",
                            resp.status_code, attempt, _MAX_RETRIES,
                        )
                        use_cffi = True
                        continue
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "Bunkr download %d (attempt %d/%d) – retrying in %ds.",
                        resp.status_code, attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code not in (200, 206):
                    resp.raise_for_status()

                # Stream to disk
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = dest.with_suffix(dest.suffix + ".downloading")
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0

                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_hook:
                            progress_hook({
                                "status": "downloading",
                                "filename": str(dest),
                                "downloaded_bytes": downloaded,
                                "total_bytes": total_size,
                            })

                tmp_path.replace(dest)
                if progress_hook:
                    progress_hook({"status": "finished", "filename": str(dest)})
                return  # success

            except Exception as exc:
                exc_str = str(exc).lower()
                
                # If we explicitly raised the forbidden error, abort immediately without retries
                if "403 forbidden" in exc_str and "silinmiş" in exc_str:
                    raise
                    
                is_reset = any(k in exc_str for k in (
                    "connection aborted", "bağlantı karşıdan kesildi",
                    "connection reset", "(35) recv failure",
                ))
                if is_reset and use_cffi:
                    # curl_cffi itself is blocked – likely conflicts with system DPI bypass tool
                    logger.warning(
                        "curl_cffi connection reset (possible ByeDPI conflict) on attempt %d/%d – switching back to requests.",
                        attempt, _MAX_RETRIES,
                    )
                    use_cffi = False
                    time.sleep(2)
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
