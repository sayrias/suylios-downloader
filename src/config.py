"""
Suylios Downloader - Application Configuration Manager.

Manages persistent application settings via a JSON config file.
Supports portable mode (relative ./bin/) and installed mode.
"""

import json
import logging
import os
import shutil
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
    "segmented_download": True,  # enable multi-connection HTTP Range download
    "segmented_connections": 8,  # maximum parallel connections per file (2..32)
    "segmented_mode": "adaptive",  # adaptive | large (>= 5 MB) | always (>= 1 MB) | turbo (all >= 512 KB, max parts)
    "default_format": "auto",    # auto | mp3 | mp4 | audio
    "default_quality": "best",   # best | 1080 | 720 | 480 | 360
    "mp3_bitrate": "320",        # kbps
    "proxy": "",
    "clipboard_monitor": True,
    "clipboard_auto_download": False,
    "create_subfolders": True,
    "language": "tr",
    "start_minimized": False,
    "auto_start_windows": False,
    "background_mode": False,
    "auto_shutdown": False,
    "embed_metadata": True,
    "download_subtitles": False,
    "auto_compress": False,
    "compress_format": "zip",
    "theme": "basit-beyaz",
    "site_settings": {
        "youtube": {"folder": "YouTube", "quality": "best", "cookies": ""},
        "bunkr": {"folder": "Bunkr", "quality": "best", "cookies": ""},
        "gofile": {
            "folder": "Gofile",
            "quality": "best",
            "cookies": "",
            "token": "",
            "account_id": "",
        },
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
        "twitch": {"folder": "Twitch", "quality": "best", "cookies": ""},
        "vimeo": {"folder": "Vimeo", "quality": "best", "cookies": ""},
        "soundcloud": {"folder": "SoundCloud", "quality": "best", "cookies": ""},
        "dailymotion": {"folder": "Dailymotion", "quality": "best", "cookies": ""},
        "imgur": {"folder": "Imgur", "quality": "best", "cookies": ""},
        "flickr": {"folder": "Flickr", "quality": "best", "cookies": ""},
        "kemono": {"folder": "Kemono", "quality": "best", "cookies": ""},
        "coomer": {"folder": "Coomer", "quality": "best", "cookies": ""},
        "erome": {"folder": "Erome", "quality": "best", "cookies": ""},
        "xhamster": {"folder": "xHamster", "quality": "best", "cookies": ""},
        "cyberdrop": {"folder": "Cyberdrop", "quality": "best", "cookies": ""},
        "mega": {"folder": "Mega", "quality": "best", "cookies": ""},
        "4chan": {"folder": "4chan", "quality": "best", "cookies": ""},
        "bluesky": {"folder": "Bluesky", "quality": "best", "cookies": ""},
        "simpcity": {"folder": "SimpCity", "quality": "best", "cookies": ""},
        "motherless": {"folder": "Motherless", "quality": "best", "cookies": ""},
        "goonbox": {"folder": "Goonbox", "quality": "best", "cookies": ""},
        "other": {"folder": "Others", "quality": "best", "cookies": ""},
    },
    "blocklist": [
        "instagram.com",
        "tiktok.com",
        "facebook.com",
        "youtube.com"
    ],
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
    """Detect if running as an explicit portable package vs standard install/dev."""
    root = _get_app_root()
    # If setup flag exists or in Program Files, it's definitely NOT portable
    if (root / "installed_by_setup.flag").exists() or "program files" in str(root).lower():
        return False
    # If onefile executable (where sys._MEIPASS is different from exe directory), NOT portable (uses appdata)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            if Path(sys._MEIPASS).resolve() != root.resolve():
                return False
        except Exception:
            pass
    # Only activate portable mode when portable.flag is explicitly present next to the app
    return (root / "portable.flag").exists()


