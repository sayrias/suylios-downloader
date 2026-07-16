"""
Suylios Downloader - Async Download Task Manager.

Manages a pool of concurrent downloads, each represented as a *task*
with observable state transitions.  Thread-safe for use from both the
PyWebView Bridge thread and internal worker threads.
"""

import enum
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Callable, Optional

from src.config import config
from src.extractors.base_extractor import ExtractionCancelled
from src.extractors.gallery_ext import kill_all_gallery_dl_procs, kill_procs_for_task, _ACTIVE_PROCS, _ACTIVE_PROCS_LOCK

logger = logging.getLogger(__name__)


def _kill_proc_for_task(task_id: str) -> None:
    """Immediately kill any active subprocesses (gallery-dl, cyberdrop-dl, ffmpeg) for a specific task."""
    kill_procs_for_task(task_id)


def format_user_error(err: Any) -> str:
    """Format and translate exceptions into clean Turkish messages for the UI."""
    s = str(err)
    if "💡 Çözüm Bilgisi:" in s:
        return s[:600]
    # gallery-dl specific errors
    if "gallery-dl bu linke erişirken 403" in s or "gallery-dl indirme yaparken 403" in s:
        return s[:600]
    if "gallery-dl bu linkten hiçbir içerik" in s or "gallery-dl cannot handle" in s:
        return "⚠️ Bu URL gallery-dl tarafından desteklenmiyor veya içerik erişilemiyor durumda."
    if "gallery-dl download failed" in s:
        inner = s.replace("gallery-dl download failed: ", "").strip()
        if inner:
            return f"⚠️ İndirme hatası: {inner[:200]}"
        return "⚠️ gallery-dl indirme sırasında beklenmedik bir hata oluştu."
    if "SyntaxError" in s or "expected 'except' or 'finally'" in s:
        return "⚠️ Dahili bir uygulama hatası oluştu. Lütfen uygulamayı yeniden başlatın."
    if "Unsupported URL" in s or "No extractor found" in s or "No video formats found" in s:
        return "⚠️ Bu URL desteklenmiyor veya geçerli bir medya/arşiv linki değil."
    if "error-rateLimit" in s or "HTTP Error 429" in s or "Too Many Requests" in s or "rate limit" in s.lower():
        return "⚠️ Sunucu IP adresinize geçici hız sınırı (rate limit) uyguladı. Lütfen birkaç dakika sonra tekrar deneyin veya VPN kullanın."
    if "youtube" in s.lower() or "youtu.be" in s.lower() or "yt-dlp" in s.lower() or "extract_info" in s.lower():
        if "403" in s or "forbidden" in s.lower() or "bot" in s.lower():
            return "⚠️ YouTube erişimi kısıtlandı (HTTP 403). VPN açık ise kapatıp deneyin veya tarayıcı çerezlerinizi (cookies) Ayarlar'dan ekleyin."
    if "Cloudflare" in s or "anti-bot" in s:
        return "⚠️ Bu site Cloudflare / bot koruması kullanıyor. Erişim engellendi."
    if "HTTP Error 403" in s or "403 Forbidden" in s:
        return "⚠️ Sunucu erişimi reddetti (HTTP 403 Forbidden). IP adresiniz engellenmiş veya linkin süresi dolmuş olabilir."
    if "notPremium" in s or "requires a premium account" in s:
        return "⚠️ Bu içeriği indirmek için premium üyelik gereklidir."
    if "notFound" in s or "HTTP Error 404" in s or "Video unavailable" in s or "Private video" in s:
        return "⚠️ İçerik bulunamadı, gizli veya silinmiş."
    if "gofile" in s.lower() or ("api.gofile.io" in s):
        if "notPremium" in s or "error-notPremium" in s or "requires a premium account" in s:
            return "⚠️ Gofile uyarı: Bu dosya yalnızca Gofile Premium hesaplara açıktır veya günlük ücretsiz indirme limitine ulaşılmıştır. Lütfen Ayarlar -> Gofile kısmından Premium API anahtarı ekleyin veya VPN ile IP değiştirin."
        if "notFound" in s or "error-notFound" in s:
            return "⚠️ Gofile uyarı: Bu dosya silinmiş, gizlenmiş veya bağlantı süresi dolmuş."
        if "ratelimit" in s.lower() or "error-ratelimit" in s.lower():
            return "⚠️ Gofile uyarı: Gofile sunucularında indirme sınırına takıldınız. Lütfen birkaç dakika bekleyin veya VPN ile IP değiştirin."
        if "no downloadable files found" in s.lower():
            return "⚠️ Gofile uyarı: Bu klasörde indirilebilir herhangi bir dosya bulunamadı veya link geçersiz."
        if "could not extract websitetoken" in s.lower():
            return "⚠️ Gofile uyarı: Gofile güvenlik doğrulaması aşılamadı. Lütfen VPN kapatıp/açıp tekrar deneyin."
        if ("timed out" in s.lower() or "timeout" in s.lower() or "ConnectTimeout" in s
                or "WinError 10060" in s or "engellenmiş" in s.lower() or "API engellenmiş" in s):
            return (
                "⚠️ Gofile sunucusuna bağlanılamıyor (IP bloğu). "
                "Gofile'ın Cloudflare sistemi IP adresinizi geçici olarak engelledi. "
                "Çözüm: VPN açın veya birkaç saat bekleyin ve tekrar deneyin."
            )
    if "ConnectTimeout" in s or "Connection timed out" in s or "timed out" in s.lower() or "Max retries exceeded" in s:
        return "⚠️ Sunucuya bağlanılamadı veya zaman aşımına uğradı. IP adresiniz sunucu tarafından engellenmiş olabilir (VPN deneyebilirsiniz)."

    for prefix in [
        "Metadata extraction failed: yt-dlp extract_info failed: ERROR: ",
        "Metadata extraction failed: ",
        "yt-dlp extract_info failed: ERROR: ",
        "ERROR: ",
    ]:
        if s.startswith(prefix):
            s = s[len(prefix):]
    # If still only generic/cryptic message, wrap it nicely
    if len(s) < 5 or s in ("None", "False", "True"):
        return "⚠️ Bilinmeyen bir hata oluştu. Lütfen tekrar deneyin."
    return s[:300]


# ======================================================================
# Task model
# ======================================================================

