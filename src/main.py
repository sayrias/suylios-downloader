"""
Suylios Downloader - Main Application Entry Point.

Launches a frameless PyWebView window with a JavaScript-callable Bridge
API that exposes download management, settings, and OS interaction.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Ensure `from src.*` imports work when running as `python src/main.py`
# ---------------------------------------------------------------------------
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import webview
except ImportError:
    _venv_py = Path(_project_root) / "venv" / "Scripts" / "python.exe"
    if _venv_py.is_file() and os.path.normcase(sys.executable) != os.path.normcase(str(_venv_py)):
        print("🔄 Sanal ortam (venv) algılandı, venv ile yeniden başlatılıyor...")
        _main_script = str(Path(__file__).resolve())
        sys.exit(subprocess.call([str(_venv_py), _main_script, *sys.argv[1:]]))
    else:
        print("📦 'pywebview' eksik! Otomatik yükleniyor...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(Path(_project_root) / "requirements.txt")], check=True)
        import webview

from src.config import config
from src.engine.download_manager import DownloadManager

# ---------------------------------------------------------------------------
# Fix Windows console encoding – prevents UnicodeEncodeError crashes
# in background threads (e.g. yt-dlp progress hooks with non-Latin chars)
# ---------------------------------------------------------------------------
if os.name == "nt":
    import io
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    else:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    else:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("suylios")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "Suylios Downloader"
APP_VERSION = "1.1.0"
APP_GITHUB = "https://github.com/sayrias/suylios-downloader"

# ---------------------------------------------------------------------------
# UI path resolution
# ---------------------------------------------------------------------------


def _resolve_ui_path() -> str:
    """Return the absolute path to the ``index.html`` of the frontend.

    When frozen (Nuitka / PyInstaller) the UI lives next to the
    executable.  In development mode it lives relative to the project
    root.
    """
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent

    # Check common locations
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "ui" / "index.html")
    candidates.extend([
        # Development layout: src/ui/index.html
        Path(__file__).resolve().parent / "ui" / "index.html",
        root / "ui" / "index.html",
        root / "src" / "ui" / "index.html",
        root / "frontend" / "dist" / "index.html",
        root / "frontend" / "index.html",
        root / "dist" / "index.html",
        root / "index.html",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    # Fallback – return a path even if it does not yet exist so that
    # pywebview shows its own error page instead of crashing.
    default = Path(__file__).resolve().parent / "ui" / "index.html"
    logger.warning("UI file not found – expected at %s", default)
    return str(default)


# ---------------------------------------------------------------------------
# Bridge API
# ---------------------------------------------------------------------------


class Bridge:
    """JavaScript ↔ Python bridge exposed to the PyWebView frontend.

    Every public method (no leading ``_``) is callable from JavaScript
    via ``window.pywebview.api.<method>(...)``.
    """

    def __init__(self) -> None:
        self._dm = DownloadManager()
        self._window: Optional[webview.Window] = None
        self._tray_icon = None
        self._force_quit = False
        self._auto_shutdown_mode: str = ""  # "" | "shutdown" | "sleep"

    def set_window(self, window: webview.Window) -> None:
        """Store a reference to the host window (called once at start)."""
        self._window = window

    # ------------------------------------------------------------------
    # Download management
    # ------------------------------------------------------------------

    def add_download(
        self, url: str, format_type: str = "auto", quality: str = "best",
        start_time: str = "", end_time: str = "",
        embed_metadata: bool = True, download_subtitles: bool = False,
        keep_original: bool = False,
    ) -> dict[str, Any]:
        """Enqueue a new download and return its task snapshot."""
        try:
            task = self._dm.add_task(
                url=url, format_type=format_type, quality=quality,
                start_time=start_time, end_time=end_time,
                embed_metadata=embed_metadata,
                download_subtitles=download_subtitles,
                keep_original=keep_original,
            )
            return {"ok": True, "task": task.to_dict()}
        except Exception as exc:
            logger.error("add_download failed: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

    def pause_download(self, task_id: str) -> dict[str, Any]:
        success = self._dm.pause_task(task_id)
        return {"ok": success}

    def resume_download(self, task_id: str) -> dict[str, Any]:
        success = self._dm.resume_task(task_id)
        return {"ok": success}

    def cancel_download(self, task_id: str) -> dict[str, Any]:
        success = self._dm.cancel_task(task_id)
        return {"ok": success}

    def remove_download(self, task_id: str) -> dict[str, Any]:
        success = self._dm.remove_task(task_id)
        return {"ok": success}

    def get_downloads(self) -> list[dict[str, Any]]:
        """Return snapshots of every download task."""
        return self._dm.get_all_tasks()

    def get_history(self) -> list[dict[str, Any]]:
        """Return download history list."""
        return config.get_history()

    def clear_history(self) -> dict[str, Any]:
        """Clear download history."""
        success = config.clear_history()
        return {"ok": success}

    def delete_history_item(self, item_id: str) -> dict[str, Any]:
        """Delete a single item from history."""
        success = config.delete_history_item(item_id)
        return {"ok": success}

    def show_desktop_notification(self, title: str, message: str) -> dict[str, Any]:
        """Show a native Windows desktop notification with custom app name."""
        try:
            t_esc = str(title).replace("'", "''").replace("<", "&lt;").replace(">", "&gt;")
            m_esc = str(message).replace("'", "''").replace("<", "&lt;").replace(">", "&gt;")
            ps = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

            $template = @"
            <toast>
                <visual>
                    <binding template='ToastGeneric'>
                        <text>{t_esc}</text>
                        <text>{m_esc}</text>
                    </binding>
                </visual>
            </toast>
"@
            $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
            $xml.LoadXml($template)
            $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Suylios Downloader").Show($toast)
            """
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], creationflags=subprocess.CREATE_NO_WINDOW)
            return {"ok": True}
        except Exception as exc:
            logger.error("Failed to show notification: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        d = config.as_dict()
        d["download_path"] = d.get("download_dir", "")
        d["subfolders"] = d.get("create_subfolders", True)
        d["concurrent_downloads"] = d.get("max_concurrent", 3)
        d["start_minimized"] = d.get("start_minimized", False)
        d["auto_start_windows"] = d.get("auto_start_windows", False)
        d["theme"] = d.get("theme", "basit-beyaz")
        return d

    def save_settings(self, settings_json: str) -> dict[str, Any]:
        """Accept a JSON string of settings, merge, and persist."""
        try:
            incoming = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
            key_map = {
                "download_path": "download_dir",
                "subfolders": "create_subfolders",
                "concurrent_downloads": "max_concurrent",
            }
            for key, value in incoming.items():
                mapped_key = key_map.get(key, key)
                config.set(mapped_key, value)
                config.set(key, value)
            config.save()
            if "concurrent_downloads" in incoming or "max_concurrent" in incoming:
                new_conc = int(incoming.get("concurrent_downloads", incoming.get("max_concurrent", 3)))
                self._dm.update_max_concurrent(new_conc)
            if "auto_start_windows" in incoming:
                self._set_windows_autostart(bool(incoming["auto_start_windows"]))
            return {"ok": True, "success": True}
        except Exception as exc:
            logger.error("save_settings failed: %s", exc)
            return {"ok": False, "success": False, "error": str(exc)}

    def _set_windows_autostart(self, enable: bool) -> None:
        """Register or unregister app from Windows startup registry."""
        if sys.platform != "win32":
            return
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            app_name = "SuyliosDownloader"
            if enable:
                exe_path = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{Path(__file__).resolve()}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
                logger.info("Windows autostart enabled: %s", exe_path)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                    logger.info("Windows autostart disabled.")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as exc:
            logger.warning("Failed to set Windows autostart: %s", exc)

    def reset_settings(self) -> dict[str, Any]:
        """Reset all configuration to default values."""
        try:
            defaults = config.reset()
            self._dm.update_max_concurrent(defaults.get("max_concurrent", 3))
            # Map keys back to UI expected keys
            defaults["download_path"] = defaults.get("download_dir", "")
            defaults["subfolders"] = defaults.get("create_subfolders", True)
            defaults["concurrent_downloads"] = defaults.get("max_concurrent", 3)
            return {"ok": True, "success": True, "settings": defaults}
        except Exception as exc:
            logger.error("reset_settings failed: %s", exc)
            return {"ok": False, "success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # OS interactions
    # ------------------------------------------------------------------

    def open_file_location(self, task_id: str) -> dict[str, Any]:
        """Open the folder containing the downloaded file in the OS
        file explorer."""
        try:
            task_dict = self._dm.get_task(task_id)
            if not task_dict:
                return {"ok": False, "error": "Task not found."}
            filepath = task_dict.get("filename", "")
            if not filepath or not Path(filepath).exists():
                # Fall back to the download directory
                filepath = config.get_download_dir()
            folder = str(Path(filepath).parent) if Path(filepath).is_file() else filepath
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
            return {"ok": True}
        except Exception as exc:
            logger.error("open_file_location failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def open_url(self, url: Any) -> dict[str, Any]:
        """Open an external URL in the system's default web browser."""
        try:
            if isinstance(url, dict):
                url = url.get("url", "")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                webbrowser.open(url)
            return {"ok": True}
        except Exception as exc:
            logger.error("open_url failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def get_clipboard_text(self) -> str:
        """Read the current clipboard text (best-effort)."""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            if text:
                return str(text)
        except Exception:
            pass

        try:
            if os.name == "nt":
                import ctypes
                import time
                user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                opened = False
                for _ in range(5):
                    if user32.OpenClipboard(0):
                        opened = True
                        break
                    time.sleep(0.05)
                if not opened:
                    return ""
                try:
                    for fmt in (13, 1):  # CF_UNICODETEXT, CF_TEXT
                        handle = user32.GetClipboardData(fmt)
                        if handle:
                            if fmt == 13:
                                kernel32.GlobalLock.restype = ctypes.c_wchar_p
                                text = kernel32.GlobalLock(handle) or ""
                            else:
                                kernel32.GlobalLock.restype = ctypes.c_char_p
                                raw = kernel32.GlobalLock(handle)
                                text = raw.decode("utf-8", errors="ignore") if raw else ""
                            kernel32.GlobalUnlock(handle)
                            if text:
                                return str(text)
                    return ""
                finally:
                    user32.CloseClipboard()
            else:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, text=True, timeout=3,
                )
                return result.stdout if result.returncode == 0 else ""
        except Exception:
            return ""

    def pick_folder(self) -> str:
        """Open a native folder-picker dialog and return the chosen path."""
        try:
            if self._window:
                result = self._window.create_file_dialog(
                    webview.FOLDER_DIALOG,
                )
                if result and len(result) > 0:
                    return result[0]
        except Exception as exc:
            logger.error("pick_folder failed: %s", exc)
        return ""

    def pick_file(self, file_types: Any = ("Text Dosyaları (*.txt)", "Tüm Dosyalar (*.*)")) -> str:
        """Open a native file-picker dialog and return the chosen path."""
        try:
            if self._window:
                if isinstance(file_types, list):
                    file_types = tuple(f if isinstance(f, str) else f"{f[0]}" for f in file_types)
                result = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    file_types=file_types,
                )
                if result and len(result) > 0:
                    return result[0]
        except Exception as exc:
            logger.error("pick_file failed: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Window controls
    # ------------------------------------------------------------------

    def minimize_window(self) -> None:
        if self._window:
            self._window.minimize()

    def _get_work_area(self) -> tuple[int, int, int, int]:
        """Get monitor working area excluding Windows taskbar."""
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore
            hwnd = user32.GetForegroundWindow()
            hmonitor = user32.MonitorFromWindow(hwnd, 2)

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong),
                ]

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
                w = mi.rcWork.right - mi.rcWork.left
                h = mi.rcWork.bottom - mi.rcWork.top
                return mi.rcWork.left, mi.rcWork.top, w, h
        except Exception as exc:
            logger.error("_get_work_area error: %s", exc)
        return 0, 0, 1920, 1040

    def maximize_window(self) -> None:
        """Toggle maximise to monitor work area without hiding taskbar."""
        if not self._window:
            return
        is_max = getattr(self, "_is_maximized", False)
        try:
            if is_max:
                saved = getattr(self, "_normal_geom", (100, 100, 1100, 700))
                self._window.move(saved[0], saved[1])
                self._window.resize(saved[2], saved[3])
                self._is_maximized = False
            else:
                self._normal_geom = (
                    getattr(self._window, "x", 100),
                    getattr(self._window, "y", 100),
                    getattr(self._window, "width", 1100),
                    getattr(self._window, "height", 700),
                )
                x, y, w, h = self._get_work_area()
                self._window.move(x, y)
                self._window.resize(w, h)
                self._is_maximized = True
        except Exception as exc:
            logger.error("maximize_window failed: %s", exc)

    def close_window(self) -> dict[str, Any]:
        """Intercept window close request from UI."""
        background_mode = config.get("background_mode", True)
        active = self._dm.get_active_count()
        scheduled = sum(1 for t in self._dm.get_all_tasks() if t.get("scheduled_at", 0) > 0)

        if background_mode or active > 0 or scheduled > 0:
            self.hide_to_tray()
            if getattr(self, "_first_tray_notify", True):
                self._first_tray_notify = False
                if hasattr(self, "_tray_icon") and self._tray_icon:
                    try:
                        self._tray_icon.notify(
                            "Suylios Downloader arka planda çalışmaya devam ediyor. Açmak veya kapatmak için gizli simgelere göz atın.",
                            "Uygulama Arka Plana Taşındı"
                        )
                    except Exception as e:
                        logger.warning("Tray notify failed: %s", e)
            return {"action": "hidden"}

        self.force_quit()
        return {"action": "closed"}


    # ------------------------------------------------------------------
    # App info
    # ------------------------------------------------------------------

    def get_app_info(self) -> dict[str, str]:
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            "github_url": APP_GITHUB,
        }

    # ------------------------------------------------------------------
    # Localization / Locales
    # ------------------------------------------------------------------

    def get_available_locales(self) -> list[dict[str, str]]:
        """Return list of available localization languages."""
        locales = [{"code": "tr", "name": "Türkçe"}, {"code": "en", "name": "English"}]
        try:
            ui_dir = Path(_resolve_ui_path()).parent
            loc_dir = ui_dir / "locales"
            if loc_dir.exists():
                for sub in loc_dir.iterdir():
                    if sub.is_dir() and sub.name not in ["tr", "en"]:
                        json_file = sub / f"{sub.name}.json"
                        if json_file.exists():
                            locales.append({"code": sub.name, "name": sub.name.upper()})
        except Exception as exc:
            logger.error("Error scanning locales: %s", exc)
        return locales

    def get_locale(self, lang: str) -> dict[str, str]:
        """Load and return translation dictionary for the requested language code."""
        try:
            ui_dir = Path(_resolve_ui_path()).parent
            for p in [ui_dir / "locales" / lang / f"{lang}.json", ui_dir / "locales" / f"{lang}.json"]:
                if p.exists():
                    with open(p, "r", encoding="utf-8") as fh:
                        return json.load(fh)
        except Exception as exc:
            logger.error("Error loading locale %s: %s", lang, exc)
        return {}

    # ------------------------------------------------------------------
    # v1.2.0 – Batch, Scheduled, Auto-shutdown, Media Preview, Tray
    # ------------------------------------------------------------------

    def add_batch_downloads(
        self, urls_text: str, format_type: str = "auto", quality: str = "best",
        embed_metadata: bool = True, download_subtitles: bool = False,
    ) -> dict[str, Any]:
        """Parse a newline-separated list of URLs and enqueue all."""
        try:
            urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
            if not urls:
                return {"ok": False, "error": "No valid URLs provided."}
            tasks = self._dm.add_batch_tasks(
                urls=urls, format_type=format_type, quality=quality,
                embed_metadata=embed_metadata, download_subtitles=download_subtitles,
            )
            return {"ok": True, "count": len(tasks), "tasks": [t.to_dict() for t in tasks]}
        except Exception as exc:
            logger.error("add_batch_downloads failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def schedule_download(
        self, url: str, format_type: str = "auto", quality: str = "best",
        scheduled_at: int = 0, start_time: str = "", end_time: str = "",
    ) -> dict[str, Any]:
        """Enqueue a download to start at a specific Unix timestamp."""
        try:
            task = self._dm.add_task(
                url=url, format_type=format_type, quality=quality,
                scheduled_at=scheduled_at, start_time=start_time, end_time=end_time,
            )
            return {"ok": True, "task": task.to_dict()}
        except Exception as exc:
            logger.error("schedule_download failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def set_auto_shutdown(self, mode: str) -> dict[str, Any]:
        """Set auto-shutdown mode. mode: '' | 'shutdown' | 'sleep'"""
        self._auto_shutdown_mode = mode
        logger.info("Auto-shutdown mode set to: %s", mode)
        return {"ok": True, "mode": mode}

    def get_auto_shutdown(self) -> dict[str, Any]:
        return {"mode": self._auto_shutdown_mode}

    def has_active_downloads(self) -> dict[str, Any]:
        """Return whether any downloads are actively running or scheduled."""
        active = self._dm.get_active_count()
        scheduled = sum(
            1 for t in self._dm.get_all_tasks()
            if t.get("scheduled_at", 0) > 0
        )
        return {"active": active, "scheduled": scheduled, "any": (active + scheduled) > 0}

    def get_file_for_preview(self, task_id: str) -> dict[str, Any]:
        """Return file path info for the built-in media player preview."""
        try:
            hist = config.get_history()
            item = next((h for h in hist if h.get("id") == task_id), None)
            if not item:
                task_dict = self._dm.get_task(task_id)
                item = task_dict
            if not item:
                return {"ok": False, "error": "Not found"}
            filepath = item.get("filename", "")
            if not filepath or not Path(filepath).exists():
                return {"ok": False, "error": "File not found on disk."}
            ext = Path(filepath).suffix.lower().lstrip(".")
            video_exts = {"mp4", "mkv", "webm", "mov", "avi"}
            audio_exts = {"mp3", "flac", "m4a", "wav", "ogg", "aac"}
            media_type = "video" if ext in video_exts else ("audio" if ext in audio_exts else "unknown")
            file_url = "file:///" + filepath.replace("\\", "/")
            return {"ok": True, "url": file_url, "type": media_type, "title": item.get("title", ""), "thumbnail": item.get("thumbnail", "")}
        except Exception as exc:
            logger.error("get_file_for_preview failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def hide_to_tray(self) -> None:
        """Hide the main window to the system tray."""
        if self._window:
            try:
                self._window.hide()
            except Exception as exc:
                logger.error("hide_to_tray failed: %s", exc)

    def show_from_tray(self) -> None:
        """Restore the main window from the system tray."""
        if self._window:
            try:
                self._window.show()
            except Exception as exc:
                logger.error("show_from_tray failed: %s", exc)

    def force_quit(self) -> None:
        """Force a complete application exit."""
        self._force_quit = True
        try:
            self._dm.shutdown()
            config.save()
            if self._tray_icon:
                self._tray_icon.stop()
        except Exception:
            pass
        if self._window:
            self._window.destroy()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def run_system_command(self, command: str) -> dict[str, Any]:
        """Execute a system-level power command: shutdown_init, shutdown_abort, sleep."""
        try:
            if command == "shutdown_init":
                # Schedules shutdown in 60 seconds
                subprocess.Popen(
                    ["shutdown", "/s", "/t", "60", "/c", "Suylios Downloader: downloads complete."],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                logger.info("System shutdown scheduled in 60 seconds.")
                return {"ok": True}
            elif command == "shutdown_abort":
                subprocess.Popen(
                    ["shutdown", "/a"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                logger.info("System shutdown aborted.")
                return {"ok": True}
            elif command == "sleep":
                subprocess.Popen(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                logger.info("System sleep triggered.")
                return {"ok": True}
            else:
                return {"ok": False, "error": f"Unknown command: {command}"}
        except Exception as exc:
            logger.error("run_system_command failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def shutdown(self) -> None:
        """Called on window close to release resources."""
        try:
            self._dm.shutdown()
            config.save()
        except Exception as exc:
            logger.error("Shutdown error: %s", exc)


def _create_tray_icon(bridge) -> None:
    """Initialize and run pystray system tray icon in background thread."""
    try:
        import pystray
        from PIL import Image, ImageDraw

        # Load or create tray icon
        icon_path = Path(_resolve_ui_path()).parent / "icon.ico"
        if icon_path.exists():
            try:
                img = Image.open(str(icon_path)).convert("RGBA").resize((32, 32))
            except Exception:
                img = None
        else:
            img = None

        if img is None:
            # Generate a simple purple-cyan "S" icon
            img = Image.new("RGBA", (32, 32), (18, 18, 28, 255))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, 30, 30], outline=(0, 240, 255), width=2)
            draw.text((9, 6), "S", fill=(168, 85, 247))

        def _show_window(icon, item):
            if bridge._window:
                bridge._window.show()
                bridge._window.restore()

        def _quit_app(icon, item):
            bridge.force_quit()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("Open Suylios", _show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", _quit_app),
        )

        tray = pystray.Icon("suylios", img, f"{APP_NAME}", menu)
        bridge._tray_icon = tray
        logger.info("System tray icon started.")
        tray.run()
    except Exception as exc:
        logger.warning("System tray not available: %s", exc)


def _scheduler_and_shutdown_loop(bridge) -> None:
    """Background daemon loop: checks scheduled tasks and auto-shutdown trigger."""
    import time
    while not bridge._force_quit:
        try:
            bridge._dm.check_scheduled_tasks()

            # Auto-shutdown check: if mode is set and no active downloads
            if bridge._auto_shutdown_mode:
                active = bridge._dm.get_active_count()
                if active == 0:
                    mode = bridge._auto_shutdown_mode
                    bridge._auto_shutdown_mode = ""
                    logger.info("Auto-shutdown triggered: %s", mode)
                    try:
                        if bridge._window:
                            bridge._window.evaluate_js(
                                f"window._triggerAutoShutdown && window._triggerAutoShutdown('{mode}')"
                            )
                    except Exception:
                        pass
                    # Actual OS command executed from JS after user confirmation countdown
        except Exception as exc:
            logger.warning("Scheduler loop error: %s", exc)
        time.sleep(5)


def main() -> None:
    """Launch the Suylios Downloader application."""
    logger.info("%s v%s starting …", APP_NAME, APP_VERSION)
    logger.info("Portable mode: %s", config.is_portable())
    logger.info("Download directory: %s", config.get_download_dir())
    logger.info("FFmpeg path: %s", config.get_ffmpeg_path())

    bridge = Bridge()
    ui_path = _resolve_ui_path()
    logger.info("UI path: %s", ui_path)

    # Start scheduler / auto-shutdown background loop
    sched_thread = threading.Thread(target=_scheduler_and_shutdown_loop, args=(bridge,), daemon=True, name="scheduler")
    sched_thread.start()

    # Start system tray icon (background thread)
    tray_thread = threading.Thread(target=_create_tray_icon, args=(bridge,), daemon=True, name="tray")
    tray_thread.start()

    # Determine if we should load a URL (dev server) or a file
    if os.environ.get("SUYLIOS_DEV_URL"):
        url = os.environ["SUYLIOS_DEV_URL"]
        logger.info("Using dev URL: %s", url)
    elif Path(ui_path).is_file():
        url = f"file:///{ui_path.replace(os.sep, '/')}"
    else:
        url = "data:text/html,<h1>Suylios Downloader</h1><p>UI files not found.</p>"

    window = webview.create_window(
        title=APP_NAME,
        url=url,
        js_api=bridge,
        width=1200,
        height=800,
        min_size=(900, 600),
        frameless=True,
        easy_drag=False,
        text_select=False,
        hidden=False,
    )
    bridge.set_window(window)

    def _on_ready() -> None:
        try:
            if config.get("start_minimized", False):
                window.minimize()
            else:
                x, y, work_w, work_h = bridge._get_work_area()
                win_w, win_h = 1200, 800
                cx = x + max(0, (work_w - win_w) // 2)
                cy = y + max(0, (work_h - win_h) // 2)
                window.move(cx, cy)
        except Exception as exc:
            logger.error("Window initial placement error: %s", exc)

    window.events.loaded += _on_ready  # type: ignore[attr-defined]

    def _on_closing() -> bool:
        """Intercept close: minimize to tray if background_mode is enabled, else quit."""
        if bridge._force_quit:
            bridge.shutdown()
            return True  # Actually close

        background_mode = config.get("background_mode", True)
        active = bridge._dm.get_active_count()
        scheduled = sum(1 for t in bridge._dm.get_all_tasks() if t.get("scheduled_at", 0) > 0)

        if background_mode or active > 0 or scheduled > 0:
            try:
                window.hide()
                if getattr(bridge, "_first_tray_notify", True):
                    bridge._first_tray_notify = False
                    if hasattr(bridge, "_tray_icon") and bridge._tray_icon:
                        bridge._tray_icon.notify(
                            "Suylios Downloader arka planda çalışmaya devam ediyor. Açmak veya kapatmak için gizli simgelere göz atın.",
                            "Uygulama Arka Plana Taşındı"
                        )
            except Exception as exc:
                logger.error("hide window error: %s", exc)
            return False  # Don't actually close

        bridge.shutdown()
        return True

    window.events.closing += _on_closing  # type: ignore[attr-defined]

    webview.start(debug=("--debug" in sys.argv))


if __name__ == "__main__":
    main()
