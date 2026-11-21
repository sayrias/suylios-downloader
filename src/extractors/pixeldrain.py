"""
Suylios Downloader - Pixeldrain Extractor.

Downloads single files and file-lists from Pixeldrain using the public
REST API with chunked streaming and retry logic.
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from src.extractors.base_extractor import BaseExtractor, ExtractionCancelled, ExtractionError
from src.extractors.fast_downloader import _wait_if_paused

logger = logging.getLogger(__name__)

_API_BASE = "https://pixeldrain.com/api"
_CHUNK_SIZE = 524288  # 512 KB for high-throughput downloads
_MAX_RETRIES = 4
_RETRY_BACKOFF = 2  # seconds; doubles each attempt

_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?pixeldrain\.com/(?P<kind>[ul])/(?P<id>[A-Za-z0-9_-]+)",
)


class PixeldrainExtractor(BaseExtractor):
    """Pixeldrain file & list extractor."""

    def __init__(self) -> None:
        self._api_cache: dict[str, dict[str, Any]] = {}
        self._api_cache_lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, */*",
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
        kind, item_id = self._parse_url(url)

        if kind == "u":
            return self._single_info(item_id)
        return self._list_info(item_id)

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
        if pause_event is None and progress_hook is not None:
            task_obj = getattr(progress_hook, "_task", None)
            if task_obj is not None:
                pause_event = getattr(task_obj, "_pause_event", None)
        kind, item_id = self._parse_url(url)

        if kind == "u":
            return self._download_file(
                item_id, output_path, progress_hook,
                cancel_event=cancel_event, pause_event=pause_event,
            )

        # List: download every file
        meta = self._api_get(f"/list/{item_id}")
        files = meta.get("files", [])
        valid_files = [f for f in files if f.get("id")]
        if not valid_files:
            raise ExtractionError("Pixeldrain list is empty.")

        list_title = meta.get("title", item_id)
        dest = Path(output_path) / self._safe_filename(list_title)
        dest.mkdir(parents=True, exist_ok=True)

        total_count = len(valid_files)
        total_list_bytes = sum(int(f.get("size") or 0) for f in valid_files)
        per_file_done: dict[int, int] = {}
        per_file_total: dict[int, int] = {
            idx: int(f.get("size") or 0) for idx, f in enumerate(valid_files, 1)
        }
        per_file_speed: dict[int, float] = {}
        agg_lock = threading.Lock()
        stop_flag = threading.Event()
        next_idx = 0
        state_box: dict[str, Any] = {
            "last_path": "",
            "completed": 0,
            "last_emit": 0.0,
            "latest_title": valid_files[0].get("name") or valid_files[0]["id"],
        }

        def _pick_next() -> Optional[tuple[int, dict[str, Any]]]:
            nonlocal next_idx
            with agg_lock:
                if next_idx >= total_count or stop_flag.is_set():
                    return None
                item = valid_files[next_idx]
                next_idx += 1
                return next_idx, item

        def _emit_list_progress(
            item_title: str,
            item_file: Optional[Path] = None,
            force: bool = False,
        ) -> None:
            if not progress_hook:
                return
            now = time.monotonic()
            with agg_lock:
                if not force and (now - state_box["last_emit"] < 0.15):
                    return
                state_box["last_emit"] = now
                agg_done = sum(per_file_done.values())
                agg_speed = sum(per_file_speed.values())
                agg_total = total_list_bytes if total_list_bytes > 0 else sum(per_file_total.values())
                done_cnt = state_box["completed"]
                cur_idx = min(total_count, done_cnt + 1 if done_cnt < total_count else total_count)
                title_snap = state_box["latest_title"] or item_title
            eta = int((agg_total - agg_done) / agg_speed) if agg_speed > 0 and agg_total > agg_done else 0
            payload: dict[str, Any] = {
                "status": "downloading",
                "item_index": cur_idx,
                "item_count": total_count,
                "item_title": title_snap,
                "downloaded_bytes": agg_done,
                "total_bytes": agg_total,
                "speed": agg_speed,
                "eta": eta,
            }
            if item_file is not None:
                payload["filename"] = str(item_file)
            progress_hook(payload)

        def _list_worker() -> None:
            while not stop_flag.is_set():
                _wait_if_paused(pause_event, cancel_event)
                nxt = _pick_next()
                if nxt is None:
                    return
                idx, fmeta = nxt
                fid = fmeta["id"]
                fname = fmeta.get("name") or fid
                fsz = int(fmeta.get("size") or 0)
                try:
                    with agg_lock:
                        state_box["latest_title"] = fname
                    _emit_list_progress(fname, dest / self._safe_filename(fname), force=True)

                    def _item_hook(
                        data: dict[str, Any],
                        _idx: int = idx,
                        _fname: str = fname,
                        _fsz: int = fsz,
                    ) -> None:
                        if data.get("status") == "finished":
                            return
                        db = max(0, int(data.get("downloaded_bytes") or 0))
                        tb = max(0, int(data.get("total_bytes") or 0))
                        sp = float(data.get("speed") or 0.0)
                        with agg_lock:
                            if _fsz > 0:
                                db = min(db, _fsz)
                            elif tb > 0 and per_file_total.get(_idx, 0) <= 0:
                                per_file_total[_idx] = tb
                            if db > per_file_done.get(_idx, 0):
                                per_file_done[_idx] = db
                            per_file_speed[_idx] = sp
                            state_box["latest_title"] = _fname
                        _emit_list_progress(_fname, dest / self._safe_filename(_fname), force=False)

                    dl_path = self._download_file(
                        fid,
                        str(dest),
                        _item_hook,
                        cancel_event=cancel_event,
                        pause_event=pause_event,
                        file_meta=fmeta,
                    )
                    actual_sz = Path(dl_path).stat().st_size if dl_path and Path(dl_path).exists() else fsz
                    with agg_lock:
                        per_file_done[idx] = max(per_file_done.get(idx, 0), fsz, actual_sz)
                        if per_file_total.get(idx, 0) <= 0 and actual_sz > 0:
                            per_file_total[idx] = actual_sz
                        per_file_speed[idx] = 0.0
                        state_box["completed"] += 1
                        state_box["last_path"] = dl_path
                    _emit_list_progress(fname, Path(dl_path), force=True)
                except ExtractionCancelled:
                    stop_flag.set()
                    raise
                except ExtractionError as exc:
                    with agg_lock:
                        per_file_speed[idx] = 0.0
                    if cancel_event is not None and cancel_event.is_set():
                        stop_flag.set()
                        raise ExtractionCancelled("İndirme iptal edildi.") from exc
                    logger.warning("Pixeldrain list item %d/%d failed: %s", idx, total_count, exc)
                except Exception as exc:
                    with agg_lock:
                        per_file_speed[idx] = 0.0
                    if exc.__class__.__name__ == "_CancelledError" or (
                        cancel_event is not None and cancel_event.is_set()
                    ):
                        stop_flag.set()
                        raise ExtractionCancelled("İndirme iptal edildi.") from exc
                    logger.warning("Pixeldrain list item %d/%d failed: %s", idx, total_count, exc)

        pool_workers = min(2, total_count)
        with ThreadPoolExecutor(max_workers=pool_workers) as pool:
            futs = [pool.submit(_list_worker) for _ in range(pool_workers)]
            for fut in futs:
                fut.result()

        last_path = state_box["last_path"]
        if not last_path:
            raise ExtractionError("Pixeldrain list download failed: no files downloaded.")
        if progress_hook:
            progress_hook({
                "status": "finished",
                "filename": last_path or str(dest),
            })
        return last_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str]:
        m = _URL_PATTERN.match(url)
        if not m:
            raise ExtractionError(f"Invalid Pixeldrain URL: {url}")
        return m.group("kind"), m.group("id")

    def _single_info(self, file_id: str) -> dict[str, Any]:
        meta = self._api_get(f"/file/{file_id}/info")
        return {
            "title": meta.get("name", file_id),
            "thumbnail": f"{_API_BASE}/file/{file_id}/thumbnail",
            "duration": None,
            "formats": [
                {
                    "format_id": "original",
                    "ext": Path(meta.get("name", "file")).suffix.lstrip(".") or "bin",
                    "quality": "original",
                    "filesize": meta.get("size", 0),
                },
            ],
            "is_playlist": False,
            "playlist_items": [],
        }

    def _list_info(self, list_id: str) -> dict[str, Any]:
        meta = self._api_get(f"/list/{list_id}")
        items: list[dict[str, Any]] = []
        for f in meta.get("files", []):
            items.append({
                "title": f.get("name", ""),
                "url": f"https://pixeldrain.com/u/{f.get('id', '')}",
                "duration": None,
                "thumbnail": f"{_API_BASE}/file/{f.get('id', '')}/thumbnail",
            })
        return {
            "title": meta.get("title", list_id),
            "thumbnail": items[0]["thumbnail"] if items else "https://www.google.com/s2/favicons?domain=pixeldrain.com&sz=128",
            "duration": None,
            "formats": [],
            "is_playlist": True,
            "playlist_items": items,
        }

    def _download_file(
        self,
        file_id: str,
        dest_dir: str,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
        cancel_event: Optional[Any] = None,
        pause_event: Optional[Any] = None,
        file_meta: Optional[dict[str, Any]] = None,
    ) -> str:
        """Download a single Pixeldrain file with retries."""
        # Reuse list metadata when present; fetch /info only if name is missing.
        if file_meta and file_meta.get("name"):
            meta = file_meta
        else:
            meta = self._api_get(f"/file/{file_id}/info")
        filename = self._safe_filename(meta.get("name", file_id))
        total_size = int(meta.get("size") or 0)

        dest_path = Path(dest_dir) / filename
        download_url = f"{_API_BASE}/file/{file_id}"

        for attempt in range(1, _MAX_RETRIES + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise ExtractionCancelled("İndirme iptal edildi.")
            try:
                self._stream_download(
                    download_url, dest_path, total_size, progress_hook,
                    cancel_event=cancel_event, pause_event=pause_event,
                )
                return str(dest_path)
            except ExtractionCancelled:
                raise
            except (requests.RequestException, IOError) as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise ExtractionCancelled("İndirme iptal edildi.") from exc
                if attempt == _MAX_RETRIES:
                    raise ExtractionError(
                        f"Failed to download {filename} after {_MAX_RETRIES} attempts: {exc}"
                    ) from exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Pixeldrain retry %d/%d for %s (waiting %ds): %s",
                    attempt, _MAX_RETRIES, file_id, wait, exc,
                )
                if cancel_event is not None:
                    if cancel_event.wait(timeout=wait):
                        raise ExtractionCancelled("İndirme iptal edildi.")
                else:
                    time.sleep(wait)

        # Should never reach here, but keeps type checker happy.
        return str(dest_path)

    @staticmethod
    def _stream_download(
        url: str,
        dest: Path,
        total_size: int,
        progress_hook: Optional[Callable[[dict[str, Any]], None]],
        cancel_event: Optional[Any] = None,
        pause_event: Optional[Any] = None,
    ) -> None:
        """Chunked streaming download with progress reporting."""
        from src.extractors.fast_downloader import download_file_fast
        download_file_fast(
            url=url,
            target_path=dest,
            progress_hook=progress_hook,
            cancel_event=cancel_event,
            pause_event=pause_event,
            display_name=str(dest.name),
            num_workers=None,
        )

        if progress_hook:
            progress_hook({
                "status": "finished",
                "filename": str(dest),
            })

    def _api_get(self, endpoint: str) -> dict[str, Any]:
        """GET request to the Pixeldrain API with error handling."""
        lock = getattr(self, "_api_cache_lock", None)
        if lock is not None:
            with lock:
                if endpoint in self._api_cache:
                    return self._api_cache[endpoint]
        elif hasattr(self, "_api_cache") and endpoint in self._api_cache:
            return self._api_cache[endpoint]

        url = f"{_API_BASE}{endpoint}"
        sess = getattr(self, "_session", None) or requests
        try:
            resp = sess.get(url, timeout=30)
            resp.raise_for_status()
            res = resp.json()
            if lock is not None:
                with lock:
                    self._api_cache[endpoint] = res
            elif hasattr(self, "_api_cache"):
                self._api_cache[endpoint] = res
            return res
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            raise ExtractionError(
                f"Pixeldrain API error ({status}): {endpoint}"
            ) from exc
        except requests.RequestException as exc:
            raise ExtractionError(f"Pixeldrain network error: {exc}") from exc

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Sanitise a filename for the local filesystem."""
        # Remove characters illegal on Windows
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name.strip(". ") or "download"
