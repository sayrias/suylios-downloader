"""
Suylios Downloader - Async Download Task Manager.

Manages a pool of concurrent downloads, each represented as a *task*
with observable state transitions.  Thread-safe for use from both the
PyWebView Bridge thread and internal worker threads.
"""

import enum
import logging
import os
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

logger = logging.getLogger(__name__)


def format_user_error(err: Any) -> str:
    """Format and translate exceptions into clean Turkish messages for the UI."""
    s = str(err)
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
    if "gofile" in s.lower():
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
    return s[:200]


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
    from src.extractors.gallery_ext import GalleryDLExtractor
    from src.extractors.ytdlp_ext import YtdlpExtractor

    # Order matters: specific extractors first, yt-dlp as universal fallback.
    for cls in (
        PixeldrainExtractor,
        GofileExtractor,
        BunkrExtractor,
        GalleryDLExtractor,
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
        self._lock = threading.RLock()
        cfg_workers = config.get("max_concurrent", 3)
        max_workers = 1000 if cfg_workers <= 0 else cfg_workers
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dl",
        )
        logger.info(
            "DownloadManager initialised (cfg_concurrent=%s, pool_workers=%d).",
            cfg_workers, max_workers,
        )

    def update_max_concurrent(self, new_concurrent: int) -> None:
        """Update the max concurrent downloads pool dynamically."""
        with self._lock:
            max_workers = 1000 if new_concurrent <= 0 else new_concurrent
            logger.info("Updating DownloadManager pool to max_workers=%d", max_workers)
            old_pool = self._pool
            self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dl")
            # Old pool will continue running existing active tasks until completion
            old_pool.shutdown(wait=False)

    # ------------------------------------------------------------------
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
        )
        if scheduled_at and scheduled_at > int(__import__('time').time()):
            task.status = TaskStatus.QUEUED
            with self._lock:
                self._tasks[task.id] = task
            self._pool.submit(self._fetch_scheduled_metadata, task)
            logger.info("Task %s scheduled for %s: %s", task.id, scheduled_at, url)
        else:
            with self._lock:
                self._tasks[task.id] = task
            self._pool.submit(self._run_task, task)
            logger.info("Task %s queued: %s (fmt=%s, q=%s)", task.id, url, format_type, quality)
        return task

    def add_batch_tasks(
        self,
        urls: list[str],
        format_type: str = "auto",
        quality: str = "best",
        embed_metadata: bool = True,
        download_subtitles: bool = False,
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
                return True
        return False

    def get_all_tasks(self) -> list[dict[str, Any]]:
        """Return a JSON-serialisable list of all task snapshots."""
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

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
            except Exception as exc:
                task.status = TaskStatus.ERROR
                task.error_message = format_user_error(exc)
                logger.error("Task %s – extractor error: %s", task.id, exc)
                return

            # ---- 2.  Extract metadata ----
            logger.info("Task %s – extracting metadata…", task.id)
            try:
                task.status = TaskStatus.DOWNLOADING
                info = extractor.extract_info(task.url)
                task.title = info.get("title", task.url)
                task.thumbnail = info.get("thumbnail") or ""
                # Keep a copy of the playlist/archive title so we can restore it on finish
                task.archive_title = task.title
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

                    # Create subfolder named after playlist or archive title
                    is_playlist_or_archive = (
                        ("list=" in url_lower or "playlist" in url_lower
                         or info.get("_type") == "playlist"
                         or info.get("is_playlist")
                         or (info.get("playlist_count") or 0) > 1
                         or info.get("entries") is not None)
                        if site_key == "youtube" else
                        (site_key in ("bunkr", "gofile", "pixeldrain") or task.item_count > 1)
                    )
                    if is_playlist_or_archive and task.title:
                        safe_title = re.sub(r'[\\/*?:"<>|]', "", str(task.title)).strip()
                        if safe_title:
                            dl_dir = str(Path(dl_dir) / safe_title[:100])

                Path(dl_dir).mkdir(parents=True, exist_ok=True)
            except Exception as path_exc:
                logger.error("Task %s – failed creating output dir %s: %s", task.id, dl_dir, path_exc)
                dl_dir = config.get_download_dir()
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
                    if idx and cnt and int(cnt) > 1:
                        task.item_index = int(idx)
                        task.item_count = int(cnt)
                    if data.get("item_title"):
                        task.title = data["item_title"]

                    status = data.get("status", "")
                    if status == "downloading":
                        task.status = TaskStatus.DOWNLOADING
                        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                        downloaded = data.get("downloaded_bytes", 0)
                        task.total_size = int(total)
                        task.downloaded_size = int(downloaded)
                        if total > 0:
                            task.progress = min(downloaded / total * 100, 100.0)
                        elif data.get("fragment_count", 0) > 0:
                            task.progress = min(data.get("fragment_index", 0) / data.get("fragment_count") * 100, 100.0)
                        task.speed = data.get("speed") or 0.0
                        task.eta = data.get("eta") or 0
                        task.filename = data.get("filename", task.filename)
                    elif status == "converting":
                        task.status = TaskStatus.CONVERTING
                    elif status == "finished":
                        task.filename = data.get("filename", task.filename)
                    elif status == "error":
                        task.error_message = format_user_error(data.get("error", "Unknown error"))
                except _CancelledError:
                    raise
                except Exception as hook_exc:
                    logger.warning("Task %s – progress hook error: %s", task.id, hook_exc)

            try:
                result_path = extractor.download(
                    url=task.url,
                    output_path=dl_dir,
                    format_id=task.format_type,
                    progress_hook=_progress_hook,
                    start_time=task.start_time,
                    end_time=task.end_time,
                    embed_metadata=task.embed_metadata,
                    download_subtitles=task.download_subtitles,
                    subtitle_langs=task.subtitle_langs,
                    keep_original=task.keep_original,
                )
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

                task.status = TaskStatus.COMPLETED
                task.progress = 100.0
                task.filename = result_path or task.filename
                # On playlist/archive completion: restore the original title and clear item counter
                if task.archive_title:
                    task.title = task.archive_title
                task.item_index = 0   # Clear so UI shows just the archive title
                logger.info("Task %s completed.", task.id)
                try:
                    config.add_to_history(task.to_dict())
                except Exception as hist_err:
                    logger.error("Failed to add to history: %s", hist_err)
            except _CancelledError:
                task.status = TaskStatus.CANCELLED
                logger.info("Task %s cancelled during download.", task.id)
            except ExtractionCancelled:
                task.status = TaskStatus.CANCELLED
                logger.info("Task %s cancelled during download.", task.id)
            except Exception as exc:
                if "Cancelled" in exc.__class__.__name__:
                    task.status = TaskStatus.CANCELLED
                    logger.info("Task %s cancelled during download.", task.id)
                    return
                task.status = TaskStatus.ERROR
                task.error_message = format_user_error(exc)
                logger.error("Task %s – download error: %s", task.id, exc, exc_info=True)
        except Exception as fatal_exc:
            task.status = TaskStatus.ERROR
            task.error_message = format_user_error(fatal_exc)
            logger.critical("Task %s fatal unhandled error: %s", task.id, fatal_exc, exc_info=True)


class _CancelledError(Exception):
    """Raised inside progress hooks to interrupt a download."""

