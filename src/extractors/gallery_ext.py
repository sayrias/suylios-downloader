"""
Suylios Downloader - gallery-dl Extractor.

Wraps the gallery-dl Python library for downloading image galleries
from hundreds of sites (SimpCity, Danbooru, Gelbooru, e-hentai, Imgur, …).

Subprocess-based download: each concurrent download runs gallery-dl in its
own Python subprocess, guaranteeing zero shared module-level state.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from src.extractors.base_extractor import BaseExtractor, ExtractionError, ExtractionCancelled

logger = logging.getLogger(__name__)


# ======================================================================
# Global subprocess registry — ensures gallery-dl processes are killed
# when tasks are cancelled or the app shuts down.
# ======================================================================

import atexit

_ACTIVE_PROCS_LOCK = threading.Lock()
_ACTIVE_PROCS: dict[str, list["subprocess.Popen"]] = {}   # uid -> list of procs


def _register_proc(uid: str, proc: "subprocess.Popen") -> None:
    with _ACTIVE_PROCS_LOCK:
        if uid not in _ACTIVE_PROCS:
            _ACTIVE_PROCS[uid] = []
        _ACTIVE_PROCS[uid].append(proc)


def _unregister_proc(uid: str, proc: "subprocess.Popen | None" = None) -> None:
    with _ACTIVE_PROCS_LOCK:
        if uid in _ACTIVE_PROCS:
            if proc is not None and proc in _ACTIVE_PROCS[uid]:
                _ACTIVE_PROCS[uid].remove(proc)
            if proc is None or not _ACTIVE_PROCS[uid]:
                _ACTIVE_PROCS.pop(uid, None)


def kill_procs_for_task(uid: str) -> None:
    """Immediately kill all subprocesses for a specific task ID."""
    with _ACTIVE_PROCS_LOCK:
        procs = _ACTIVE_PROCS.pop(uid, [])
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=1.5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def kill_all_gallery_dl_procs() -> None:
    """Terminate all running subprocesses across all tasks. Safe to call at any time."""
    with _ACTIVE_PROCS_LOCK:
        procs = [p for plist in _ACTIVE_PROCS.values() for p in plist]
        _ACTIVE_PROCS.clear()
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    # Give them a moment to exit, then force-kill
    for p in procs:
        try:
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


# Kill all subprocesses when the Python interpreter exits
atexit.register(kill_all_gallery_dl_procs)


# ======================================================================
# Thread-Local gallery-dl config proxy (used by extract_info only)
# ======================================================================

class _TLConfigProxy:
    """
    Drop-in replacement for gallery-dl's global ``_config`` dict.

    Every thread gets its own independent config store so concurrent
    downloads can call ``gdl_config.clear()`` and ``gdl_config.set()``
    without clobbering each other.
    """

    _tl: threading.local

    def __init__(self) -> None:
        object.__setattr__(self, "_tl", threading.local())

    # ---- internal accessor ----
    def _d(self) -> dict:
        tl = object.__getattribute__(self, "_tl")
        if not hasattr(tl, "cfg"):
            tl.cfg = {}
        return tl.cfg

    # ---- full dict interface ----
    def __getitem__(self, k):          return self._d()[k]
    def __setitem__(self, k, v):       self._d()[k] = v
    def __delitem__(self, k):          del self._d()[k]
    def __contains__(self, k):         return k in self._d()
    def __len__(self):                 return len(self._d())
    def __iter__(self):                return iter(self._d())
    def __bool__(self):                return bool(self._d())
    def __repr__(self):                return repr(self._d())
    def clear(self):                   self._d().clear()
    def get(self, k, d=None):          return self._d().get(k, d)
    def items(self):                   return self._d().items()
    def values(self):                  return self._d().values()
    def keys(self):                    return self._d().keys()
    def update(self, *a, **kw):        self._d().update(*a, **kw)
    def pop(self, *a):                 return self._d().pop(*a)
    def setdefault(self, k, d=None):   return self._d().setdefault(k, d)
    def copy(self):                    return self._d().copy()


_TL_CONFIG_INSTALLED = False
_TL_CONFIG_LOCK = threading.Lock()


def _ensure_thread_local_config() -> None:
    """Install the thread-local config proxy into gallery-dl (once)."""
    global _TL_CONFIG_INSTALLED
    if _TL_CONFIG_INSTALLED:
        return
    with _TL_CONFIG_LOCK:
        if _TL_CONFIG_INSTALLED:
            return
        try:
            import gallery_dl.config as _gdlcfg
            _gdlcfg._config = _TLConfigProxy()
            _TL_CONFIG_INSTALLED = True
            logger.info("gallery-dl thread-local config proxy installed.")
        except Exception as exc:
            logger.warning("Could not install thread-local gallery-dl config: %s", exc)


# ======================================================================
# GalleryDLExtractor
# ======================================================================

class GalleryDLExtractor(BaseExtractor):
    """gallery-dl based extractor for image/gallery sites."""

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        if not url:
            return False
        try:
            import gallery_dl.extractor
            result = gallery_dl.extractor.find(url)
            if result is not None:
                return True
        except Exception:
            pass
        url_lower = url.lower()
        if any(domain in url_lower for domain in (
            "simpcity.cr", "simpcity.su", "danbooru", "gelbooru",
            "e-hentai", "imgur.com", "imgbox.com", "realbooru", "rule34.xxx",
        )):
            return True
        return False

    # ------------------------------------------------------------------
    # Cookie / Proxy helpers (used by extract_info)
    # ------------------------------------------------------------------

    def _configure_cookies_and_proxy(self, gdl_config, url: str) -> None:
        from src.config import config
        proxy_val = getattr(self, "_app_proxy", "") or config.get("proxy", "")
        if proxy_val:
            gdl_config.set(("extractor",), "proxy", proxy_val)
            gdl_config.set(("downloader",), "proxy", proxy_val)

        task_url = (url or getattr(self, "_task_url", "")).lower()
        sites = config.get("site_settings", {})
        custom_sites = {
            cs.get("key", ""): cs.get("domain", "")
            for cs in config.get("custom_sites", [])
            if isinstance(cs, dict)
        }

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
            elif final_cookie.lower() in (
                "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "safari", "chromium"
            ):
                gdl_config.set(("extractor",), "cookies-from-browser", final_cookie.lower())
                gdl_config.set(("downloader",), "cookies-from-browser", final_cookie.lower())

    def _configure_gdl_base(self, gdl_config, url: str) -> None:
        from src.config import config
        if config.is_portable():
            from src.config import _get_app_root
            gdl_cache = _get_app_root() / "data" / "gdl_cache"
            gdl_cache.mkdir(parents=True, exist_ok=True)
            gdl_config.set(("cache",), "file", str(gdl_cache / "cache.sqlite3"))

        gdl_config.set(("extractor",), "user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        gdl_config.set(("downloader",), "user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
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

        # Blocklist implementation: use gallery-dl's extractor.blacklist config key
        # to block certain child extractor domains.
        # Only applies when the main task URL is NOT from that domain.
        blocklist = config.get("blocklist", [])
        if blocklist:
            url_lower = url.lower()
            blocked_names = []
            for b in blocklist:
                bl_lower = str(b).lower().strip()
                if not bl_lower or bl_lower in url_lower:
                    continue  # Skip if this is the main URL's domain
                extractor_name = bl_lower.split(".")[0].replace("-", "")
                if extractor_name:
                    blocked_names.append(extractor_name)
            if blocked_names:
                gdl_config.set(("extractor",), "blacklist", blocked_names)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        _ensure_thread_local_config()
        try:
            import gallery_dl  # noqa: F401
            from gallery_dl import config as gdl_config
            from gallery_dl.extractor import find as find_extractor
        except ImportError as exc:
            raise ExtractionError("gallery-dl is not installed. Run: pip install gallery-dl") from exc

        gdl_config.clear()
        self._configure_gdl_base(gdl_config, url)
        self._configure_cookies_and_proxy(gdl_config, url)

        extractor = find_extractor(url)
        if extractor is None:
            raise ExtractionError(f"gallery-dl cannot handle: {url}")

        from gallery_dl.extractor.common import Message as GDLMessage
        MSG_DIRECTORY = GDLMessage.Directory
        MSG_URL = GDLMessage.Url
        MSG_QUEUE = getattr(GDLMessage, "Queue", 6)

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
                        title = (
                            directory_data.get("thread", {}).get("title", "")
                            or directory_data.get("gallery", {}).get("title", "")
                            or directory_data.get("album", {}).get("title", "")
                            or directory_data.get("title", "")
                        )
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
                    if len(items) >= 20 or (time.time() - start_scan) > 5.0:
                        break
                elif msg_type == MSG_QUEUE:
                    queued_url = msg[1] if len(msg) > 1 else ""
                    queued_meta = msg[2] if len(msg) > 2 else {}
                    if not title and isinstance(queued_meta, dict):
                        if "thread" in queued_meta and "title" in queued_meta["thread"]:
                            title = queued_meta["thread"]["title"]
                        elif "gallery" in queued_meta and "title" in queued_meta["gallery"]:
                            title = queued_meta["gallery"]["title"]
                    if queued_url:
                        if isinstance(queued_meta, dict):
                            fname = (
                                queued_meta.get("description", "")
                                or queued_meta.get("title", "")
                                or Path(queued_url).stem
                            )
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
                raise ExtractionError(
                    "gallery-dl bu linke erişirken 403 (Giriş/Cookie Gerekli) engeline takıldı: "
                    "'simpcity.cr' gibi sitelerdeki gizli/üyelik gerektiren konuları indirebilmek için "
                    "'Programlar ve Siteler' ayarlarında bu siteye (veya 'Diğer Siteler'e) "
                    "'Cookies Dosya Yolu' olarak kullandığınız tarayıcı adını "
                    "(Örn: chrome veya edge) ya da cookies.txt dosyanızı girmelisiniz."
                )
            raise ExtractionError(
                "gallery-dl bu linkten hiçbir içerik/metadata bulamadı veya 403 engeline takıldı."
            )

        is_playlist = len(items) > 1
        # Use a domain favicon as thumbnail for forum/gallery sites (actual CDN URLs often blocked by CORS)
        thumb_url = None
        try:
            import urllib.parse as _up
            parsed = _up.urlparse(url)
            thumb_url = f"https://www.google.com/s2/favicons?domain={parsed.netloc}&sz=128"
        except Exception:
            pass
        return {
            "title": title or self._title_from_url(url),
            "thumbnail": thumb_url,
            "duration": None,
            "formats": [{"format_id": "original", "ext": "mixed", "quality": "original", "filesize": 0}],
            "is_playlist": is_playlist,
            "item_count": len(items),
            "playlist_items": items,
        }

    # ------------------------------------------------------------------
    # Download — runs gallery-dl in an isolated subprocess per task
    # ------------------------------------------------------------------

    def download(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Download via gallery-dl subprocess — fully isolated per concurrent download.

        Each call spawns a dedicated Python process so concurrent downloads
        never share gallery-dl's global state (pathfmt, config dict, extractor
        instances, etc.).  A file-watcher thread moves files to *dest*
        individually as soon as gallery-dl finishes writing them.
        """
        import sys, json, subprocess, tempfile

        if cancel_event is None:
            cancel_event = threading.Event()

        # ---- Final destination directory ----
        dest = Path(output_path).resolve()
        dest.mkdir(parents=True, exist_ok=True)

        # ---- Unique temp directory per task ----
        _uid = task_id or uuid.uuid4().hex[:12]
        try:
            _appdata_local = Path(
                os.environ.get("LOCALAPPDATA", "") or str(Path.home() / "AppData" / "Local")
            )
            temp_base = _appdata_local / "Programs" / "SuyliosDownloader" / "TempDownloads"
        except Exception:
            temp_base = Path(os.environ.get("TEMP", "/tmp")) / "SuyliosDownloader" / "TempDownloads"
        temp_dir = temp_base / _uid
        temp_dir.mkdir(parents=True, exist_ok=True)

        last_file = ""
        downloaded_count = 0
        files_before = set(os.listdir(dest)) if dest.exists() else set()

        # ---- Build gallery-dl JSON config ----
        try:
            from src.config import config as app_config
        except Exception:
            app_config = {}  # type: ignore[assignment]

        UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        gdl_cfg: dict = {
            "extractor": {
                "base-directory": str(temp_dir),
                "directory": [],
                "filename": "{filename}.{extension}",
                "sleep-request": 0.5,
                "sleep": 0.3,
                "retries": 1,
                "timeout": 7,
                "verify": False,
                "user-agent": UA,
            },
            "downloader": {
                "retries": 1,
                "timeout": 7,
                "verify": False,
                "user-agent": UA,
            },
            "output": {"mode": "null"},
        }
        for _sub in (
            "turbo", "filester", "bunkr", "imgbox", "cyberdrop",
            "reddit", "imgur", "pixeldrain", "simpcity", "goonbox",
            "hizliresim", "rule34", "gelbooru", "danbooru",
        ):
            gdl_cfg["extractor"][_sub] = {"directory": []}

        plugins_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "gdl_plugins"))
        if os.path.exists(plugins_dir):
            gdl_cfg["extractor"]["module-sources"] = [plugins_dir]

        if "gofile.io" in url:
            gdl_cfg["extractor"]["gofile"] = {"retries": 0, "timeout": 5}

        # Blocklist: Use gallery-dl's "blacklist" config to block child extractor domains.
        # "extractor.blacklist" is the correct gallery-dl config key that prevents
        # certain extractors from being invoked for child/queued URLs.
        # Only applies when the main URL is NOT from the blocked domain.
        try:
            blocklist = app_config.get("blocklist", []) or []  # type: ignore[union-attr]
            url_lower_bl = url.lower()
            blocked_extractors = []
            for bl_entry in blocklist:
                bl_lower = str(bl_entry).lower().strip()
                if not bl_lower:
                    continue
                # If the main URL itself contains this keyword, skip — user wants to download it directly
                if bl_lower in url_lower_bl:
                    continue
                # Derive gallery-dl extractor name from domain keyword
                # e.g. "tiktok.com" -> "tiktok", "instagram.com" -> "instagram"
                extractor_name = bl_lower.split(".")[0].replace("-", "")
                if extractor_name:
                    blocked_extractors.append(extractor_name)
            if blocked_extractors:
                # gallery-dl's correct config key to block child extractors by category
                gdl_cfg["extractor"]["blacklist"] = blocked_extractors
        except Exception:
            pass

        # Cookies
        task_url_lower = url.lower()
        sites: dict = {}
        try:
            sites = app_config.get("site_settings", {}) or {}  # type: ignore[union-attr]
        except Exception:
            pass
        custom_sites: dict = {}
        try:
            custom_sites = {
                cs.get("key", ""): cs.get("domain", "")
                for cs in (app_config.get("custom_sites", []) or [])  # type: ignore[union-attr]
                if isinstance(cs, dict)
            }
        except Exception:
            pass

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
            url_stripped = task_url_lower.replace("_", "").replace(".", "").replace("-", "")
            if (
                match_key in task_url_lower
                or (domain_key and domain_key.lower() in task_url_lower)
                or key_dotted in task_url_lower
                or (len(key_stripped) > 3 and key_stripped in url_stripped)
            ):
                best_cookie = cookies_val
                break

        final_cookie = best_cookie or other_cookie
        if final_cookie:
            if Path(final_cookie).is_file():
                gdl_cfg["extractor"]["cookies"] = final_cookie
                gdl_cfg["downloader"]["cookies"] = final_cookie
            elif final_cookie.lower() in (
                "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "safari", "chromium"
            ):
                gdl_cfg["extractor"]["cookies-from-browser"] = final_cookie.lower()

        # Proxy
        try:
            proxy_val = app_config.get("proxy", "") or ""  # type: ignore[union-attr]
            if proxy_val:
                gdl_cfg["extractor"]["proxy"] = proxy_val
                gdl_cfg["downloader"]["proxy"] = proxy_val
        except Exception:
            pass

        # Cache (portable mode)
        try:
            from src.config import _get_app_root
            if app_config.is_portable():  # type: ignore[union-attr]
                gdl_cache = _get_app_root() / "data" / "gdl_cache"
                gdl_cache.mkdir(parents=True, exist_ok=True)
                gdl_cfg["cache"] = {"file": str(gdl_cache / "cache.sqlite3")}
        except Exception:
            pass

        # Write config to temp JSON file
        cfg_path = ""
        try:
            cfg_fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="suylios_gdl_")
            with os.fdopen(cfg_fd, "w", encoding="utf-8") as fh:
                json.dump(gdl_cfg, fh, ensure_ascii=False)
        except Exception:
            cfg_path = ""

        # ---- File-watcher: move completed files to dest immediately ----
        _watcher_stop = threading.Event()
        _known_in_temp: set = set()
        # Speed tracking
        _speed_window: list = []  # list of (timestamp, bytes)
        _speed_lock = threading.Lock()

        def _calc_speed() -> float:
            """Return approximate download speed (bytes/sec) based on last 5s window."""
            now = time.monotonic()
            with _speed_lock:
                # Keep only last 5 seconds
                cutoff = now - 5.0
                while _speed_window and _speed_window[0][0] < cutoff:
                    _speed_window.pop(0)
                if len(_speed_window) < 2:
                    return 0.0
                total_bytes = sum(b for _, b in _speed_window)
                elapsed = _speed_window[-1][0] - _speed_window[0][0]
                return total_bytes / elapsed if elapsed > 0 else 0.0

        def _move_new_files() -> None:
            nonlocal downloaded_count, last_file
            while not _watcher_stop.is_set():
                try:
                    if temp_dir.exists():
                        for f in list(temp_dir.rglob("*")):
                            if not f.is_file():
                                continue
                            key = str(f)
                            if key in _known_in_temp:
                                continue
                            # Skip files still being written
                            if f.suffix.lower() in (".part", ".tmp", ".ytdl", ".download"):
                                continue
                            
                            # Skip yt-dlp intermediate dash/format streams (.fdash-xxx.mp4, .f137.mp4) before ffmpeg merges them
                            if re.search(r'\.f(?:dash-)?\w+\.(?:mp4|m4a|webm|mp3|mkv)$', f.name, re.IGNORECASE):
                                continue
                            _known_in_temp.add(key)
                            try:
                                file_size = 0
                                try:
                                    file_size = f.stat().st_size
                                except Exception:
                                    pass
                                dst = dest / f.name
                                if dst.exists():
                                    stem, suffix = f.stem, f.suffix
                                    ctr = 1
                                    while dst.exists():
                                        dst = dest / f"{stem}_{ctr}{suffix}"
                                        ctr += 1
                                shutil.move(str(f), str(dst))
                                last_file = str(dst)
                                downloaded_count += 1
                                # Record bytes for speed calculation
                                if file_size > 0:
                                    with _speed_lock:
                                        _speed_window.append((time.monotonic(), file_size))
                                logger.debug("Watcher moved: %s -> %s", f.name, dst)
                                if progress_hook:
                                    try:
                                        progress_hook({
                                            "status": "finished",
                                            "filename": str(dst),
                                            "file_size": file_size,
                                            "item_index": downloaded_count,
                                            "item_count": -1,  # -1 = unknown total, show counter only
                                            "speed": _calc_speed(),
                                        })
                                    except Exception:
                                        pass
                            except Exception as mv_err:
                                logger.debug("Watcher move failed (retry next tick): %s", mv_err)
                                _known_in_temp.discard(key)
                except Exception:
                    pass
                _watcher_stop.wait(0.4)

        watcher = threading.Thread(
            target=_move_new_files, daemon=True, name=f"gdl-watcher-{_uid}"
        )
        watcher.start()

        try:
            cmd = [sys.executable, "-m", "gallery_dl", "--config-ignore"]
            plugins_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "gdl_plugins"))
            if os.path.exists(plugins_dir):
                cmd += ["-X", plugins_dir]
            if cfg_path and os.path.exists(cfg_path):
                cmd += ["--config-json", cfg_path]
            cmd.append(url)
            logger.debug("gallery-dl subprocess: %s", " ".join(cmd))

            creation_flags = 0
            if os.name == "nt":
                # CREATE_NO_WINDOW: no console window
                # NOT CREATE_NEW_PROCESS_GROUP so CTRL+C propagates
                creation_flags = subprocess.CREATE_NO_WINDOW

            proc_env = os.environ.copy()
            proc_env["PYTHONWARNINGS"] = "ignore"
            blocklist_cfg = app_config.get("blocklist", []) or []
            if isinstance(blocklist_cfg, list):
                proc_env["SUYLIOS_BLOCKLIST"] = ",".join(blocklist_cfg)
            elif isinstance(blocklist_cfg, str):
                proc_env["SUYLIOS_BLOCKLIST"] = blocklist_cfg

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
                env=proc_env,
            )

            # Register so app shutdown / cancel can kill this proc
            _register_proc(_uid, proc)

            # Cancel-watcher: terminates proc the moment cancel_event fires,
            # even if the stdout loop is blocked waiting for output.
            _proc_done = threading.Event()

            def _cancel_watcher_fn() -> None:
                while not _proc_done.is_set():
                    if cancel_event.is_set():
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        return
                    _proc_done.wait(0.3)

            _cancel_thread = threading.Thread(
                target=_cancel_watcher_fn, daemon=True, name=f"gdl-cancel-{_uid}"
            )
            _cancel_thread.start()

            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        logger.debug("gdl[%s]: %s", _uid[:8], line)
                        if progress_hook and (line.startswith("#") or "downloading " in line.lower()):
                            try:
                                progress_hook({
                                    "status": "downloading",
                                    "item_index": downloaded_count,
                                    "item_count": -1,
                                    "speed": _calc_speed(),
                                })
                            except Exception:
                                pass
                    if cancel_event.is_set():
                        break  # cancel-watcher already called proc.terminate()

                rc = proc.wait()
                logger.debug("gallery-dl exited with code %d (task %s)", rc, _uid)
            finally:
                _proc_done.set()
                _cancel_thread.join(timeout=2)

            if cancel_event.is_set():
                raise ExtractionCancelled("gallery-dl download was cancelled")

        except ExtractionCancelled:
            raise
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(f"gallery-dl subprocess failed: {exc}") from exc
        finally:
            # Unregister from global tracker
            _unregister_proc(_uid, proc)

            # Stop the file watcher AFTER a small delay to let it finish its last tick
            time.sleep(0.5)  # Give watcher one more tick to catch last files
            _watcher_stop.set()
            watcher.join(timeout=3)

            # Final sweep: move any files the watcher missed (race condition between
            # gallery-dl writing its last files and watcher's 0.4s poll interval)
            if temp_dir.exists():
                for f in list(temp_dir.rglob("*")):
                    if not f.is_file():
                        continue
                    if re.search(r'\.f(?:dash-)?\w+\.(?:mp4|m4a|webm|mp3|mkv)$', f.name, re.IGNORECASE):
                        continue
                    if f.suffix.lower() in (".part", ".tmp", ".ytdl", ".download"):
                        continue
                    if str(f) in _known_in_temp:
                        continue
                    try:
                        file_size = 0
                        try:
                            file_size = f.stat().st_size
                        except Exception:
                            pass
                        dst = dest / f.name
                        if dst.exists():
                            stem, suffix = f.stem, f.suffix
                            ctr = 1
                            while dst.exists():
                                dst = dest / f"{stem}_{ctr}{suffix}"
                                ctr += 1
                        shutil.move(str(f), str(dst))
                        last_file = str(dst)
                        downloaded_count += 1
                        if file_size > 0:
                            with _speed_lock:
                                _speed_window.append((time.monotonic(), file_size))
                        logger.debug("Final sweep moved: %s -> %s", f.name, dst)
                        if progress_hook:
                            try:
                                progress_hook({
                                    "status": "finished",
                                    "filename": str(dst),
                                    "file_size": file_size,
                                    "item_index": downloaded_count,
                                    "item_count": -1,
                                    "speed": _calc_speed(),
                                })
                            except Exception:
                                pass
                    except Exception as fsweep_err:
                        logger.debug("Final sweep move failed: %s", fsweep_err)

            # Clean up temp directory
            if temp_dir.exists():
                try:
                    shutil.rmtree(str(temp_dir), ignore_errors=True)
                    logger.debug("Cleaned up temp dir: %s", temp_dir)
                except Exception as rm_err:
                    logger.warning("Could not remove temp dir %s: %s", temp_dir, rm_err)

            # Clean up temp config file
            if cfg_path and os.path.exists(cfg_path):
                try:
                    os.unlink(cfg_path)
                except Exception:
                    pass

        try:
            files_after = set(os.listdir(dest)) if dest.exists() else set()
            actual_new = [f for f in (files_after - files_before) if not f.startswith(".") and f not in ("AppData", ".suylios_cdl_data", "Cache", "Configs")]
            if len(actual_new) > downloaded_count:
                downloaded_count = len(actual_new)
        except Exception:
            pass

        if progress_hook:
            try:
                progress_hook({
                    "status": "finished",
                    "filename": last_file or str(dest),
                    "item_index": downloaded_count,
                    "item_count": -1,
                    "speed": 0.0,
                })
            except Exception:
                pass

        return last_file or str(dest)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _title_from_url(url: str) -> str:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            name = unquote(parts[-1])
            if name.endswith(".html"):
                name = name[:-5]
            if name.endswith(".htm"):
                name = name[:-4]
            if "." in name and name.split(".")[-1].isdigit():
                name = name.rsplit(".", 1)[0]
            name = name.replace("-", " ").strip()
            return name
        return parsed.hostname or "gallery"