def _get_user_data_root() -> Path:
    """Return the OS-standard per-user data folder for non-portable runs."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Suylios Downloader"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Suylios Downloader"
    xdg_data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg_data) / "Suylios Downloader"


def _get_user_downloads_base() -> Path:
    """Resolve the user's Downloads folder cleanly (honoring Linux XDG user-dirs)."""
    if sys.platform.startswith("linux"):
        xdg_dl = os.environ.get("XDG_DOWNLOAD_DIR")
        if xdg_dl:
            p = Path(os.path.expanduser(xdg_dl))
            if p.is_dir():
                return p
        user_dirs = Path.home() / ".config" / "user-dirs.dirs"
        if user_dirs.is_file():
            try:
                for line in user_dirs.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DOWNLOAD_DIR="):
                        raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                        raw = raw.replace("$HOME", str(Path.home()))
                        cand = Path(os.path.expanduser(raw))
                        if cand.is_dir():
                            return cand
            except Exception:
                pass
    return Path.home() / "Downloads"


def _default_download_dir() -> str:
    """Return the default download directory based on the deployment mode."""
    if _is_portable():
        dl_dir = _get_app_root() / "Downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        return str(dl_dir)
    dl_dir = _get_user_downloads_base() / "Suylios Downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    return str(dl_dir)


def _get_appdata_dir() -> Path:
    if _is_portable():
        data_dir = _get_app_root() / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        legacy_paths = [
            Path(os.environ.get("APPDATA", "")) / "Suylios Downloader",
            Path(os.environ.get("LOCALAPPDATA", "")) / "SuyliosDownloader",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Suylios Downloader" / "data",
        ]
        for fname in ("config.json", "history.json"):
            target_file = data_dir / fname
            if not target_file.exists():
                for leg_dir in legacy_paths:
                    leg_file = leg_dir / fname
                    if leg_file.exists() and leg_file.resolve() != target_file.resolve():
                        try:
                            shutil.copy2(leg_file, target_file)
                            logger.info("Migrated user data (%s) to portable folder from %s", fname, leg_file)
                            break
                        except Exception:
                            pass
        return data_dir

    cfg_dir = _get_user_data_root()
    cfg_dir.mkdir(parents=True, exist_ok=True)

    legacy_paths = [
        _get_app_root() / "data",
        Path(os.environ.get("LOCALAPPDATA", "")) / "SuyliosDownloader",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Suylios Downloader" / "data",
    ]
    for fname in ("config.json", "history.json"):
        target_file = cfg_dir / fname
        if not target_file.exists():
            for leg_dir in legacy_paths:
                leg_file = leg_dir / fname
                if leg_file.exists() and leg_file.resolve() != target_file.resolve():
                    try:
                        shutil.copy2(leg_file, target_file)
                        logger.info("Migrated user data (%s) to %s from %s", fname, target_file, leg_file)
                        break
                    except Exception as exc:
                        logger.warning("Could not migrate %s from %s: %s", fname, leg_file, exc)
    return cfg_dir


def _config_file_path() -> Path:
    """Return the path to the JSON config file."""
    return _get_appdata_dir() / "config.json"


def _history_file_path() -> Path:
    """Return the path to the JSON history file."""
    return _get_appdata_dir() / "history.json"


