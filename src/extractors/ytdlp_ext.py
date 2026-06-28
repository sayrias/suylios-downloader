"""
Suylios Downloader - yt-dlp Extractor.

Universal extractor powered by yt-dlp.  Handles YouTube and 1 000+ other
sites.  Uses the Python API exclusively (no subprocess calls).
"""

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from src.config import config
from src.extractors.base_extractor import BaseExtractor, ExtractionError, ExtractionCancelled

logger = logging.getLogger(__name__)


def _patch_ytdlp_playlist_pagination() -> None:
    """Apply a runtime monkeypatch to yt-dlp to fix YouTube playlist pagination capping at 100 items.

    YouTube recently changed continuation item schemas from 'continuationItemRenderer'
    to 'continuationItemViewModel'. yt-dlp 2026.06.09 misses this token, stopping at 100.
    """
    try:
        import yt_dlp.utils
        import yt_dlp.extractor.youtube as y_ext

        base_cls = getattr(y_ext, "YoutubeTabBaseInfoExtractor", None)
        if not base_cls or hasattr(base_cls, "_suylios_patched"):
            return

        old_ec = base_cls._extract_continuation.__func__

        def new_ec(cls: Any, renderer: Any) -> Any:
            res = old_ec(cls, renderer)
            if res:
                return res
            ep = yt_dlp.utils.traverse_obj(
                renderer,
                (('contents', 'items', 'rows', 'subThreads'), ..., 'continuationItemViewModel', 'continuationCommand', 'innertubeCommand'),
                get_all=False,
                expected_type=dict,
            )
            if ep and hasattr(cls, "_extract_continuation_ep_data"):
                return cls._extract_continuation_ep_data(ep)
            return None

        base_cls._extract_continuation = classmethod(new_ec)
        base_cls._suylios_patched = True
        logger.debug("Successfully patched yt-dlp YouTube playlist continuation.")
    except Exception as exc:
        logger.warning("Failed to patch yt-dlp playlist continuation: %s", exc)


