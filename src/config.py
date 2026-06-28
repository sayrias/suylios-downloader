"""
Suylios Downloader - Application Configuration Manager.

Manages persistent application settings via a JSON config file.
Supports portable mode (relative ./bin/) and installed mode.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: dict[str, Any] = {
    "download_dir": "",          # resolved at runtime
    "max_concurrent": 3,
    "speed_limit": 0,            # 0 = unlimited, otherwise bytes/sec
    "default_format": "auto",    # auto | mp3 | mp4 | audio
    "default_quality": "best",   # best | 1080 | 720 | 480 | 360
    "mp3_bitrate": "320",        # kbps
    "proxy": "",
    "clipboard_monitor": True,
    "create_subfolders": True,
    "language": "tr",
    "start_minimized": False,
    "theme": "basit-beyaz",
    "site_settings": {
        "youtube": {"folder": "YouTube", "quality": "best", "cookies": ""},
        "bunkr": {"folder": "Bunkr", "quality": "best", "cookies": ""},
        "gofile": {"folder": "Gofile", "quality": "best", "cookies": ""},
        "pixeldrain": {"folder": "Pixeldrain", "quality": "best", "cookies": ""},
        "tiktok": {"folder": "TikTok", "quality": "best", "cookies": ""},
        "twitter": {"folder": "Twitter", "quality": "best", "cookies": ""},
        "instagram": {"folder": "Instagram", "quality": "best", "cookies": ""},
        "reddit": {"folder": "Reddit", "quality": "best", "cookies": ""},
        "pornhub": {"folder": "Pornhub", "quality": "best", "cookies": ""},
        "xvideos": {"folder": "XVideos", "quality": "best", "cookies": ""},
        "rule34": {"folder": "Rule34", "quality": "best", "cookies": ""},
        "hanime": {"folder": "Hanime", "quality": "best", "cookies": ""},
        "hitomi": {"folder": "Hitomi", "quality": "best", "cookies": ""},
        "ehentai": {"folder": "E-Hentai", "quality": "best", "cookies": ""},
        "other": {"folder": "Others", "quality": "best", "cookies": ""},
    },
}


def _get_app_root() -> Path:
    """Return the application root directory.

    When frozen with Nuitka / PyInstaller the executable sits in the root.
    During development, *this* file is at ``src/config.py``, so the root
    is one level up.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _is_portable() -> bool:
    """Detect portable mode by looking for ./bin/ or ./src/ next to the executable / project root."""
    root = _get_app_root()
    return getattr(sys, "frozen", False) or (root / "bin").is_dir() or (root / "src").is_dir() or (root / "config.json").exists()


def _default_download_dir() -> str:
    """Return the default download directory based on the deployment mode."""
    if _is_portable():
        return str(_get_app_root() / "Downloads")
    return str(Path.home() / "Downloads" / "Suylios")


def _config_file_path() -> Path:
    """Return the path to the JSON config file."""
    if _is_portable():
        return _get_app_root() / "config.json"
    appdata = os.environ.get("APPDATA", str(Path.home()))
    cfg_dir = Path(appdata) / "SuyliosDownloader"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "config.json"


def _history_file_path() -> Path:
    """Return the path to the JSON history file."""
    if _is_portable():
        return _get_app_root() / "history.json"
    appdata = os.environ.get("APPDATA", str(Path.home()))
    cfg_dir = Path(appdata) / "SuyliosDownloader"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "history.json"


