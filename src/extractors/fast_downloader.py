"""
Suylios Downloader - High-Speed Multi-Connection Parallel Range Downloader.

Splits files into multiple chunks and downloads them concurrently using HTTP Range
requests to independent part files to completely eliminate disk locks and bypass CDN throttling.
"""

import os
import time
import shutil
import logging
import threading
from pathlib import Path
from typing import Any, Optional, Dict, Callable
try:
    from curl_cffi import requests
except ImportError:
    import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("suylios.fast_downloader")


def download_file_fast(
    url: str,
    target_path: Path,
    headers: Optional[Dict[str, str]] = None,
    progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    display_name: str = "",
    item_idx: int = 0,
    item_cnt: int = 0,
    num_workers: int = 32,
) -> Path:
    """Download a file with maximum throughput using concurrent HTTP Range workers to zero-lock part files."""
    req_headers = dict(headers or {})
    if "User-Agent" not in req_headers:
        req_headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    # 1. Probe file size and Range support
    total_size = 0
    accept_ranges = False
    try:
        r = requests.head(
            url, headers=req_headers, timeout=12, allow_redirects=True, verify=False
        )
        if r.status_code in (200, 206):
            total_size = int(r.headers.get("Content-Length", 0))
            if (
                r.headers.get("Accept-Ranges", "").lower() == "bytes"
                or r.status_code == 206
            ):
                accept_ranges = True
    except Exception as exc:
        logger.debug("fast_downloader HEAD probe failed: %s", exc)

    if not accept_ranges or total_size < 1024 * 512:
        try:
            r_test = requests.get(
                url,
                headers={**req_headers, "Range": "bytes=0-1023"},
                timeout=12,
                stream=True,
                verify=False,
            )
            if r_test.status_code == 206:
                accept_ranges = True
                if total_size == 0:
                    cr = r_test.headers.get("Content-Range", "")
                    if "/" in cr and cr.split("/")[-1].isdigit():
                        total_size = int(cr.split("/")[-1])
            if hasattr(r_test, "close"):
                try:
                    r_test.close()
                except Exception:
                    pass
        except Exception:
            pass

    start_time = time.time()
    worker_bytes: Dict[int, int] = {}
    progress_lock = threading.Lock()
    last_progress_time = 0.0

    def _update_progress(force: bool = False):
        nonlocal last_progress_time
        if not progress_hook:
            return
        now = time.time()
        # Throttled to 10 FPS (every 0.1s) for silky-smooth progress bar updates
        if not force and (now - last_progress_time < 0.1):
            return
        with progress_lock:
            if not force and (now - last_progress_time < 0.1):
                return
            last_progress_time = now
            current_downloaded = sum(worker_bytes.values())
            elapsed = now - start_time
            speed = current_downloaded / elapsed if elapsed > 0.1 else 0
            payload: Dict[str, Any] = {
                "status": "downloading",
                "downloaded_bytes": current_downloaded,
                "total_bytes": total_size,
                "speed": speed,
                "filename": display_name or target_path.name,
            }
            if item_cnt > 1:
                payload["item_index"] = item_idx
                payload["item_count"] = item_cnt
                payload["item_title"] = display_name
            progress_hook(payload)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = target_path.parent / f".parts_{target_path.name}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # If Range is supported and file size is at least 512 KB, use zero-lock parallel part files
    if accept_ranges and total_size >= 1024 * 512:
        if total_size >= 1024 * 1024 * 50:
            num_workers = max(num_workers, 64)
        elif total_size >= 1024 * 1024 * 10:
            num_workers = max(num_workers, 48)

        logger.info(
            "Starting parallel range download (%d workers) for %s (size: %d bytes)",
            num_workers,
            display_name or target_path.name,
            total_size,
        )

        chunk_size = max(1024 * 512, total_size // num_workers)
        ranges = []
        start = 0
        while start < total_size:
            end = min(total_size - 1, start + chunk_size - 1)
            ranges.append((start, end, len(ranges)))
            start = end + 1

        part_files = [tmp_dir / f"part_{part_id}.bin" for _, _, part_id in ranges]

        def _worker(range_start: int, range_end: int, part_id: int):
            if cancel_event and cancel_event.is_set():
                return
            part_path = part_files[part_id]
            h = {**req_headers, "Range": f"bytes={range_start}-{range_end}"}
            for attempt in range(5):
                if cancel_event and cancel_event.is_set():
                    return
                try:
                    # Open part file ONCE for writing per worker - ZERO disk contention
                    resp = requests.get(
                        url, headers=h, timeout=20, stream=True, verify=False
                    )
                    try:
                        if resp.status_code in (200, 206):
                            with open(part_path, "wb") as f:
                                for chunk in resp.iter_content(chunk_size=65536):
                                    if cancel_event and cancel_event.is_set():
                                        return
                                    if not chunk:
                                        break
                                    f.write(chunk)
                                    worker_bytes[part_id] = worker_bytes.get(part_id, 0) + len(chunk)
                                    _update_progress(force=False)
                            return
                    finally:
                        if hasattr(resp, "close"):
                            try:
                                resp.close()
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug("Worker retry %d for part %d: %s", attempt, part_id, exc)
                    time.sleep(1)

        with ThreadPoolExecutor(max_workers=min(num_workers, len(ranges))) as executor:
            futures = [executor.submit(_worker, rs, re, pid) for rs, re, pid in ranges]
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    break

        if cancel_event and cancel_event.is_set():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise Exception("Download cancelled")

        # Concatenate all part files into target_path at RAM/NVMe speed
        tmp_final = target_path.with_suffix(target_path.suffix + ".downloading")
        with open(tmp_final, "wb") as out_f:
            for pf in part_files:
                if pf.exists():
                    with open(pf, "rb") as in_f:
                        shutil.copyfileobj(in_f, out_f, 1024 * 1024)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_final.replace(target_path)
        _update_progress(force=True)

    else:
        logger.info(
            "Starting single-stream download for %s (Range not available or small file)",
            display_name or target_path.name,
        )
        tmp_path = target_path.with_suffix(target_path.suffix + ".downloading")
        resp = requests.get(
            url, headers=req_headers, timeout=25, stream=True, verify=False
        )
        try:
            resp.raise_for_status()
            if total_size == 0:
                total_size = int(resp.headers.get("Content-Length", 0))
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if cancel_event and cancel_event.is_set():
                        break
                    if not chunk:
                        break
                    f.write(chunk)
                    worker_bytes[-1] = worker_bytes.get(-1, 0) + len(chunk)
                    _update_progress(force=False)
        finally:
            if hasattr(resp, "close"):
                try:
                    resp.close()
                except Exception:
                    pass

        if cancel_event and cancel_event.is_set():
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise Exception("Download cancelled")

        if tmp_path.exists():
            tmp_path.replace(target_path)
        _update_progress(force=True)

    logger.info("Finished fast download: %s", target_path)
    return target_path
