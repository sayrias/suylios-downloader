"""
Suylios Downloader – Reddit Extractor.

Handles Reddit gallery posts (multiple images/gifs) and video posts
by directly accessing the Reddit JSON API with proper browser headers.
This completely bypasses gallery-dl (403) and yt-dlp (infinite redirect loop).
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from src.extractors.base_extractor import BaseExtractor, ExtractionCancelled, ExtractionError
from src.extractors.fast_downloader import _wait_if_paused

logger = logging.getLogger(__name__)

# Reddit API'ye erişirken kullanılacak tarayıcı başlıkları
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


class RedditExtractor(BaseExtractor):
    """Reddit-specific extractor for gallery and video posts."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._img_sessions: list[requests.Session] = []
        self._img_pool_lock = threading.Lock()

    def _borrow_img_session(self) -> requests.Session:
        lock = getattr(self, "_img_pool_lock", None)
        if lock is not None:
            with lock:
                if self._img_sessions:
                    return self._img_sessions.pop()
        sess = requests.Session()
        # Resim indirirken Accept: text/html göndermiyoruz, aksi takdirde Reddit bizi HTML sarmalayıcısına yönlendirir
        sess.headers.update({"User-Agent": _HEADERS["User-Agent"]})
        return sess

    def _return_img_session(self, sess: requests.Session) -> None:
        lock = getattr(self, "_img_pool_lock", None)
        if lock is not None:
            with lock:
                if len(self._img_sessions) < 4:
                    self._img_sessions.append(sess)
                    return
        try:
            sess.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    @staticmethod
    def can_handle(url: str) -> bool:
        """Return True for Reddit URLs."""
        lower = url.lower()
        return "reddit.com" in lower or "redd.it" in lower

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_post_id(url: str) -> str | None:
        """Extract the Reddit post ID from various URL formats."""
        # /comments/POST_ID/...
        m = re.search(r"/comments/([a-zA-Z0-9]+)", url)
        if m:
            return m.group(1)
        # /gallery/POST_ID
        m = re.search(r"/gallery/([a-zA-Z0-9]+)", url)
        if m:
            return m.group(1)
        # redd.it/POST_ID
        m = re.search(r"redd\.it/([a-zA-Z0-9]+)", url)
        if m:
            return m.group(1)
        return None

    def _fetch_post_json(self, post_id: str) -> dict[str, Any]:
        """Fetch post data from Reddit JSON API."""
        session = getattr(self, "_session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(_HEADERS)
            self._session = session

        urls_to_try = [
            f"https://old.reddit.com/comments/{post_id}/.json?limit=0&raw_json=1",
            f"https://www.reddit.com/comments/{post_id}/.json?limit=0&raw_json=1",
        ]
        last_err = None

        def _try_json_urls() -> Optional[dict[str, Any]]:
            nonlocal last_err
            for api_url in urls_to_try:
                try:
                    resp = session.get(api_url, timeout=15, allow_redirects=True)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            children = data[0].get("data", {}).get("children", [])
                            if children:
                                return children[0].get("data", {})
                        elif isinstance(data, dict):
                            children = data.get("data", {}).get("children", [])
                            if children:
                                return children[0].get("data", {})
                    else:
                        last_err = f"HTTP {resp.status_code}"
                        logger.warning("Reddit API %s returned %s", api_url, resp.status_code)
                except Exception as exc:
                    last_err = str(exc)
                    logger.warning("Reddit API error for %s: %s", api_url, exc)
            return None

        # Önce doğrudan .json uç noktalarını dene (tam HTML GET maliyetinden kaçın)
        post_data = _try_json_urls()
        if post_data is not None:
            return post_data

        # 403 vb. engellerde fallback olarak HTML sayfasından cookie/session alıp tekrar dene
        try:
            session.get(f"https://old.reddit.com/comments/{post_id}/", timeout=15)
        except Exception as exc:
            logger.debug("Reddit session pre-fetch failed: %s", exc)
        else:
            post_data = _try_json_urls()
            if post_data is not None:
                return post_data

        raise ExtractionError(f"Reddit API erişilemedi (post: {post_id}): {last_err}")

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        """Extract metadata from a Reddit post."""
        post_id = self._extract_post_id(url)
        if not post_id:
            raise ExtractionError(f"Reddit post ID bulunamadı: {url}")

        post_data = self._fetch_post_json(post_id)
        title = post_data.get("title", f"reddit_{post_id}")
        is_gallery = post_data.get("is_gallery", False)
        is_video = post_data.get("is_video", False)

        items: list[dict[str, Any]] = []

        if is_gallery:
            # Galeri postu – media_metadata'dan resim/gif URL'lerini çıkar
            media_metadata = post_data.get("media_metadata", {})
            gallery_data = post_data.get("gallery_data", {})
            gallery_items = gallery_data.get("items", [])

            # gallery_data sıralı olarak öğeleri verir
            for gi in gallery_items:
                media_id = gi.get("media_id", "")
                meta = media_metadata.get(media_id, {})
                if meta.get("status") != "valid":
                    continue

                mime = meta.get("m", "image/jpeg")
                ext = mime.split("/")[-1] if "/" in mime else "jpg"
                if ext == "jpeg":
                    ext = "jpg"

                # En yüksek çözünürlüklü URL
                source = meta.get("s", {})
                img_url = source.get("u") or source.get("gif") or ""
                # Reddit URL encoding: &amp; -> &
                img_url = img_url.replace("&amp;", "&")

                if img_url:
                    items.append({
                        "title": f"{media_id}.{ext}",
                        "url": img_url,
                        "duration": None,
                        "thumbnail": img_url,
                    })

            if not items and media_metadata:
                # gallery_data boşsa, media_metadata'dan doğrudan çıkar
                for media_id, meta in media_metadata.items():
                    if meta.get("status") != "valid":
                        continue
                    mime = meta.get("m", "image/jpeg")
                    ext = mime.split("/")[-1] if "/" in mime else "jpg"
                    if ext == "jpeg":
                        ext = "jpg"
                    source = meta.get("s", {})
                    img_url = source.get("u") or source.get("gif") or ""
                    img_url = img_url.replace("&amp;", "&")
                    if img_url:
                        items.append({
                            "title": f"{media_id}.{ext}",
                            "url": img_url,
                            "duration": None,
                            "thumbnail": img_url,
                        })

        elif is_video:
            # Video postu – bu durumda yt-dlp'ye devredilecek (download aşamasında)
            video_url = ""
            media = post_data.get("media", {})
            rv = media.get("reddit_video", {})
            video_url = rv.get("fallback_url", "")
            duration = rv.get("duration")

            if video_url:
                items.append({
                    "title": title,
                    "url": video_url,
                    "duration": duration,
                    "thumbnail": post_data.get("thumbnail"),
                })
        else:
            # Tek resim/gif postu
            img_url = post_data.get("url_overridden_by_dest", "") or post_data.get("url", "")
            if img_url and ("i.redd.it" in img_url or "i.imgur" in img_url or
                           img_url.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"))):
                ext = Path(urlparse(img_url).path).suffix or ".jpg"
                items.append({
                    "title": f"{post_id}{ext}",
                    "url": img_url,
                    "duration": None,
                    "thumbnail": img_url,
                })

        # Tüm yollar tükendiyse, "_reddit_is_video" flag'ini sakla (download'da yt-dlp fallback)
        self._post_data = post_data
        self._is_video = is_video
        self._is_gallery = is_gallery

        is_playlist = len(items) > 1

        return {
            "title": title,
            "thumbnail": items[0].get("thumbnail") if items else post_data.get("thumbnail"),
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": "mixed" if is_gallery else "mp4" if is_video else "jpg",
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
        """Download Reddit content."""
        is_video = getattr(self, "_is_video", False)

        # Video postları için yt-dlp'ye devret
        if is_video:
            return self._download_via_ytdlp(url, output_path, format_id, progress_hook, **kwargs)

        # Galeri ve tekli resim postları için doğrudan indirme
        return self._download_images(
            url, output_path, progress_hook,
            cancel_event=kwargs.get("cancel_event"),
            pause_event=kwargs.get("pause_event"),
        )

    def _download_images(
        self,
        url: str,
        output_path: str,
        progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
        cancel_event: Optional[Any] = None,
        pause_event: Optional[Any] = None,
    ) -> str:
        """Download gallery images directly via requests."""
        post_id = self._extract_post_id(url) or "reddit"
        post_data = getattr(self, "_post_data", None)

        if not post_data:
            post_data = self._fetch_post_json(post_id)

        title = post_data.get("title", post_id)
        # Klasör adı olarak başlığı temizle
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)[:100].strip(". ")
        if not safe_title:
            safe_title = post_id

        dest = Path(output_path) / safe_title
        dest.mkdir(parents=True, exist_ok=True)

        is_gallery = post_data.get("is_gallery", False)
        images: list[tuple[str, str]] = []  # (url, filename)

        if is_gallery:
            media_metadata = post_data.get("media_metadata", {})
            gallery_data = post_data.get("gallery_data", {})
            gallery_items = gallery_data.get("items", [])

            order = [gi.get("media_id", "") for gi in gallery_items] if gallery_items else list(media_metadata.keys())

            for idx, media_id in enumerate(order, 1):
                meta = media_metadata.get(media_id, {})
                if meta.get("status") != "valid":
                    continue
                mime = meta.get("m", "image/jpeg")
                ext = mime.split("/")[-1] if "/" in mime else "jpg"
                if ext == "jpeg":
                    ext = "jpg"
                source = meta.get("s", {})
                img_url = source.get("u") or source.get("gif") or ""
                img_url = img_url.replace("&amp;", "&")
                if img_url:
                    images.append((img_url, f"{idx:03d}_{media_id}.{ext}"))
        else:
            # Tekli resim
            img_url = post_data.get("url_overridden_by_dest", "") or post_data.get("url", "")
            if img_url:
                ext = Path(urlparse(img_url).path).suffix or ".jpg"
                images.append((img_url, f"{post_id}{ext}"))

        if not images:
            raise ExtractionError("Reddit postundan indirilebilecek medya bulunamadı.")

        if pause_event is None and progress_hook is not None:
            task_obj = getattr(progress_hook, "_task", None)
            if task_obj is not None:
                pause_event = getattr(task_obj, "_pause_event", None)

        total = len(images)
        lock = threading.Lock()
        stop_flag = threading.Event()
        state: dict[str, Any] = {
            "downloaded_bytes": 0,
            "completed": 0,
            "last_file": "",
            "last_update": time.monotonic(),
            "last_bytes": 0,
            "speed": 0.0,
        }

        def _download_one(img_url: str, filename: str) -> None:
            if stop_flag.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise ExtractionCancelled("İndirme iptal edildi.")
            _wait_if_paused(pause_event, cancel_event)
            filepath = dest / filename
            sess = self._borrow_img_session()
            try:
                with sess.get(img_url, timeout=30, stream=True) as resp:
                    resp.raise_for_status()
                    with open(filepath, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=131072):
                            if stop_flag.is_set() or (cancel_event is not None and cancel_event.is_set()):
                                raise ExtractionCancelled("İndirme iptal edildi.")
                            _wait_if_paused(pause_event, cancel_event)
                            if not chunk:
                                continue
                            f.write(chunk)
                            chunk_len = len(chunk)
                            hook_payload = None
                            with lock:
                                state["downloaded_bytes"] += chunk_len
                                if progress_hook:
                                    now = time.monotonic()
                                    time_diff = now - state["last_update"]
                                    if time_diff > 0.25:
                                        sp = (state["downloaded_bytes"] - state["last_bytes"]) / time_diff
                                        state["speed"] = sp
                                        state["last_update"] = now
                                        state["last_bytes"] = state["downloaded_bytes"]
                                        cur_idx = min(total, state["completed"] + 1)
                                        hook_payload = {
                                            "status": "downloading",
                                            "item_index": cur_idx,
                                            "item_count": total,
                                            "fragment_index": cur_idx,
                                            "fragment_count": total,
                                            "downloaded_bytes": state["downloaded_bytes"],
                                            "total_bytes": 0,
                                            "speed": sp,
                                            "eta": 0,
                                            "filename": str(filepath),
                                        }
                            if hook_payload is not None and progress_hook:
                                progress_hook(hook_payload)

                hook_payload = None
                with lock:
                    state["completed"] += 1
                    state["last_file"] = str(filepath)
                    done_cnt = state["completed"]
                    if progress_hook:
                        now = time.monotonic()
                        time_diff = now - state["last_update"]
                        if time_diff > 0.1:
                            state["speed"] = (state["downloaded_bytes"] - state["last_bytes"]) / time_diff
                            state["last_update"] = now
                            state["last_bytes"] = state["downloaded_bytes"]
                        hook_payload = {
                            "status": "downloading",
                            "item_index": done_cnt,
                            "item_count": total,
                            "fragment_index": done_cnt,
                            "fragment_count": total,
                            "downloaded_bytes": state["downloaded_bytes"],
                            "total_bytes": 0,
                            "speed": state["speed"],
                            "eta": 0,
                            "filename": str(filepath),
                        }
                logger.info("Downloaded %d/%d: %s", done_cnt, total, filename)
                if hook_payload is not None and progress_hook:
                    progress_hook(hook_payload)
            except ExtractionCancelled:
                stop_flag.set()
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            except Exception as exc:
                if exc.__class__.__name__ == "_CancelledError" or (
                    cancel_event is not None and cancel_event.is_set()
                ):
                    stop_flag.set()
                    try:
                        filepath.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise ExtractionCancelled("İndirme iptal edildi.") from exc
                logger.warning("Failed to download %s: %s", img_url, exc)
            finally:
                self._return_img_session(sess)

        with ThreadPoolExecutor(max_workers=min(3, total)) as pool:
            futs = [pool.submit(_download_one, img_url, filename) for img_url, filename in images]
            for fut in futs:
                fut.result()

        if not state["last_file"]:
            raise ExtractionError("Reddit galeri indirmesi başarısız – hiçbir dosya indirilemedi.")

        if progress_hook:
            progress_hook({
                "status": "finished",
                "filename": str(dest),
            })

        return str(dest)

    def _download_via_ytdlp(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
        **kwargs: Any,
    ) -> str:
        """Fallback to yt-dlp for video posts."""
        from src.extractors.ytdlp_ext import YtdlpExtractor
        ytdlp = YtdlpExtractor()
        ytdlp._task_quality = getattr(self, "_task_quality", "best")
        ytdlp._task_url = url
        ytdlp._site_settings = getattr(self, "_site_settings", {})
        return ytdlp.download(
            url=url,
            output_path=output_path,
            format_id=format_id,
            progress_hook=progress_hook,
            **kwargs,
        )