class TaskStatus(str, enum.Enum):
    """Lifecycle states of a download task."""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    CONVERTING = "converting"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    """Mutable state object for a single download."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    url: str = ""
    title: str = ""
    thumbnail: str = ""
    filename: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0          # 0 – 100
    speed: float = 0.0             # bytes / sec
    total_size: int = 0
    downloaded_size: int = 0
    format_type: str = "auto"      # auto | mp3 | mp4 | audio
    quality: str = "best"          # best | 1080 | 720 | 480 | 360
    item_index: int = 0
    item_count: int = 0
    archive_title: str = ""        # original playlist/archive title
    eta: int = 0                   # seconds remaining
    error_message: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # v1.2.0 – Trimming / scheduling / metadata
    start_time: str = ""           # e.g. "01:15:00" for video trimming
    end_time: str = ""             # e.g. "01:25:00" for video trimming
    scheduled_at: int = 0          # Unix timestamp; 0 = start immediately
    embed_metadata: bool = True
    download_subtitles: bool = False
    subtitle_langs: str = "tr,en"  # comma-separated lang codes
    keep_original: bool = False
    compress_archive: bool = False
    compress_format: str = "zip"   # zip | tar | gztar | bztar

    # Internal bookkeeping (not serialised to JS).
    _cancel_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False,
    )
    _pause_event: threading.Event = field(
        default_factory=lambda: _set_event(), repr=False, compare=False,
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot (no internal fields)."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title or self.url,
            "thumbnail": self.thumbnail,
            "filename": self.filename,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "total_size": self.total_size,
            "downloaded_size": self.downloaded_size,
            "format_type": self.format_type,
            "quality": self.quality,
            "item_index": self.item_index,
            "item_count": self.item_count,
            "archive_title": self.archive_title,
            "eta": self.eta,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "scheduled_at": self.scheduled_at,
            "keep_original": self.keep_original,
            "compress_archive": self.compress_archive,
            "compress_format": self.compress_format,
        }


def _set_event() -> threading.Event:
    """Return an *already-set* ``Event`` (download starts unpaused)."""
    e = threading.Event()
    e.set()
    return e


# ======================================================================
# Extractor registry
# ======================================================================

def _get_extractor(url: str):
    """Return the first extractor whose ``can_handle`` matches *url*."""
    # Lazy imports so that optional dependencies don't blow up at import
    # time if they are missing.
    from src.extractors.pixeldrain import PixeldrainExtractor
    from src.extractors.gofile import GofileExtractor
    from src.extractors.bunkr import BunkrExtractor
    from src.extractors.reddit_ext import RedditExtractor
    from src.extractors.gallery_ext import GalleryDLExtractor
    from src.extractors.ytdlp_ext import YtdlpExtractor
    from src.extractors.cyberdrop_ext import CyberdropDLExtractor

    url_lower = url.lower()

    # Reddit linkleri özel RedditExtractor ile işleniyor (gallery-dl 403 ve yt-dlp sonsuz döngü sorunu)
    if "reddit.com" in url_lower or "redd.it" in url_lower:
        logger.debug("Reddit URL detected, using RedditExtractor: %s", url)
        return RedditExtractor()

    # YouTube linklerinde yt-dlp öncelikli
    if "youtu" in url_lower:
        logger.debug("YouTube URL detected, prioritizing YtdlpExtractor: %s", url)
        return YtdlpExtractor()

    # Order matters: specific site extractors first, then gallery-dl, cyberdrop-dl, and finally yt-dlp.
    for cls in (
        GofileExtractor,
        BunkrExtractor,
        PixeldrainExtractor,
        GalleryDLExtractor,
        CyberdropDLExtractor,
        YtdlpExtractor,
    ):
        try:
            if cls.can_handle(url):
                logger.debug("Extractor selected: %s for %s", cls.__name__, url)
                return cls()
        except Exception:
            continue
    # Ultimate fallback
    return YtdlpExtractor()


# ======================================================================
# Download Manager
# ======================================================================

class DownloadManager:
    """Central download orchestrator.

    * Maintains an ordered list of :class:`DownloadTask` objects.
    * Submits work to a :class:`~concurrent.futures.ThreadPoolExecutor`.
    * Provides pause / resume / cancel semantics via ``threading.Event``.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, DownloadTask] = {}
        self._ordered_ids: list[str] = []
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(
            max_workers=1000, thread_name_prefix="dl",
        )
        logger.info("DownloadManager initialised (pool_workers=1000).")

    def update_max_concurrent(self, new_concurrent: int) -> None:
        """Update the max concurrent downloads pool dynamically."""
        with self._lock:
            logger.info("Updating DownloadManager max concurrent preference to %d", new_concurrent)
    # Public API
    # ------------------------------------------------------------------

    def add_task(
        self,
        url: str,
        format_type: str = "auto",
        quality: str = "best",
        start_time: str = "",
        end_time: str = "",
        scheduled_at: int = 0,
        embed_metadata: bool = True,
        download_subtitles: bool = False,
        subtitle_langs: str = "tr,en",
        keep_original: bool = False,
        compress_archive: bool = False,
        compress_format: str = "zip",
    ) -> DownloadTask:
        """Create a new download task and submit it to the worker pool.

        Returns the newly created :class:`DownloadTask`.
        """
        task = DownloadTask(
            url=url,
            format_type=format_type,
            quality=quality,
            start_time=start_time,
            end_time=end_time,
            scheduled_at=scheduled_at,
            embed_metadata=embed_metadata,
            download_subtitles=download_subtitles,
            subtitle_langs=subtitle_langs,
            keep_original=keep_original,
            compress_archive=compress_archive,
            compress_format=compress_format,
        )
        if scheduled_at and scheduled_at > int(__import__('time').time()):
            task.status = TaskStatus.QUEUED
            with self._lock:
                self._tasks[task.id] = task
                self._ordered_ids.append(task.id)
            self._pool.submit(self._fetch_scheduled_metadata, task)
            logger.info("Task %s scheduled for %s: %s", task.id, scheduled_at, url)
        else:
            with self._lock:
                self._tasks[task.id] = task
                self._ordered_ids.append(task.id)
            self._pool.submit(self._run_task, task)
            logger.info("Task %s queued: %s (fmt=%s, q=%s)", task.id, url, format_type, quality)
        return task

    def reorder_tasks(self, ids: list[str]) -> None:
        with self._lock:
            # Sadece mevcut task ID'lerini dikkate al (silinmiş olanlar vs. ayıklanır)
            valid_ids = [i for i in ids if i in self._tasks]
            # Liste tam uyuşuyorsa UI ile senkronize et
            self._ordered_ids = valid_ids
            logger.info("Tasks reordered according to UI. Ordered count: %d", len(valid_ids))

            seq_mode = config.get("sequential_download")
            limit = config.get("concurrent_downloads")
            if seq_mode or limit == 1:
                # Find the top pending/active task
                top_task_id = None
                for tid in self._ordered_ids:
                    t = self._tasks.get(tid)
                    if t and t.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSED):
                        top_task_id = tid
                        break
                
                if top_task_id:
                    for other_tid, other_t in self._tasks.items():
                        if other_tid != top_task_id and getattr(other_t, "_is_actually_downloading", False) and other_t.status == TaskStatus.DOWNLOADING:
                            other_t._pause_event.clear()
                            other_t.status = TaskStatus.PAUSED
                            other_t._is_actually_downloading = False
                            logger.info("Sequential mode reorder: paused lower priority task %s", other_tid)
                    
                    top_t = self._tasks[top_task_id]
                    if top_t.status == TaskStatus.PAUSED:
                        top_t.status = TaskStatus.DOWNLOADING
                        top_t._is_actually_downloading = True
                        top_t._pause_event.set()
                        logger.info("Sequential mode reorder: resumed top priority task %s", top_task_id)


    def add_batch_tasks(
        self,
        urls: list[str],
        format_type: str = "auto",
        quality: str = "best",
        embed_metadata: bool = True,
        download_subtitles: bool = False,
        compress_archive: bool = False,
        compress_format: str = "zip",
    ) -> list[DownloadTask]:
        """Enqueue multiple URLs at once."""
        tasks = []
        for url in urls:
            url = url.strip()
            if url:
                t = self.add_task(
                    url=url,
                    format_type=format_type,
                    quality=quality,
                    embed_metadata=embed_metadata,
                    download_subtitles=download_subtitles,
                    compress_archive=compress_archive,
                    compress_format=compress_format,
                )
                tasks.append(t)
        return tasks

    def get_active_count(self) -> int:
        """Return number of tasks that are actively downloading or queued."""
        with self._lock:
            return sum(
                1 for t in self._tasks.values()
                if t.status in (
                    TaskStatus.QUEUED, TaskStatus.DOWNLOADING,
                    TaskStatus.PAUSED, TaskStatus.CONVERTING,
                )
            )

    def check_scheduled_tasks(self) -> None:
        """Check if any scheduled tasks are due and start them."""
        import time
        now = int(time.time())
        with self._lock:
            due = [
                t for t in self._tasks.values()
                if t.scheduled_at > 0 and t.scheduled_at <= now
                and t.status == TaskStatus.QUEUED
            ]
        for task in due:
            task.scheduled_at = 0
            self._pool.submit(self._run_task, task)
            logger.info("Scheduled task %s starting now.", task.id)

    def pause_task(self, task_id: str) -> bool:
        """Pause a running download.  Returns ``True`` on success."""
        task = self._get(task_id)
        if task and task.status == TaskStatus.DOWNLOADING:
            task._pause_event.clear()
            task.status = TaskStatus.PAUSED
            logger.info("Task %s paused.", task_id)
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused download.  Returns ``True`` on success."""
        task = self._get(task_id)
        if task and task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.DOWNLOADING
            task._pause_event.set()
            logger.info("Task %s resumed.", task_id)
            return True
        return False

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or queued download."""
        task = self._get(task_id)
        if task and task.status in (
            TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSED,
        ):
            task._cancel_event.set()
            task._pause_event.set()          # unblock if paused
            task.status = TaskStatus.CANCELLED
            logger.info("Task %s cancelled.", task_id)
            # Also immediately kill the gallery-dl subprocess if one is running
            _kill_proc_for_task(task_id)
            return True
        return False

    def remove_task(self, task_id: str) -> bool:
        """Remove a task from the list (canceling it if active)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task._cancel_event.set()
                task._pause_event.set()
                del self._tasks[task_id]
                if task_id in self._ordered_ids:
                    self._ordered_ids.remove(task_id)
        # Kill subprocess outside lock to avoid deadlock
        _kill_proc_for_task(task_id)
        return True

    def retry_task(self, task_id: str) -> bool:
        """Reset a completed/error/cancelled task and re-queue it for download."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            # Don't re-queue a task that is already running
            if task.status in (TaskStatus.DOWNLOADING, TaskStatus.CONVERTING):
                return False
            # Reset task state
            task.status = TaskStatus.QUEUED
            task.progress = 0.0
            task.speed = 0.0
            task.total_size = 0
            task.downloaded_size = 0
            task.eta = 0
            task.item_index = 0
            task.item_count = 0
            task.error_message = ""
            task.filename = ""
            task._cancel_event.clear()
            task._pause_event.set()  # ensure unpaused
            task._is_actually_downloading = False
        self._pool.submit(self._run_task, task)
        logger.info("Task %s re-queued for retry.", task_id)
        return True

    def get_all_tasks(self) -> list[dict[str, Any]]:
        """Return a JSON-serialisable list of all task snapshots."""
        with self._lock:
            result = []
            for tid in self._ordered_ids:
                if tid in self._tasks:
                    result.append(self._tasks[tid].to_dict())
            # Fallback for any tasks not in _ordered_ids
            for tid, t in self._tasks.items():
                if tid not in self._ordered_ids:
                    result.append(t.to_dict())
            return result

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        """Return a single task snapshot, or ``None``."""
        task = self._get(task_id)
        return task.to_dict() if task else None

    def shutdown(self) -> None:
        """Gracefully cancel all running tasks and shut down the pool."""
        logger.info("DownloadManager shutting down …")
        with self._lock:
            for task in self._tasks.values():
                if task.status in (
                    TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSED,
                ):
                    task._cancel_event.set()
                    task._pause_event.set()
                    task.status = TaskStatus.CANCELLED
        # Force-kill all gallery-dl subprocesses immediately
        kill_all_gallery_dl_procs()
        self._pool.shutdown(wait=False, cancel_futures=True)
        logger.info("DownloadManager shut down.")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def _fetch_scheduled_metadata(self, task: DownloadTask) -> None:
        """Fetch metadata in background for scheduled task without downloading."""
        try:
            logger.info("Task %s – fetching metadata for scheduled item…", task.id)
            extractor = _get_extractor(task.url)
            extractor._task_quality = task.quality
            extractor._task_url = task.url
            extractor._site_settings = config.get("site_settings", {})
            extractor._app_proxy = config.get("proxy", "")
            info = extractor.extract_info(task.url)
            if info:
                t = info.get("title")
                thumb = info.get("thumbnail")
                if t and t != task.url:
                    task.title = t
                if thumb:
                    task.thumbnail = thumb
                logger.info("Task %s – scheduled metadata OK: %s", task.id, repr(task.title)[:80])
        except Exception as exc:
            logger.warning("Task %s – scheduled metadata fetch error: %s", task.id, exc)

    def _run_task(self, task: DownloadTask) -> None:
        """Worker entry-point executed inside the thread pool."""
        try:
            if task._cancel_event.is_set():
                return

            # ---- 1.  Resolve extractor ----
            logger.info("Task %s – resolving extractor…", task.id)
            try:
                extractor = _get_extractor(task.url)
                extractor._task_quality = task.quality
                extractor._task_url = task.url
                extractor._site_settings = config.get("site_settings", {})
                extractor._app_proxy = config.get("proxy", "")
            except Exception as exc:
                task.status = TaskStatus.ERROR
                task.error_message = format_user_error(exc)
                logger.error("Task %s – extractor error: %s", task.id, exc)
                return

            # ---- 2.  Extract metadata ----
            logger.info("Task %s – extracting metadata…", task.id)
            try:
                task.status = TaskStatus.DOWNLOADING
                tried_extractors = {extractor.__class__}
                try:
                    info = extractor.extract_info(task.url)
                except Exception as ext_err:
                    from src.extractors.gofile import GofileExtractor
                    from src.extractors.bunkr import BunkrExtractor
                    from src.extractors.pixeldrain import PixeldrainExtractor
                    from src.extractors.gallery_ext import GalleryDLExtractor
                    from src.extractors.cyberdrop_ext import CyberdropDLExtractor
                    from src.extractors.ytdlp_ext import YtdlpExtractor

                    fallback_classes = []
                    if "gofile.io" not in task.url.lower():
                        for cls in (GofileExtractor, BunkrExtractor, PixeldrainExtractor, GalleryDLExtractor, CyberdropDLExtractor, YtdlpExtractor):
                            if cls not in tried_extractors and cls.can_handle(task.url):
                                fallback_classes.append(cls)
                        if YtdlpExtractor not in tried_extractors and YtdlpExtractor not in fallback_classes:
                            fallback_classes.append(YtdlpExtractor)
                    else:
                        raise ext_err

                    success = False
                    gdl_auth_error = any(x in str(ext_err) for x in ("AuthRequired", "logged-in", "403", "Giriş/Cookie"))
                    for fb_cls in fallback_classes:
                        tried_extractors.add(fb_cls)
                        logger.warning("Extractor %s extract_info failed (%s), falling back to %s...", extractor.__class__.__name__, ext_err, fb_cls.__name__)
                        try:
                            fb_extractor = fb_cls()
                            fb_extractor._task_quality = task.quality
                            fb_extractor._task_url = task.url
                            fb_extractor._site_settings = config.get("site_settings", {})
                            fb_extractor._app_proxy = config.get("proxy", "")
                            info = fb_extractor.extract_info(task.url)
                            # If the original extractor was GalleryDL and hit an auth error,
                            # keep it for download so cookies are applied properly.
                            if gdl_auth_error and extractor.__class__.__name__ == "GalleryDLExtractor":
                                logger.info("Task %s – keeping GalleryDLExtractor for download despite metadata fallback (auth/cookie flow)", task.id)
                            else:
                                extractor = fb_extractor
                            success = True
                            break
                        except Exception as fb_err:
                            ext_err = fb_err
                            logger.warning("Fallback extractor %s extract_info also failed (%s).", fb_cls.__name__, fb_err)

                    if not success:
                        if "gofile.io" in task.url.lower():
                            raise ext_err
                        # If GalleryDL hit auth error, keep it as the extractor – it will apply cookies on download
                        if gdl_auth_error and extractor.__class__.__name__ == "GalleryDLExtractor":
                            logger.info("Task %s – all metadata extractors failed; keeping GalleryDLExtractor for download with cookies", task.id)
                        info = {"title": task.url.split("/")[-1] or "Download"}
                task.title = info.get("title", task.url)
                task.thumbnail = info.get("thumbnail") or ""
                # Keep a copy of the playlist/archive title so we can restore it on finish
                task.archive_title = task.title
                # Store initial item count from metadata if available
                meta_item_count = info.get("item_count") or 0
                if meta_item_count and meta_item_count > 1:
                    task.item_count = int(meta_item_count)
                logger.info("Task %s – metadata OK: %s", task.id, repr(task.title)[:80])
            except Exception as exc:
                task.status = TaskStatus.ERROR
                task.error_message = format_user_error(exc)
                logger.error("Task %s – extract_info error: %s", task.id, exc, exc_info=True)
                return

            if task._cancel_event.is_set():
                task.status = TaskStatus.CANCELLED
                return

            # ---- 3.  Build output path ----
            try:
                dl_dir = config.get_download_dir()
                if config.get("create_subfolders", True) or config.get("subfolders", True):
                    url_lower = task.url.lower()
                    site_key = None
                    default_folder = None

                    # 1. Check Custom Sites FIRST
                    custom_sites = config.get("custom_sites", [])
                    if custom_sites:
                        url_stripped = task.url.replace("_", "").replace(".", "").replace("-", "")
                        for cs in custom_sites:
                            c_key = cs.get("key", "")
                            c_domain = cs.get("domain", "")
                            key_dotted = c_key.replace("_", ".").replace("-", ".")
                            key_stripped = c_key.replace("_", "").replace(".", "").replace("-", "")
                            if (
                                (c_domain and c_domain.lower() in url_lower)
                                or key_dotted in url_lower
                                or (len(key_stripped) > 3 and key_stripped in url_stripped)
                            ):
                                site_key = c_key
                                default_folder = cs.get("folder") or cs.get("name")
                                break

                    # 2. Check hardcoded sites if no custom site matched
                    if not site_key:
                        if any(x in url_lower for x in ("youtube", "youtu.be", "ytimg")):
                            site_key = "youtube"
                            default_folder = "YouTube"
                        elif "bunkr" in url_lower:
                            site_key = "bunkr"
                            default_folder = "Bunkr"
                        elif "gofile" in url_lower:
                            site_key = "gofile"
                            default_folder = "Gofile"
                        elif "pixel" in url_lower:
                            site_key = "pixeldrain"
                            default_folder = "Pixeldrain"
                        elif "tiktok" in url_lower:
                            site_key = "tiktok"
                            default_folder = "TikTok"
                        elif any(x in url_lower for x in ("twitter", "x.com")):
                            site_key = "twitter"
                            default_folder = "Twitter"
                        elif "instagram" in url_lower:
                            site_key = "instagram"
                            default_folder = "Instagram"
                        elif "reddit" in url_lower:
                            site_key = "reddit"
                            default_folder = "Reddit"
                        elif any(x in url_lower for x in ("simpcity.cr", "simpcity.su", "simpcity.")):
                            site_key = "simpcity"
                            default_folder = "SimpCityForums"
                        elif "pornhub" in url_lower:
                            site_key = "pornhub"
                            default_folder = "Pornhub"
                        elif "xvideos" in url_lower:
                            site_key = "xvideos"
                            default_folder = "XVideos"
                        elif "rule34" in url_lower:
                            site_key = "rule34"
                            default_folder = "Rule34"
                        elif any(x in url_lower for x in ("hanime", "hentai.tv")):
                            site_key = "hanime"
                            default_folder = "Hanime"
                        elif "hitomi" in url_lower:
                            site_key = "hitomi"
                            default_folder = "Hitomi"
                        elif any(x in url_lower for x in ("e-hentai", "exhentai")):
                            site_key = "ehentai"
                            default_folder = "E-Hentai"
                        else:
                            site_key = "other"
                            default_folder = "Others"

                    site_cfg = config.get("site_settings", {}).get(site_key, {})
                    folder_name = site_cfg.get("folder") or default_folder
                    dl_dir = str(Path(dl_dir) / folder_name)

                    # Create per-download subfolder for playlists and galleries.
                    # This is CRITICAL for concurrent downloads: each task must have
                    # its own unique directory so files don't get mixed together.
                    is_gallery_or_forum = (
                        any(x in url_lower for x in ("simpcity.cr", "simpcity.su", "simpcity."))
                        or info.get("is_playlist") is True
                        or site_key in ("simpcity",)
                    )
                    is_playlist_or_archive = (
                        (site_key == "youtube" and (
                            "list=" in url_lower or "playlist" in url_lower
                            or info.get("_type") == "playlist"
                            or info.get("is_playlist")
                            or (info.get("playlist_count") or 0) > 1
                            or info.get("entries") is not None
                        ))
                        or info.get("is_playlist") is True
                        or is_gallery_or_forum
                    )
                    if is_playlist_or_archive and task.title:
                        safe_title = re.sub(r'[\\/*?"<>|]', "", str(task.title)).strip()
                        if safe_title:
                            dl_dir = str(Path(dl_dir) / safe_title[:100])

                # Store dl_dir on the task object so it doesn't change between
                # the calculation here and the actual download call below.
                # This prevents concurrent tasks from sharing the same directory.
                task._dl_dir = dl_dir
                Path(dl_dir).mkdir(parents=True, exist_ok=True)
            except Exception as path_exc:
                logger.error("Task %s – failed creating output dir %s: %s", task.id, dl_dir, path_exc)
                dl_dir = config.get_download_dir()
                task._dl_dir = dl_dir
                Path(dl_dir).mkdir(parents=True, exist_ok=True)

            logger.info("Task %s – downloading to: %s (fmt=%s, q=%s)", task.id, dl_dir, task.format_type, task.quality)

            # ---- 4.  Download ----
            def _progress_hook(data: dict[str, Any]) -> None:
                """Bridge between extractor progress dicts and task state."""
                try:
                    # Respect pause gate
                    task._pause_event.wait()
                    if task._cancel_event.is_set():
                        raise _CancelledError()

                    idx = data.get("item_index") or data.get("playlist_index") or 0
                    cnt = data.get("item_count") or data.get("n_entries") or data.get("playlist_count") or 0
                    # item_count == -1 means unknown total (gallery watcher mode): don't update item_count
                    if idx and cnt and int(cnt) > 1:
                        task.item_index = int(idx)
                        task.item_count = int(cnt)
                    elif idx and int(cnt) == -1:
                        # Unknown total: just track how many files were downloaded, don't set item_count to 0 if already known
                        task.item_index = int(idx)
                        if not task.item_count or task.item_count <= 1:
                            task.item_count = 0  # 0 = hide the X/Y counter chip, show plain count elsewhere
                    if data.get("item_title"):
                        task.title = data["item_title"]

                    status = data.get("status", "")
                    if status == "downloading":
                        task.status = TaskStatus.DOWNLOADING
                        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                        downloaded = data.get("downloaded_bytes", 0)
                        if total and int(total) > 0:
                            task.total_size = int(total)
                        if downloaded and int(downloaded) > 0:
                            task.downloaded_size = int(downloaded)
                        if total and downloaded and int(total) > 0 and int(downloaded) > 0:
                            task.progress = min(int(downloaded) / int(total) * 100, 100.0)
                        elif data.get("fragment_count", 0) > 0:
                            task.progress = min(data.get("fragment_index", 0) / data.get("fragment_count") * 100, 100.0)
                        elif downloaded > 0 and (not total or total <= 0):
                            # Smoothly estimate progress based on downloaded megabytes when total size is unknown
                            mb = downloaded / (1024.0 * 1024.0)
                            task.progress = min(92.0, max(task.progress or 0.0, 5.0 + (mb / (mb + 40.0)) * 85.0))
                        if data.get("speed") is not None and float(data.get("speed", 0)) > 0:
                            task.speed = float(data["speed"])
                        if data.get("eta") is not None and int(data.get("eta", 0)) > 0:
                            task.eta = int(data["eta"])
                        if data.get("filename"):
                            task.filename = str(data["filename"])
                    elif status == "converting":
                        task.status = TaskStatus.CONVERTING
                    elif status == "finished":
                        task.filename = data.get("filename", task.filename)
                        if data.get("file_size"):
                            task.downloaded_size += int(data["file_size"])
                        # For gallery/watcher downloads: update item counter and animate progress
                        if idx:
                            task.item_index = int(idx)
                            task.status = TaskStatus.DOWNLOADING
                            # Update speed from watcher
                            watcher_speed = data.get("speed") or 0.0
                            if watcher_speed > 0:
                                task.speed = watcher_speed
                            current_progress = task.progress or 0
                            if task.item_count and task.item_count > 0:
                                task.progress = min((task.item_index / task.item_count) * 100.0, 99.0)
                            else:
                                increment = max((96.0 - current_progress) * 0.28, 5.0)
                                task.progress = min(current_progress + increment, 96.0)
                    elif status == "error":
                        task.error_message = format_user_error(data.get("error", "Unknown error"))
                except _CancelledError:
                    raise
                except Exception as hook_exc:
                    logger.warning("Task %s – progress hook error: %s", task.id, hook_exc)

            try:
                # 4a. Wait for download slot (Sequential Download support)
                import time
                task.status = TaskStatus.QUEUED
                task._metadata_fetched = True
                while True:
                    if task._cancel_event.is_set():
                        raise _CancelledError()
                    
                    with self._lock:
                        if bool(config.get("sequential_download", False)):
                            concurrent_limit = 1
                        else:
                            concurrent_limit = config.get("concurrent_downloads")
                            if concurrent_limit is None or int(concurrent_limit) <= 0:
                                concurrent_limit = config.get("max_concurrent", 3)
                            if concurrent_limit is None or int(concurrent_limit) <= 0:
                                concurrent_limit = 3
                            concurrent_limit = int(concurrent_limit)
                        
                        # Count ONLY tasks that are truly actively downloading/converting
                        active = sum(
                            1 for t in self._tasks.values() 
                            if getattr(t, "_is_actually_downloading", False) 
                            and t.status in (TaskStatus.DOWNLOADING, TaskStatus.CONVERTING)
                        )
                        
                        if active < concurrent_limit:
                            # Am I the next in line?
                            # Look at ALL tasks not yet actively downloading, in ordered position
                            waiting_ids = [
                                tid for tid in self._ordered_ids
                                if tid in self._tasks 
                                and self._tasks[tid].status == TaskStatus.QUEUED 
                                and not getattr(self._tasks[tid], "_is_actually_downloading", False)
                            ]
                            # Also include tasks not in ordered_ids yet
                            if task.id not in self._ordered_ids:
                                waiting_ids.insert(0, task.id)
                            
                            if not waiting_ids or waiting_ids[0] == task.id:
                                task._is_actually_downloading = True
                                task.status = TaskStatus.DOWNLOADING
                                break
                    time.sleep(0.3)

                try:
                    setattr(_progress_hook, "_task", task)
                    result_path = extractor.download(
                        url=task.url,
                        output_path=dl_dir,
                        format_id=task.format_type,
                        progress_hook=_progress_hook,
                        cancel_event=task._cancel_event,
                        task_id=task.id,
                        start_time=task.start_time,
                        end_time=task.end_time,
                        embed_metadata=task.embed_metadata,
                        download_subtitles=task.download_subtitles,
                        subtitle_langs=task.subtitle_langs,
                        keep_original=task.keep_original,
                    )
                except Exception as dl_err:
                    from src.extractors.gofile import GofileExtractor
                    from src.extractors.bunkr import BunkrExtractor
                    from src.extractors.pixeldrain import PixeldrainExtractor
                    from src.extractors.gallery_ext import GalleryDLExtractor
                    from src.extractors.cyberdrop_ext import CyberdropDLExtractor
                    from src.extractors.ytdlp_ext import YtdlpExtractor

                    fallback_classes = []
                    if "gofile.io" not in task.url.lower():
                        for cls in (GofileExtractor, BunkrExtractor, PixeldrainExtractor, GalleryDLExtractor, CyberdropDLExtractor, YtdlpExtractor):
                            if cls not in tried_extractors and cls.can_handle(task.url):
                                fallback_classes.append(cls)
                        if YtdlpExtractor not in tried_extractors and YtdlpExtractor not in fallback_classes:
                            fallback_classes.append(YtdlpExtractor)
                    else:
                        raise dl_err

                    success = False
                    for fb_cls in fallback_classes:
                        tried_extractors.add(fb_cls)
                        logger.warning("Extractor %s download failed (%s), falling back to %s...", extractor.__class__.__name__, dl_err, fb_cls.__name__)
                        try:
                            fb_extractor = fb_cls()
                            fb_extractor._task_quality = task.quality
                            fb_extractor._task_url = task.url
                            fb_extractor._site_settings = config.get("site_settings", {})
                            fb_extractor._app_proxy = config.get("proxy", "")
                            result_path = fb_extractor.download(
                                url=task.url,
                                output_path=dl_dir,
                                format_id=task.format_type,
                                progress_hook=_progress_hook,
                                cancel_event=task._cancel_event,
                                task_id=task.id,
                                start_time=task.start_time,
                                end_time=task.end_time,
                                embed_metadata=task.embed_metadata,
                                download_subtitles=task.download_subtitles,
                                subtitle_langs=task.subtitle_langs,
                                keep_original=task.keep_original,
                            )
                            extractor = fb_extractor
                            success = True
                            break
                        except Exception as fb_err:
                            dl_err = fb_err
                            logger.warning("Fallback extractor %s download also failed (%s).", fb_cls.__name__, fb_err)

                    if not success:
                        raise dl_err

                if task._cancel_event.is_set():
                    task.status = TaskStatus.CANCELLED
                    return

                if (task.start_time or task.end_time) and result_path and os.path.isfile(result_path):
                    if not extractor.__class__.__name__.startswith("YtDlp") or task.keep_original:
                        task.status = TaskStatus.CONVERTING
                        logger.info("Trimming file %s from %s to %s", result_path, task.start_time, task.end_time)
                        try:
                            def _p_time(val: str) -> float:
                                if not val: return 0.0
                                parts = str(val).strip().split(":")
                                try:
                                    if len(parts) == 1: return float(parts[0])
                                    elif len(parts) == 2: return float(parts[0]) * 60.0 + float(parts[1])
                                    elif len(parts) >= 3: return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
                                except Exception: return 0.0
                                return 0.0
                            s_sec = _p_time(task.start_time)
                            e_sec = _p_time(task.end_time)
                            if e_sec > 0 and e_sec <= s_sec:
                                e_sec = s_sec + e_sec
                            dur_sec = e_sec - s_sec if e_sec > s_sec else 0

                            temp_out = result_path + ".trimmed" + os.path.splitext(result_path)[1]
                            cmd = [config.get_ffmpeg_path(), "-y"]
                            if s_sec > 0:
                                cmd.extend(["-ss", str(s_sec)])
                            cmd.extend(["-i", result_path])
                            if dur_sec > 0:
                                cmd.extend(["-t", str(dur_sec)])
                            cmd.extend(["-c", "copy", temp_out])
                            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            if res.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
                                if task.keep_original:
                                    final_path = os.path.splitext(result_path)[0] + " (Trimmed)" + os.path.splitext(result_path)[1]
                                    if os.path.exists(final_path):
                                        try: os.remove(final_path)
                                        except Exception: pass
                                    os.rename(temp_out, final_path)
                                    result_path = final_path
                                else:
                                    os.remove(result_path)
                                    os.rename(temp_out, result_path)
                            else:
                                logger.warning("Copy trim failed, trying re-encode trim...")
                                cmd[-2:] = ["-c:v", "libx264", "-preset", "fast", "-c:a", "aac", temp_out]
                                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                                if os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
                                    if task.keep_original:
                                        final_path = os.path.splitext(result_path)[0] + " (Trimmed)" + os.path.splitext(result_path)[1]
                                        if os.path.exists(final_path):
                                            try: os.remove(final_path)
                                            except Exception: pass
                                        os.rename(temp_out, final_path)
                                        result_path = final_path
                                    else:
                                        os.remove(result_path)
                                        os.rename(temp_out, result_path)
                        except Exception as trim_err:
                            logger.error("Trimming failed: %s", trim_err)

                # İndirme tamamlandı demeden önce, klasörün veya dosyanın gerçekten var ve içinin dolu olduğunu kontrol et
                check_target = result_path or dl_dir
                is_empty = False
                if check_target and os.path.exists(check_target):
                    if os.path.isdir(check_target):
                        real_files = [f for f in os.listdir(check_target) if f not in ("AppData", ".suylios_cdl_data", "Cache", "Configs")]
                        if not real_files:
                            is_empty = True
                    elif os.path.isfile(check_target):
                        if os.path.getsize(check_target) == 0:
                            is_empty = True
                else:
                    is_empty = True

                if is_empty:
                    raise Exception("İndirme işlemi bitti ancak hedef klasörde hiçbir dosya oluşturulmadı. İçerik platform tarafından engellenmiş veya gizli olabilir.")

                task.status = TaskStatus.COMPLETED
                task._is_actually_downloading = False
                task.progress = 100.0
                task.filename = result_path or task.filename

                if task.compress_archive or config.get("auto_compress", False):
                    # Only archive if it's a single item, or if it's the last item in a playlist
                    should_archive = (task.item_count <= 1) or (task.item_index == task.item_count)
                    if not should_archive:
                        logger.info("Skipping archive for %s: item %d of %d", check_target, task.item_index, task.item_count)
                    else:
                        fmt = (task.compress_format or config.get("compress_format", "zip")).lower().strip(".")
                        try:
                            logger.info("Archiving %s using format %s...", check_target, fmt)
                            
                            # Eğer check_target tek dosya ama üst dizinde başka dosyalar da varsa (ve ana dl_dir değilse), klasörü arşivle
                            archive_dir = check_target
                            if os.path.isfile(check_target):
                                parent = os.path.dirname(check_target)
                                # Check if parent is NOT the main download directory
                                if os.path.normcase(os.path.abspath(parent)) != os.path.normcase(os.path.abspath(dl_dir)):
                                    parent_files = [f for f in os.listdir(parent) if not f.startswith(".") and f not in ("AppData", ".suylios_cdl_data", "Cache", "Configs")]
                                    if len(parent_files) > 1:
                                        archive_dir = parent

                            archive_success = False
                            
                            if os.path.isdir(archive_dir):
                                archive_base_name = os.path.basename(archive_dir.rstrip("/\\"))
                                # Arşiv adı = klasörün dışına geçici isimle oluştur, sonra içine taşıyacağız
                                archive_base = archive_dir.rstrip("/\\") + "_temp_archive"
                                temp_archive_path = ""
                                
                                if fmt == "rar":
                                    rar_bin = shutil.which("rar") or shutil.which("Rar") or r"C:\Program Files\WinRAR\Rar.exe"
                                    if os.path.exists(rar_bin):
                                        subprocess.run([rar_bin, "a", "-r", archive_base + ".rar", archive_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                        if os.path.exists(archive_base + ".rar"):
                                            temp_archive_path = archive_base + ".rar"
                                            archive_success = True
                                    else:
                                        fmt = "zip"
                                if fmt == "7z":
                                    sz_bin = shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe"
                                    if os.path.exists(sz_bin):
                                        subprocess.run([sz_bin, "a", archive_base + ".7z", archive_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                        if os.path.exists(archive_base + ".7z"):
                                            temp_archive_path = archive_base + ".7z"
                                            archive_success = True
                                    else:
                                        try:
                                            import py7zr
                                            with py7zr.SevenZipFile(archive_base + ".7z", 'w') as szf:
                                                szf.writeall(archive_dir, os.path.basename(archive_dir))
                                            if os.path.exists(archive_base + ".7z"):
                                                temp_archive_path = archive_base + ".7z"
                                                archive_success = True
                                        except ImportError:
                                            fmt = "zip"
                                if fmt in ("zip", "tar", "gztar", "bztar"):
                                    root_dir = os.path.dirname(archive_dir.rstrip("/\\"))
                                    base_dir = os.path.basename(archive_dir.rstrip("/\\"))
                                    shutil.make_archive(archive_base, fmt, root_dir, base_dir)
                                    ext = ".zip" if fmt == "zip" else (".tar.gz" if fmt == "gztar" else (".tar.bz2" if fmt == "bztar" else f".{fmt}"))
                                    if os.path.exists(archive_base + ext):
                                        temp_archive_path = archive_base + ext
                                        archive_success = True
                                        
                                # Eğer başarılı olduysa temp dosyayı klasörün içine taşı
                                if archive_success and temp_archive_path and os.path.exists(temp_archive_path):
                                    final_archive_path = os.path.join(archive_dir, os.path.basename(temp_archive_path).replace("_temp_archive", ""))
                                    if os.path.exists(final_archive_path):
                                        try: os.remove(final_archive_path)
                                        except: pass
                                    shutil.move(temp_archive_path, final_archive_path)
                                    task.filename = final_archive_path
                                    result_path = task.filename
                                    
                                    # Opsiyonel: Orijinal klasör içeriğini silme
                                    delete_after = config.get("delete_after_archive", False)
                                    if delete_after and not task.keep_original:
                                        logger.info("Archive successful, cleaning up contents of folder: %s", archive_dir)
                                        for item in os.listdir(archive_dir):
                                            item_path = os.path.join(archive_dir, item)
                                            # Kendi oluşturduğumuz zip/rar dosyasını silmeyelim
                                            if os.path.normcase(os.path.abspath(item_path)) != os.path.normcase(os.path.abspath(final_archive_path)):
                                                try:
                                                    if os.path.isdir(item_path):
                                                        shutil.rmtree(item_path)
                                                    else:
                                                        os.remove(item_path)
                                                except Exception as clean_err:
                                                    logger.error("Failed to clean up item inside folder: %s", clean_err)
                                                    
                                    archive_success = False # Bu bloğun altındaki genel silme işlemine girmemesi için False yapıyoruz (çünkü zaten üstte sildik)
                            elif os.path.isfile(archive_dir):
                                single_base = os.path.splitext(archive_dir)[0]
                                if fmt in ("zip", "tar", "gztar", "bztar"):
                                    root_dir = os.path.dirname(archive_dir)
                                    base_dir = os.path.basename(archive_dir)
                                    shutil.make_archive(single_base, fmt, root_dir, base_dir)
                                    ext = ".zip" if fmt == "zip" else (".tar.gz" if fmt == "gztar" else (".tar.bz2" if fmt == "bztar" else f".{fmt}"))
                                    archive_path = single_base + ext
                                    if os.path.exists(archive_path):
                                        archive_success = True
                                elif fmt == "rar":
                                    rar_bin = shutil.which("rar") or shutil.which("Rar") or r"C:\Program Files\WinRAR\Rar.exe"
                                    archive_path = single_base + ".rar"
                                    if os.path.exists(rar_bin):
                                        subprocess.run([rar_bin, "a", archive_path, archive_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                        if os.path.exists(archive_path):
                                            archive_success = True
                                    else:
                                        import zipfile
                                        archive_path = single_base + ".zip"
                                        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                                            zf.write(archive_dir, os.path.basename(archive_dir))
                                        if os.path.exists(archive_path):
                                            archive_success = True
                                elif fmt == "7z":
                                    sz_bin = shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe"
                                    archive_path = single_base + ".7z"
                                    if os.path.exists(sz_bin):
                                        subprocess.run([sz_bin, "a", archive_path, archive_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                        if os.path.exists(archive_path):
                                            archive_success = True
                                    else:
                                        try:
                                            import py7zr
                                            with py7zr.SevenZipFile(archive_path, 'w') as szf:
                                                szf.write(archive_dir, os.path.basename(archive_dir))
                                            if os.path.exists(archive_path):
                                                archive_success = True
                                        except ImportError:
                                            import zipfile
                                            archive_path = single_base + ".zip"
                                            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                                                zf.write(archive_dir, os.path.basename(archive_dir))
                                            if os.path.exists(archive_path):
                                                archive_success = True
                                
                                if archive_success and os.path.exists(archive_path):
                                    task.filename = archive_path
                                    result_path = task.filename
                                    
                            # İndirilen orijinal klasörü/dosyayı temizle (Tekil dosyalar veya temp'i taşımayan eski mantık için)
                            delete_after = config.get("delete_after_archive", False)
                            if archive_success and delete_after and not task.keep_original:
                                logger.info("Archive successful, cleaning up original: %s", archive_dir)
                                try:
                                    if os.path.isdir(archive_dir):
                                        shutil.rmtree(archive_dir)
                                    elif os.path.isfile(archive_dir):
                                        os.remove(archive_dir)
                                except Exception as clean_err:
                                    logger.error("Failed to clean up original after archiving: %s", clean_err)
                        except Exception as arc_err:
                            logger.error("Compression archive error: %s", arc_err)

                # On playlist/archive completion: restore the original title and clear item counter
                # On playlist/archive completion: restore the original title
                if task.archive_title:
                    task.title = task.archive_title
                    task.item_index = 0   # Clear so UI shows just the archive title
                logger.info("Task %s completed.", task.id)
                try:
                    config.add_to_history(task.to_dict())
                except Exception as hist_err:
                    logger.error("Failed to add to history: %s", hist_err)
            except (_CancelledError, ExtractionCancelled, BaseException) as exc:
                # BaseException catches _GDLCancelledError (subclass of BaseException)
                # as well as our own _CancelledError
                if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                    # This is _GDLCancelledError or similar – treat as cancelled
                    task.status = TaskStatus.CANCELLED
                    logger.info("Task %s cancelled during download (low-level interrupt).", task.id)
                elif isinstance(exc, (ExtractionCancelled, _CancelledError)):
                    task.status = TaskStatus.CANCELLED
                    logger.info("Task %s cancelled during download.", task.id)
                elif "Cancelled" in exc.__class__.__name__:
                    task.status = TaskStatus.CANCELLED
                    logger.info("Task %s cancelled during download.", task.id)
                else:
                    task.status = TaskStatus.ERROR
                    task.error_message = format_user_error(exc)
                    logger.error("Task %s – download error: %s", task.id, exc, exc_info=True)
        except BaseException as fatal_exc:
            if not isinstance(fatal_exc, Exception):
                task.status = TaskStatus.CANCELLED
                logger.info("Task %s cancelled (low-level BaseException).", task.id)
            else:
                task.status = TaskStatus.ERROR
                task.error_message = format_user_error(fatal_exc)
                logger.critical("Task %s fatal unhandled error: %s", task.id, fatal_exc, exc_info=True)
        finally:
            task._is_actually_downloading = False


class _CancelledError(BaseException):
    """Raised inside progress hooks to interrupt a download immediately."""

