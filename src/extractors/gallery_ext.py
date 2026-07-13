"""
Suylios Downloader - gallery-dl Extractor.

Wraps the gallery-dl Python library for downloading image galleries
from hundreds of sites (Danbooru, Gelbooru, e-hentai, Imgur, …).
"""

import logging
import os
import re
import time
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

    def _configure_cookies_and_proxy(self, gdl_config, url: str) -> None:
        from src.config import config
        proxy_val = getattr(self, "_app_proxy", "") or config.get("proxy", "")
        if proxy_val:
            gdl_config.set(("extractor",), "proxy", proxy_val)
            gdl_config.set(("downloader",), "proxy", proxy_val)

        task_url = (url or getattr(self, "_task_url", "")).lower()
        sites = config.get("site_settings", {})
        custom_sites = {cs.get("key", ""): cs.get("domain", "") for cs in config.get("custom_sites", []) if isinstance(cs, dict)}
        
        best_cookie = ""
        other_cookie = ""
        
        for site_key, s_data in sites.items():
            if not isinstance(s_data, dict):
                continue
            
            cookies_val = s_data.get("cookies", "").strip()
            if not cookies_val:
                continue

            if site_key == "other":
                other_cookie = cookies_val
                continue

            match_key = "youtu" if site_key == "youtube" else site_key
            domain_key = custom_sites.get(site_key, "")
            key_dotted = site_key.replace("_", ".").replace("-", ".")
            key_stripped = site_key.replace("_", "").replace(".", "").replace("-", "")
            url_stripped = task_url.replace("_", "").replace(".", "").replace("-", "")
            is_match = (
                match_key in task_url
                or (domain_key and domain_key.lower() in task_url)
                or key_dotted in task_url
                or (len(key_stripped) > 3 and key_stripped in url_stripped)
            )
            if is_match:
                best_cookie = cookies_val
                break

        final_cookie = best_cookie or other_cookie
        if final_cookie:
            if Path(final_cookie).is_file():
                gdl_config.set(("extractor",), "cookies", str(final_cookie))
                gdl_config.set(("downloader",), "cookies", str(final_cookie))
            elif final_cookie.lower() in ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "safari", "chromium"):
                gdl_config.set(("extractor",), "cookies-from-browser", final_cookie.lower())
                gdl_config.set(("downloader",), "cookies-from-browser", final_cookie.lower())

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def _configure_gdl_base(self, gdl_config, url: str) -> None:
        from src.config import config
        if config.is_portable():
            from src.config import _get_app_root
            gdl_cache = _get_app_root() / "data" / "gdl_cache"
            gdl_cache.mkdir(parents=True, exist_ok=True)
            gdl_config.set(("cache",), "file", str(gdl_cache / "cache.sqlite3"))
        gdl_config.set(("extractor",), "user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        gdl_config.set(("downloader",), "user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        gdl_config.set(("extractor", "reddit"), "comments", 0)
        gdl_config.set(("extractor", "reddit"), "morecomments", False)
        gdl_config.set(("extractor",), "sleep-request", 0.5)
        gdl_config.set(("extractor",), "sleep", 0.3)
        gdl_config.set(("extractor",), "retries", 3)
        gdl_config.set(("extractor",), "timeout", 15)
        gdl_config.set(("extractor",), "verify", False)
        gdl_config.set(("downloader",), "retries", 3)
        gdl_config.set(("downloader",), "timeout", 15)
        gdl_config.set(("downloader",), "verify", False)
        gdl_config.set(("downloader", "http"), "verify", False)
        if "gofile.io" in url:
            gdl_config.set(("extractor", "gofile"), "retries", 0)
            gdl_config.set(("extractor", "gofile"), "timeout", 5)
            gdl_config.set(("extractor",), "retries", 0)
            gdl_config.set(("extractor",), "timeout", 5)

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

        # Rate limit engellerini ve Reddit 403/429 hatalarını önle (Tarayıcı taklidi)
        gdl_config.clear()
        self._configure_gdl_base(gdl_config, url)

        self._configure_cookies_and_proxy(gdl_config, url)

        extractor = find_extractor(url)
        if extractor is None:
            raise ExtractionError(f"gallery-dl cannot handle: {url}")

        # Message type constants
        from gallery_dl.extractor.common import Message as GDLMessage
        MSG_DIRECTORY = GDLMessage.Directory  # 2
        MSG_URL = GDLMessage.Url              # 3
        MSG_QUEUE = getattr(GDLMessage, 'Queue', 6)  # 6 – linked galleries/albums

        # Attempt to gather basic info without downloading everything
        items: list[dict[str, Any]] = []
        title = ""
        start_scan = time.time()
        last_exc: Optional[Exception] = None
        try:
            extractor.initialize()
            for msg in extractor:
                msg_type = msg[0] if msg else None
                if msg_type == MSG_DIRECTORY:
                    directory_data = msg[1] if len(msg) > 1 else {}
                    if isinstance(directory_data, dict):
                        # Prioritize thread/gallery title over category name
                        title = directory_data.get("thread", {}).get("title", "") or \
                                directory_data.get("gallery", {}).get("title", "") or \
                                directory_data.get("album", {}).get("title", "") or \
                                directory_data.get("title", "")
                        
                        if not title:
                            title = str(directory_data.get("category", "Gallery"))
                elif msg_type == MSG_URL:
                    file_url = msg[1] if len(msg) > 1 else ""
                    file_meta = msg[2] if len(msg) > 2 else {}
                    if not title and isinstance(file_meta, dict):
                        if "thread" in file_meta and "title" in file_meta["thread"]:
                            title = file_meta["thread"]["title"]
                        elif "gallery" in file_meta and "title" in file_meta["gallery"]:
                            title = file_meta["gallery"]["title"]

                    if isinstance(file_meta, dict):
                        fname = file_meta.get("filename", Path(file_url).stem)
                    else:
                        fname = Path(file_url).stem
                    items.append({
                        "title": fname,
                        "url": file_url,
                        "duration": None,
                        "thumbnail": None,
                    })
                    # Cap preview to 20 items or 5 seconds
                    if len(items) >= 20 or (time.time() - start_scan) > 5.0:
                        break
                elif msg_type == MSG_QUEUE:
                    # Forum threads (SimpCity etc.) send Queue messages pointing to
                    # linked galleries/albums on other hosts (goonbox, bunkr, …).
                    # Treat each queued URL as one logical item for preview purposes.
                    queued_url = msg[1] if len(msg) > 1 else ""
                    queued_meta = msg[2] if len(msg) > 2 else {}
                    if not title and isinstance(queued_meta, dict):
                        if "thread" in queued_meta and "title" in queued_meta["thread"]:
                            title = queued_meta["thread"]["title"]
                        elif "gallery" in queued_meta and "title" in queued_meta["gallery"]:
                            title = queued_meta["gallery"]["title"]
                    
                    if queued_url:
                        if isinstance(queued_meta, dict):
                            fname = queued_meta.get("description", "") or queued_meta.get("title", "") or Path(queued_url).stem
                        else:
                            fname = Path(queued_url).stem
                        items.append({
                            "title": str(fname) or queued_url,
                            "url": queued_url,
                            "duration": None,
                            "thumbnail": None,
                        })
                    if len(items) >= 20 or (time.time() - start_scan) > 5.0:
                        break
        except Exception as exc:
            last_exc = exc
            logger.warning("gallery-dl metadata scan partial: %s", exc)

        if len(items) == 0:
            exc_msg = str(last_exc or "")
            if "AuthRequired" in exc_msg or "logged-in" in exc_msg or "403" in exc_msg:
                raise ExtractionError("gallery-dl bu linke erişirken 403 (Giriş/Cookie Gerekli) engeline takıldı: 'simpcity.cr' gibi sitelerdeki gizli/üyelik gerektiren konuları indirebilmek için 'Programlar ve Siteler' ayarlarında bu siteye (veya 'Diğer Siteler'e) 'Cookies Dosya Yolu' olarak kullandığınız tarayıcı adını (Örn: chrome veya edge) ya da cookies.txt dosyanızı girmelisiniz.")
            raise ExtractionError("gallery-dl bu linkten hiçbir içerik/metadata bulamadı veya 403 engeline takıldı.")

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
        self._configure_gdl_base(gdl_config, url)
        gdl_config.set(
            ("extractor",), "base-directory", str(dest),
        )
        gdl_config.set(
            ("extractor",), "directory", [],
        )
        gdl_config.set(
            ("extractor",), "filename", "{filename}.{extension}",
        )
        gdl_config.set(
            ("output",), "mode", "null",
        )
        # Rate limit engellerini ve Reddit 403/429 hatalarını önle (Tarayıcı taklidi)
        gdl_config.set(("extractor",), "user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        gdl_config.set(("downloader",), "user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        gdl_config.set(("extractor", "reddit"), "comments", 0)
        gdl_config.set(("extractor", "reddit"), "morecomments", False)
        gdl_config.set(("extractor",), "sleep-request", 0.5)
        gdl_config.set(("extractor",), "sleep", 0.3)
        gdl_config.set(("extractor",), "retries", 2)
        gdl_config.set(("extractor",), "timeout", 10)
        gdl_config.set(("extractor",), "verify", False)
        gdl_config.set(("downloader",), "retries", 1)
        gdl_config.set(("downloader",), "timeout", 8)
        gdl_config.set(("downloader",), "verify", False)
        gdl_config.set(("downloader", "http"), "verify", False)
        # Gofile: rate-limit bekleme süresini sıfırla (varsayılan 1dk x4=4dk beklemeyi engeller)
        if "gofile.io" in url:
            gdl_config.set(("extractor", "gofile"), "retries", 0)
            gdl_config.set(("extractor", "gofile"), "timeout", 5)
            gdl_config.set(("extractor",), "retries", 0)
            gdl_config.set(("extractor",), "timeout", 5)

        self._configure_cookies_and_proxy(gdl_config, url)

        downloaded_count = 0
        last_file = ""

        try:
            job = DownloadJob(url)

            # Hook into the download process to track progress
            original_handle_url = getattr(job, 'handle_url', None)
            original_handle_queue = getattr(job, 'handle_queue', None)

            def _patched_handle_url(url_or_tuple, kwdict=None):
                nonlocal downloaded_count, last_file
                # gallery-dl >= 1.27 calls handle_url(url, kwdict) with 2 args.
                # Older versions call handle_url((url, kwdict)) with 1 tuple arg.
                if kwdict is not None:
                    result = original_handle_url(url_or_tuple, kwdict)
                else:
                    result = original_handle_url(url_or_tuple)
                downloaded_count += 1
                if hasattr(job, 'pathfmt') and job.pathfmt:
                    last_file = getattr(job.pathfmt, 'path', '') or \
                                getattr(job.pathfmt, 'realpath', '')
                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "downloaded_bytes": 100,
                        "total_bytes": 100,
                        "speed": 0,
                        "eta": 0,
                        "filename": last_file,
                    })
                    progress_hook({
                        "status": "finished",
                        "filename": last_file,
                    })
                return result

            def _patched_handle_queue(*args, **kwargs):
                """Called for Message.Queue (type 6) – forum thread linked albums."""
                nonlocal downloaded_count, last_file
                
                q_url = args[0] if len(args) > 0 else kwargs.get("url", "")
                queued_url_str = str(q_url) if q_url else ""
                q_lower = queued_url_str.lower()
                
                # SimpCity filter: User wants photos (goonbox, imgbox, etc.) and turbo videos.
                # Block irrelevant social media links that clutter downloads.
                if "simpcity.cr" in url.lower():
                    if any(bad in q_lower for bad in ("instagram.com", "twitter.com", "x.com", "tiktok.com", "facebook.com", "youtube.com", "youtu.be")):
                        return None

                # gallery-dl doesn't support goonbox or hizliresim natively. Resolve to direct URLs!
                if "goonbox.cr/img/" in q_lower:
                    try:
                        import urllib.request, json
                        img_id = queued_url_str.split("/")[-1].split("?")[0].split("#")[0]
                        req = urllib.request.Request(f"https://goonbox.cr/api/images/{img_id}", headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            data = json.loads(resp.read().decode())
                            if "image" in data and "original_url" in data["image"]:
                                d_url = data["image"]["original_url"]
                                if args: args = (d_url,) + args[1:]
                                else: kwargs['url'] = d_url
                    except Exception: pass
                elif "hizliresim.com/" in q_lower and "i.hizliresim.com" not in q_lower:
                    try:
                        import urllib.request, re
                        req = urllib.request.Request(queued_url_str, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            html = resp.read().decode('utf-8', errors='ignore')
                            m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)
                            if m:
                                d_url = m.group(1)
                                if args: args = (d_url,) + args[1:]
                                else: kwargs['url'] = d_url
                    except Exception: pass

                result = None
                if original_handle_queue:
                    result = original_handle_queue(*args, **kwargs)
                # After gallery-dl processes the queued URL internally,
                # update counts so progress is shown.
                downloaded_count += 1
                
                if progress_hook:
                    # Find the latest file in the destination folder to show in the UI, if any
                    latest_file = queued_url_str
                    if dest.exists():
                        files = [f for f in dest.rglob('*') if f.is_file()]
                        if files:
                            latest_file = str(files[-1])
                            last_file = latest_file
                            
                    # Sadece finished statüsünü gönderiyoruz, böylece fake %100 göstermez
                    progress_hook({
                        "status": "finished",
                        "filename": latest_file,
                        "item_index": downloaded_count,
                        "item_count": 0
                    })
                return result

            if original_handle_url:
                job.handle_url = _patched_handle_url
            if original_handle_queue:
                job.handle_queue = _patched_handle_queue
            job.run()

            if downloaded_count == 0:
                # For Queue-only responses (forum threads), downloaded_count may
                # still be 0 because gallery-dl handles Queue internally without
                # calling handle_url. Check dest folder instead.
                dest_files = list(dest.rglob('*')) if dest.exists() else []
                actual_files = [f for f in dest_files if f.is_file()]
                if actual_files:
                    downloaded_count = len(actual_files)
                    last_file = str(actual_files[-1])
                if not actual_files:
                    logger.warning("gallery-dl indirme tamamlandı ancak yeni bir dosya bulunamadı (Tümü önceden inmiş olabilir veya hata var).")
                    
        except ExtractionError:
            raise
        except Exception as exc:
            exc_str = str(exc)
            if "AuthRequired" in exc_str or "logged-in" in exc_str or "403" in exc_str:
                raise ExtractionError("gallery-dl indirme yaparken 403 / Giriş Gerekli engeline takıldı. SimpCity veya benzeri sitelerde gizli/üyelik gerektiren içeriklere erişmek için 'Programlar ve Siteler' ayarlarından bu site veya 'Diğer Siteler' için 'Cookies Dosya Yolu' kısmına kullandığınız tarayıcı adını (Örn: chrome veya edge) ya da cookies.txt dosyanızı girmelisiniz.") from exc
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
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            name = unquote(parts[-1])
            if name.endswith(".html"): name = name[:-5]
            if name.endswith(".htm"): name = name[:-4]
            # specific simpcity fix for slugs ending in .12345
            if "." in name and name.split(".")[-1].isdigit():
                name = name.rsplit(".", 1)[0]
            name = name.replace("-", " ").strip()
            return name
        return parsed.hostname or "gallery"