def _detect_initial_language() -> str:
    """Detect setup language from install_lang.ini or OS locale."""
    try:
        exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
        ini_path = exe_dir / "install_lang.ini"
        if ini_path.exists():
            content = ini_path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                if line.strip().lower().startswith("language="):
                    val = line.split("=", 1)[1].strip().lower()
                    if val in ("en", "tr"):
                        return val
    except Exception:
        pass

    try:
        import locale
        loc = locale.getdefaultlocale()[0] or ""
        if loc.lower().startswith("tr"):
            return "tr"
    except Exception:
        pass

    return "en"


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
                # Deep merge site_settings
                if "site_settings" in defaults and isinstance(defaults["site_settings"], dict):
                    for k, v in _DEFAULT_SETTINGS.get("site_settings", {}).items():
                        if k not in defaults["site_settings"] or not isinstance(defaults["site_settings"][k], dict):
                            defaults["site_settings"][k] = dict(v) if isinstance(v, dict) else v
                        elif isinstance(v, dict):
                            for sub_k, sub_v in v.items():
                                if sub_k not in defaults["site_settings"][k]:
                                    defaults["site_settings"][k][sub_k] = sub_v
                
                # Check if setup installer ran after config.json was saved and updated language choice
                try:
                    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
                    ini_path = exe_dir / "install_lang.ini"
                    if ini_path.exists() and ini_path.stat().st_mtime > self._path.stat().st_mtime + 2:
                        setup_lang = _detect_initial_language()
                        if setup_lang in ("en", "tr") and setup_lang != defaults.get("language"):
                            defaults["language"] = setup_lang
                            logger.info("Language updated from install_lang.ini: %s", setup_lang)
                except Exception:
                    pass

                logger.info("Config loaded from %s", self._path)
            else:
                defaults["language"] = _detect_initial_language()
                logger.info("No config file found – using defaults with detected language: %s", defaults["language"])
        except (json.JSONDecodeError, OSError) as exc:
            defaults["language"] = _detect_initial_language()
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
        dl_dir = self.get("download_dir", "")
        default_dl = _default_download_dir()
        in_app_root = False
        if dl_dir and not _is_portable():
            try:
                dl_res = Path(dl_dir).expanduser().resolve()
                app_root_dl = (_get_app_root() / "Downloads").resolve()
                in_app_root = (
                    dl_res == app_root_dl
                    or (dl_res.name == "Downloads" and dl_res.parent.name in ("Downloader", _get_app_root().name))
                )
            except Exception:
                in_app_root = dl_dir.rstrip("/\\").endswith("Downloader/Downloads")
        if not dl_dir or in_app_root or dl_dir.endswith("Suylios"):
            if dl_dir != default_dl:
                dl_dir = default_dl
                self.set("download_dir", dl_dir)
                self.save()
        Path(dl_dir).mkdir(parents=True, exist_ok=True)
        return dl_dir

    @staticmethod
    def clamp_segmented_connections(value: Any, default: int = 8) -> int:
        """Clamp configured maximum segmented connections to ``[2, 32]``."""
        try:
            conns = int(value)
        except (TypeError, ValueError):
            conns = default
        return max(2, min(32, conns))

    @staticmethod
    def normalize_segmented_mode(value: Any) -> str:
        """Normalize segmented download mode to ``adaptive``, ``large``, ``always``, or ``turbo``."""
        mode = str(value or "adaptive").strip().lower()
        if mode in ("aggressive", "idm"):
            return "turbo"
        if mode not in ("adaptive", "large", "always", "turbo"):
            return "adaptive"
        return mode

    def get_segmented_settings(self) -> tuple[bool, int, str]:
        """Return ``(enabled, max_connections, mode)`` clamped to safe bounds."""
        enabled = bool(self.get("segmented_download", True))
        conns = self.clamp_segmented_connections(self.get("segmented_connections", 8))
        mode = self.normalize_segmented_mode(self.get("segmented_mode", "adaptive"))
        return enabled, conns, mode

    @staticmethod
    def is_portable() -> bool:
        """Return ``True`` when running in portable mode."""
        return _is_portable()

    @staticmethod
    def get_app_root() -> Path:
        """Return the application root directory."""
        return _get_app_root()

    @staticmethod
    def get_data_dir() -> Path:
        """Return the active persistent user data directory."""
        return _get_appdata_dir()

    def get_history(self) -> list[dict[str, Any]]:
        """Load and return the download history list."""
        h_path = _history_file_path()
        if not h_path.exists():
            self._history_cache = []
            self._history_mtime = None
            return []
        try:
            mtime = h_path.stat().st_mtime_ns
            if getattr(self, "_history_mtime", None) == mtime and hasattr(self, "_history_cache"):
                return list(self._history_cache)
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
                    mtime = h_path.stat().st_mtime_ns
                except Exception:
                    pass
            self._history_cache = data
            self._history_mtime = mtime
            return list(data)
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
            "thumbnail": task_snapshot.get("thumbnail", ""),
            "format_type": task_snapshot.get("format_type", ""),
            "quality": task_snapshot.get("quality", ""),
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
            self._history_cache = history
            self._history_mtime = h_path.stat().st_mtime_ns
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
                self._history_cache = new_history
                self._history_mtime = h_path.stat().st_mtime_ns
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
            self._history_cache = []
            self._history_mtime = h_path.stat().st_mtime_ns
            return True
        except Exception as exc:
            logger.error("Failed to clear history: %s", exc)
            return False


# Module-level singleton – importable as ``from src.config import config``.
config = Config()