class Config:
    """Thread-safe, JSON-backed application configuration."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._path: Path = _config_file_path()
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load settings from disk, falling back to defaults."""
        defaults = dict(_DEFAULT_SETTINGS)
        defaults["download_dir"] = _default_download_dir()
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                # Merge: stored values override defaults but unknown keys
                # in defaults are preserved.
                defaults.update(stored)
                logger.info("Config loaded from %s", self._path)
            else:
                logger.info("No config file found – using defaults.")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read config file: %s – using defaults.", exc)
        self._data = defaults

    def save(self) -> None:
        """Persist current settings to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
            logger.debug("Config saved to %s", self._path)
        except OSError as exc:
            logger.error("Failed to save config: %s", exc)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for *key*, or *default* if not present."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set *key* to *value* (does **not** auto-save)."""
        self._data[key] = value

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow copy of all settings."""
        return dict(self._data)

    def reset(self) -> dict[str, Any]:
        """Reset settings to default and save to disk."""
        defaults = dict(_DEFAULT_SETTINGS)
        defaults["download_dir"] = _default_download_dir()
        self._data = defaults
        self.save()
        return self.as_dict()

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def get_ffmpeg_path(self) -> str | None:
        """Return the absolute path to ``ffmpeg.exe`` (or ``ffmpeg`` on
        Linux/macOS), or ``None`` if it cannot be found."""
        # 1. Bundled binary
        if hasattr(sys, "_MEIPASS"):
            meipass_bundled = Path(sys._MEIPASS) / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if meipass_bundled.is_file():
                return str(meipass_bundled)
        root = _get_app_root()
        bundled = root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if bundled.is_file():
            return str(bundled)
        # 2. System PATH
        import shutil
        system = shutil.which("ffmpeg")
        if system:
            return system
        return None

    def get_download_dir(self) -> str:
        """Return the current download directory, creating it if needed."""
        dl_dir = self.get("download_dir", _default_download_dir())
        default_dl = _default_download_dir()
        if not dl_dir or dl_dir.endswith("Downloads") or dl_dir.endswith("Suylios") or "Downloader" in dl_dir:
            if dl_dir != default_dl:
                dl_dir = default_dl
                self.set("download_dir", dl_dir)
        Path(dl_dir).mkdir(parents=True, exist_ok=True)
        return dl_dir

    @staticmethod
    def is_portable() -> bool:
        """Return ``True`` when running in portable mode."""
        return _is_portable()

    @staticmethod
    def get_app_root() -> Path:
        """Return the application root directory."""
        return _get_app_root()

    def get_history(self) -> list[dict[str, Any]]:
        """Load and return the download history list."""
        h_path = _history_file_path()
        if not h_path.exists():
            return []
        try:
            with open(h_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            modified = False
            for item in data:
                if not item.get("total_bytes") and item.get("filename"):
                    p = Path(item["filename"])
                    if p.exists():
                        try:
                            item["total_bytes"] = p.stat().st_size
                            modified = True
                        except Exception:
                            pass
            if modified:
                try:
                    with open(h_path, "w", encoding="utf-8") as fw:
                        json.dump(data, fw, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            return data
        except Exception as exc:
            logger.error("Failed to load history: %s", exc)
            return []

    def add_to_history(self, task_snapshot: dict[str, Any]) -> None:
        """Append a completed task snapshot to the history file."""
        import time
        h_path = _history_file_path()
        history = self.get_history()
        
        total_b = task_snapshot.get("total_bytes") or task_snapshot.get("total_size") or 0
        fn = task_snapshot.get("filename", "")
        if not total_b and fn:
            p = Path(fn)
            if p.exists():
                try:
                    total_b = p.stat().st_size
                except Exception:
                    pass

        item = {
            "id": task_snapshot.get("id"),
            "title": task_snapshot.get("title", "Bilinmeyen Dosya"),
            "url": task_snapshot.get("url", ""),
            "filename": fn,
            "total_bytes": total_b,
            "extractor_name": task_snapshot.get("extractor_name", "Diğer"),
            "timestamp": int(time.time()),
            "date_str": time.strftime("%d.%m.%Y %H:%M"),
        }
        
        history = [h for h in history if h.get("id") != item["id"] and h.get("url") != item["url"]]
        history.insert(0, item)
        history = history[:500]
        
        try:
            with open(h_path, "w", encoding="utf-8") as fh:
                json.dump(history, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error("Failed to save history: %s", exc)

    def delete_history_item(self, item_id: str) -> bool:
        """Delete a single item from history by ID."""
        h_path = _history_file_path()
        history = self.get_history()
        new_history = [h for h in history if str(h.get("id")) != str(item_id)]
        if len(new_history) != len(history):
            try:
                with open(h_path, "w", encoding="utf-8") as fh:
                    json.dump(new_history, fh, indent=2, ensure_ascii=False)
                return True
            except Exception as exc:
                logger.error("Failed to update history: %s", exc)
        return False

    def clear_history(self) -> bool:
        """Clear all download history."""
        h_path = _history_file_path()
        try:
            with open(h_path, "w", encoding="utf-8") as fh:
                json.dump([], fh)
            return True
        except Exception as exc:
            logger.error("Failed to clear history: %s", exc)
            return False


# Module-level singleton – importable as ``from src.config import config``.
config = Config()
