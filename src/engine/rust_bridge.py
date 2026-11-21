"""
Suylios Downloader - Native Rust Engine Bridge (`suylios-engine`).

Provides high-speed IPC between Python/UI and the compiled Rust hybrid core
(`bin/suylios-engine`), including:
- Zero-merge `pwrite` HTTP Range Work-Stealing downloads with `.suylios-part` checkpointing
- Real-time stdin pause / resume / cancel signaling
- Native Rust extractors (Pixeldrain, Gofile, Bunkr, Reddit, VideoHost)
- 3-Channel Ed25519 + PyPI `.whl` Smart Updater & SiteRegistry
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.extractors.base_extractor import ExtractionCancelled, ExtractionError

logger = logging.getLogger(__name__)

_ENGINE_PATH_CACHE: Optional[str] = None
_ENGINE_LOCK = threading.Lock()


def locate_rust_engine() -> Optional[str]:
    """Return the path to the compiled `suylios-engine` binary if available."""
    global _ENGINE_PATH_CACHE
    with _ENGINE_LOCK:
        if _ENGINE_PATH_CACHE and Path(_ENGINE_PATH_CACHE).is_file():
            return _ENGINE_PATH_CACHE

        exe_name = "suylios-engine.exe" if os.name == "nt" else "suylios-engine"
        root = Path(__file__).resolve().parent.parent.parent

        candidates = [
            root / "bin" / exe_name,
            root / "rust" / "target" / "release" / exe_name,
            root / "target" / "release" / exe_name,
        ]
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.insert(0, Path(meipass) / "bin" / exe_name)
            candidates.insert(0, Path(sys.executable).resolve().parent / "bin" / exe_name)

        for cand in candidates:
            if cand.is_file() and os.access(cand, os.X_OK):
                _ENGINE_PATH_CACHE = str(cand)
                return _ENGINE_PATH_CACHE

    return None


def is_rust_engine_available() -> bool:
    return locate_rust_engine() is not None


def run_rust_command(cmd: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 45.0) -> Optional[Dict[str, Any]]:
    """Run a single JSON command on `suylios-engine` and return the parsed JSON response."""
    bin_path = locate_rust_engine()
    if not bin_path:
        return None

    args = [bin_path, cmd]
    if payload is not None:
        args.append(json.dumps(payload, ensure_ascii=False))

    cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=cflags,
        )
        lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
        if lines:
            return json.loads(lines[-1])
    except Exception as exc:
        logger.debug("Rust engine command '%s' failed: %s", cmd, exc)
    return None


def rust_download_file(
    url: str,
    target_path: Path,
    *,
    headers: Optional[Dict[str, str]] = None,
    proxy: str = "",
    segmented_download: bool = True,
    segmented_connections: int = 8,
    segmented_mode: str = "adaptive",
    speed_limit_bps: int = 0,
    known_size: int = 0,
    resumable: Optional[bool] = None,
    max_host_conns: int = 0,
    display_name: str = "",
    item_idx: int = 0,
    item_cnt: int = 0,
    progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> Optional[Path]:
    """Execute a high-speed download via the Rust `TransferEngine` (`suylios-engine download`).

    Returns `target_path` on success, `None` if the Rust binary is unavailable
    (allowing caller to fall back cleanly), or raises `ExtractionCancelled` /
    `ExtractionError` on cancellation or fatal HTTP failure.
    """
    bin_path = locate_rust_engine()
    if not bin_path:
        return None

    req_payload = {
        "url": url,
        "output_path": str(target_path),
        "headers": dict(headers or {}),
        "proxy": str(proxy or ""),
        "segmented_download": bool(segmented_download),
        "segmented_connections": int(segmented_connections),
        "segmented_mode": str(segmented_mode or "adaptive"),
        "speed_limit_bps": int(speed_limit_bps or 0),
        "known_size": int(known_size or 0),
        "resumable": resumable,
        "max_host_conns": int(max_host_conns or 0),
        "display_name": display_name or target_path.name,
        "item_index": int(item_idx or 0),
        "item_count": int(item_cnt or 0),
    }

    cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        [bin_path, "download", json.dumps(req_payload, ensure_ascii=False)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=cflags,
    )

    stop_watcher = threading.Event()

    def _watch_signals() -> None:
        was_paused = False
        while not stop_watcher.is_set() and proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                try:
                    if proc.stdin:
                        proc.stdin.write('{"cmd":"cancel"}\n')
                        proc.stdin.flush()
                except Exception:
                    pass
                time.sleep(0.15)
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                break

            is_paused = pause_event is not None and not pause_event.is_set()
            if is_paused != was_paused:
                was_paused = is_paused
                cmd_str = '{"cmd":"pause"}\n' if is_paused else '{"cmd":"resume"}\n'
                try:
                    if proc.stdin:
                        proc.stdin.write(cmd_str)
                        proc.stdin.flush()
                except Exception:
                    pass
            time.sleep(0.12)

    watcher = threading.Thread(target=_watch_signals, daemon=True)
    watcher.start()

    last_error = ""
    try:
        if proc.stdout:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue

                status = msg.get("status", "")
                if status == "error":
                    last_error = str(msg.get("error") or "Rust transfer error")
                elif status == "downloading" and progress_hook:
                    hook_data: Dict[str, Any] = {
                        "status": "downloading",
                        "status_detail": msg.get("status_detail", ""),
                        "downloaded_bytes": int(msg.get("downloaded_bytes") or 0),
                        "total_bytes": int(msg.get("total_bytes") or 0),
                        "speed": float(msg.get("speed") or 0.0),
                        "eta": int(msg.get("eta") or 0),
                        "filename": msg.get("filename") or display_name or target_path.name,
                    }
                    if item_cnt > 1:
                        hook_data["item_index"] = item_idx
                        hook_data["item_count"] = item_cnt
                        hook_data["item_title"] = display_name or target_path.name
                    progress_hook(hook_data)
        proc.wait(timeout=10)
    finally:
        stop_watcher.set()
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    if cancel_event is not None and cancel_event.is_set():
        raise ExtractionCancelled("İndirme iptal edildi.")

    if proc.returncode == 0 and target_path.is_file():
        return target_path

    if "iptal" in last_error.lower():
        raise ExtractionCancelled("İndirme iptal edildi.")

    if last_error:
        raise ExtractionError(last_error)

    return None
