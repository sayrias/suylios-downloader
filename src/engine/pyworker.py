#!/usr/bin/env python3
"""
Suylios Downloader - Kalici Python Extractor Daemon (suylios-pyworker).

Rust Hibrit cekirdek ile stdin/stdout uzerinden NDJSON protokoluyle haberlesir.
Acilista runtime overlay dizinlerini (`hotfixes_active`, `plugins_active`,
`python_overlay_active`) sys.path en basina ekler ve mega.vault yamasini uygular.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _activate_runtime_overlays(data_dir: Path) -> dict[str, str]:
    runtime_dir = data_dir / "runtime"
    active_paths: dict[str, str] = {}

    # Öncelik sırası: 1) hotfixes_active, 2) plugins_active, 3) python_overlay_active
    overlay_py = runtime_dir / "python_overlay_active"
    if overlay_py.is_dir():
        p_str = str(overlay_py)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)
        active_paths["python_overlay"] = p_str

    plugins_dir = runtime_dir / "plugins_active"
    if plugins_dir.is_dir():
        for sub in sorted(plugins_dir.iterdir()):
            if sub.is_dir():
                s_str = str(sub)
                if s_str not in sys.path:
                    sys.path.insert(0, s_str)
        active_paths["plugins_overlay"] = str(plugins_dir)

    hotfixes_dir = runtime_dir / "hotfixes_active"
    if hotfixes_dir.is_dir():
        for sub in sorted(hotfixes_dir.iterdir()):
            if sub.is_dir():
                s_str = str(sub)
                if s_str not in sys.path:
                    sys.path.insert(0, s_str)
        active_paths["hotfixes_overlay"] = str(hotfixes_dir)

    try:
        from src.extractors.mega_ext import _ensure_mega_vault_patched
        _ensure_mega_vault_patched()
    except Exception:
        pass

    return active_paths


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class WorkerSession:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.overlays = _activate_runtime_overlays(data_dir)
        self.tasks: dict[str, dict[str, threading.Event]] = {}
        self.lock = threading.Lock()

    def handle_request(self, req: dict[str, Any]) -> None:
        req_id = req.get("id", "")
        method = req.get("method", "")
        params = req.get("params") or {}

        if method == "health":
            import yt_dlp
            import gallery_dl
            _emit({
                "id": req_id,
                "ok": True,
                "result": {
                    "status": "ready",
                    "yt_dlp": getattr(yt_dlp.version, "__version__", "unknown"),
                    "gallery_dl": getattr(gallery_dl, "__version__", "unknown"),
                    "overlays": self.overlays,
                },
            })
            return

        if method == "reload_overlays":
            self.overlays = _activate_runtime_overlays(self.data_dir)
            importlib.invalidate_caches()
            _emit({"id": req_id, "ok": True, "result": {"overlays": self.overlays}})
            return

        if method in ("cancel", "pause", "resume"):
            task_id = str(params.get("task_id", ""))
            with self.lock:
                ev = self.tasks.get(task_id)
            if ev:
                if method == "cancel":
                    ev["cancel"].set()
                    ev["pause"].set()
                elif method == "pause":
                    ev["pause"].clear()
                elif method == "resume":
                    ev["pause"].set()
            _emit({"id": req_id, "ok": True})
            return

        if method == "extract_or_handoff":
            threading.Thread(
                target=self._run_extract_or_handoff,
                args=(req_id, params),
                daemon=True,
            ).start()
            return

        _emit({"id": req_id, "ok": False, "error": f"Unknown method: {method}"})

    def _run_extract_or_handoff(self, req_id: str, params: dict[str, Any]) -> None:
        url = str(params.get("url", ""))
        task_id = str(params.get("task_id", req_id))
        try:
            from src.engine.download_manager import DownloadManager
            dm = DownloadManager()
            ext = dm._get_extractor(url)
            info = ext.extract_info(url)
            _emit({
                "id": req_id,
                "ok": True,
                "task_id": task_id,
                "result": info,
            })
        except Exception as exc:
            _emit({
                "id": req_id,
                "ok": False,
                "task_id": task_id,
                "error": str(exc),
            })


def main() -> None:
    from src.config import config
    data_dir = config.get_data_dir()
    session = WorkerSession(data_dir)
    _emit({"event": "worker_ready", "overlays": session.overlays})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if isinstance(req, dict):
                session.handle_request(req)
        except Exception as exc:
            _emit({"ok": False, "error": f"Invalid JSON-RPC message: {exc}"})


if __name__ == "__main__":
    main()