class YtdlpExtractor(BaseExtractor):
    """yt-dlp-based extractor.

    Acts as the **universal fallback** – its :meth:`can_handle` always
    returns ``True`` so that any URL not claimed by a more specific
    extractor is attempted through yt-dlp's vast site support.
    """

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()
        self._cached_raw_info: Optional[dict[str, Any]] = None

    @staticmethod
    def can_handle(url: str) -> bool:
        """Always returns ``True`` – yt-dlp is the catch-all backend."""
        return True

    # ------------------------------------------------------------------
    # Info extraction
    # ------------------------------------------------------------------

    def extract_info(self, url: str) -> dict[str, Any]:
        """Fetch metadata via yt-dlp without downloading."""
        try:
            import yt_dlp
            _patch_ytdlp_playlist_pagination()
        except ImportError as exc:
            raise ExtractionError(
                "yt-dlp is not installed.  Run: pip install yt-dlp"
            ) from exc

        ydl_opts = self._base_opts(url)
        ydl_opts["extract_flat"] = "in_playlist"
        if "list=rd" in url.lower() or "start_radio=" in url.lower():
            ydl_opts["playlistend"] = 25

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                raw = ydl.extract_info(url, download=False)
                self._cached_raw_info = raw
        except Exception as exc:
            raise ExtractionError(f"yt-dlp extract_info failed: {exc}") from exc

        if raw is None:
            raise ExtractionError("yt-dlp returned no information for this URL.")

        return self._normalise_info(raw)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(
        self,
        url: str,
        output_path: str,
        format_id: str = "best",
        progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> str:
        """Download media via yt-dlp and return the resulting file path."""
        try:
            import yt_dlp
            _patch_ytdlp_playlist_pagination()
        except ImportError as exc:
            raise ExtractionError(
                "yt-dlp is not installed.  Run: pip install yt-dlp"
            ) from exc

        ydl_opts = self._base_opts(url)
        ydl_opts["paths"] = {"home": output_path}
        ydl_opts["outtmpl"] = {"default": "%(title).150B [%(id)s].%(ext)s"}

        # Format selection
        fmt_opts = self._format_opts(format_id)
        ydl_opts.update(fmt_opts)
        logger.info("yt-dlp download: url=%s, format=%s, postprocessors=%s",
                     url[:80], ydl_opts.get("format", "?"),
                     bool(ydl_opts.get("postprocessors")))

        # Progress hook bridge
        final_filename: list[str] = []

        def _hook(d: dict[str, Any]) -> None:
            status = d.get("status", "")
            payload: dict[str, Any] = {"status": status}
            info_dict = d.get("info_dict", {}) or {}
            idx = d.get("playlist_index") or info_dict.get("playlist_index") or 0
            cnt = d.get("playlist_count") or d.get("n_entries") or info_dict.get("n_entries") or info_dict.get("playlist_count") or 0
            if idx and cnt and int(cnt) > 1:
                payload["item_index"] = int(idx)
                payload["item_count"] = int(cnt)
            title = info_dict.get("title") or d.get("filename")
            if title and cnt and int(cnt) > 1:
                payload["item_title"] = title

            if status == "downloading":
                payload["downloaded_bytes"] = d.get("downloaded_bytes", 0)
                payload["total_bytes"] = (
                    d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                )
                payload["speed"] = d.get("speed") or 0
                payload["eta"] = d.get("eta") or 0
                payload["filename"] = d.get("filename", "")
                payload["fragment_index"] = d.get("fragment_index", 0)
                payload["fragment_count"] = d.get("fragment_count", 0)
            elif status == "finished":
                fname = d.get("filename", "")
                if fname:
                    final_filename.clear()
                    final_filename.append(fname)
                payload["filename"] = fname
            elif status == "error":
                payload["error"] = str(d.get("error", "Unknown error"))

            if progress_hook:
                progress_hook(payload)

        def _postprocessor_hook(d: dict[str, Any]) -> None:
            status = d.get("status", "")
            if status == "started":
                if progress_hook:
                    progress_hook({"status": "converting"})
            elif status == "finished":
                info = d.get("info_dict", {})
                filepath = info.get("filepath") or info.get("filename", "")
                if filepath:
                    final_filename.clear()
                    final_filename.append(filepath)

        ydl_opts["progress_hooks"] = [_hook]
        ydl_opts["postprocessor_hooks"] = [_postprocessor_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            if "DownloadCancelled" in exc.__class__.__name__ or "CancelledError" in exc.__class__.__name__:
                raise ExtractionCancelled("Download cancelled") from exc
            raise ExtractionError(f"yt-dlp download failed: {exc}") from exc

        return final_filename[-1] if final_filename else ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _base_opts(self, url: str = "") -> dict[str, Any]:
        """Common yt-dlp options shared between info extraction and
        download."""
        opts: dict[str, Any] = {
            "quiet": False,
            "no_warnings": False,
            "no_color": True,
            "ignoreerrors": False,
            "retries": 5,
            "fragment_retries": 5,
            "socket_timeout": 30,
            "nocheckcertificate": False,
            "geo_bypass": True,
            "noprogress": True,
        }
        check_url = (url or getattr(self, "_task_url", "")).lower()
        if "list=rd" in check_url or "start_radio=" in check_url or "list=ul" in check_url or "list=ll" in check_url:
            opts["playlistend"] = 50

        # FFmpeg location
        ffmpeg = config.get_ffmpeg_path()
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
            logger.debug("FFmpeg found: %s", ffmpeg)
        else:
            logger.debug("FFmpeg not found – merge/conversion unavailable")

        # Proxy
        proxy = config.get("proxy", "")
        if proxy:
            opts["proxy"] = proxy

        # Speed limit (bytes/sec, 0 = unlimited)
        speed_limit = config.get("speed_limit", 0)
        if speed_limit and speed_limit > 0:
            opts["ratelimit"] = speed_limit

        # Cookies file support from site settings
        task_url = getattr(self, "_task_url", "").lower()
        sites = config.get("site_settings", {})
        for site_key, s_data in sites.items():
            match_key = "youtu" if site_key == "youtube" else site_key
            if (match_key in task_url or site_key == "other") and isinstance(s_data, dict):
                cookies_path = s_data.get("cookies", "").strip()
                if cookies_path and Path(cookies_path).is_file():
                    opts["cookiefile"] = str(cookies_path)
                    break

        return opts

    def _format_opts(self, format_type: str) -> dict[str, Any]:
        """Return yt-dlp options for the requested format/quality."""
        quality = getattr(self, "_task_quality", None) or config.get("default_quality", "best")
        q_clean = str(quality).lower().replace("p", "").replace("kbps", "").strip()
        q_num = q_clean if q_clean.isdigit() else None
        hf = f"[height<={q_num}]" if q_num else ""

        opts: dict[str, Any] = {}
        ffmpeg_path = config.get_ffmpeg_path()

        fmt_clean = str(format_type or "auto").strip().lower()
        if any(x in fmt_clean for x in ("mp3", "flac", "m4a", "wav", "audio", "ses")):
            if "flac" in fmt_clean: codec = "flac"
            elif "m4a" in fmt_clean: codec = "m4a"
            elif "wav" in fmt_clean: codec = "wav"
            else: codec = "mp3"
            bitrate = q_num or config.get("mp3_bitrate", "320")
            if ffmpeg_path:
                opts["format"] = "bestaudio/best"
                opts["postprocessors"] = [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": codec,
                        "preferredquality": str(bitrate),
                    },
                ]
            else:
                opts["format"] = "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best"

        elif any(x in fmt_clean for x in ("mp4", "mkv", "webm", "auto", "best", "video", "otomatik")):
            ext_pref = "mp4" if "mp4" in fmt_clean else ("mkv" if "mkv" in fmt_clean else ("webm" if "webm" in fmt_clean else None))
            if ffmpeg_path:
                if q_num:
                    if ext_pref == "mp4":
                        fmt = f"bestvideo*{hf}[ext=mp4]+bestaudio[ext=m4a]/bestvideo*{hf}+bestaudio/best{hf}[ext=mp4]/best{hf}/bestvideo*+bestaudio/best"
                    elif ext_pref == "webm":
                        fmt = f"bestvideo*{hf}[ext=webm]+bestaudio[ext=webm]/bestvideo*{hf}+bestaudio/best{hf}[ext=webm]/best{hf}/bestvideo*+bestaudio/best"
                    else:
                        fmt = f"bestvideo*{hf}+bestaudio/best{hf}/bestvideo*+bestaudio/best"
                else:
                    if ext_pref == "mp4":
                        fmt = "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/bestvideo*+bestaudio/best[ext=mp4]/best"
                    elif ext_pref == "webm":
                        fmt = "bestvideo*[ext=webm]+bestaudio[ext=webm]/bestvideo*+bestaudio/best[ext=webm]/best"
                    else:
                        fmt = "bestvideo*+bestaudio/best"
                if ext_pref:
                    opts["merge_output_format"] = ext_pref
            else:
                if q_num:
                    if ext_pref:
                        fmt = f"best{hf}[ext={ext_pref}]/best{hf}/best[ext={ext_pref}]/best"
                    else:
                        fmt = f"best{hf}/best"
                else:
                    if ext_pref:
                        fmt = f"best[ext={ext_pref}]/best"
                    else:
                        fmt = "best"
            opts["format"] = fmt
        else:
            opts["format"] = "bestvideo*+bestaudio/best" if ffmpeg_path else "best"

        return opts

    @staticmethod
    def _normalise_info(raw: dict[str, Any]) -> dict[str, Any]:
        """Transform a yt-dlp info dict into our canonical schema."""
        is_playlist = raw.get("_type") == "playlist" or "entries" in raw

        formats_list: list[dict[str, Any]] = []
        for fmt in raw.get("formats", []):
            formats_list.append({
                "format_id": fmt.get("format_id", "unknown"),
                "ext": fmt.get("ext", "?"),
                "quality": _quality_label(fmt),
                "filesize": fmt.get("filesize") or fmt.get("filesize_approx") or 0,
            })

        playlist_items: list[dict[str, Any]] = []
        if is_playlist:
            for entry in raw.get("entries", []) or []:
                if entry is None:
                    continue
                playlist_items.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("url") or entry.get("webpage_url", ""),
                    "duration": entry.get("duration"),
                    "thumbnail": entry.get("thumbnail"),
                })

        return {
            "title": raw.get("title") or raw.get("id", "Unknown"),
            "thumbnail": raw.get("thumbnail"),
            "duration": raw.get("duration"),
            "formats": formats_list,
            "is_playlist": is_playlist,
            "playlist_items": playlist_items,
            # Pass through playlist metadata for subfolder/archive detection
            "playlist_count": raw.get("playlist_count") or len(playlist_items) or None,
            "_type": raw.get("_type"),
            "entries": playlist_items if playlist_items else ([] if is_playlist else None),
        }


def _quality_label(fmt: dict[str, Any]) -> str:
    """Build a human-readable quality label for a yt-dlp format dict."""
    parts: list[str] = []
    height = fmt.get("height")
    if height:
        parts.append(f"{height}p")
    fps = fmt.get("fps")
    if fps and fps > 30:
        parts.append(f"{fps}fps")
    vcodec = fmt.get("vcodec", "none")
    acodec = fmt.get("acodec", "none")
    if vcodec != "none" and acodec != "none":
        parts.append("av")
    elif vcodec != "none":
        parts.append("video-only")
    elif acodec != "none":
        parts.append("audio-only")
    return " ".join(parts) if parts else fmt.get("format_note", "?")
