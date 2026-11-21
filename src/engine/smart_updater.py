"""
Suylios Downloader - 3 Kanalli Akilli Guncelleme Orkestratoru (`SmartUpdater`).

Kanal 1: Uygulama Cekirdegi (GitHub Releases - gercek SemVer ile)
Kanal 2: Tek-Repo Zero-Release Hotfix Beslemesi (`hotfix-manifest.json` + Ed25519 `.sig`)
Kanal 3: Dogrudan PyPI JSON API `.whl` Motor Overlay (`yt-dlp`, `gallery-dl`, `cyberdrop-dl`, `async-mega-py`)

Derlenmis Rust `suylios-engine` ikilisini birincil olarak kullanir;
Rust ikilisi bulunamazsa saf-Python Ed25519 + PyPI yedegiyle kesintisiz calisir.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import config
from src.engine.rust_bridge import is_rust_engine_available, locate_rust_engine, run_rust_command
from src.engine.site_registry import parse_semver, site_registry

logger = logging.getLogger(__name__)

MANAGED_PACKAGES = ("yt-dlp", "gallery-dl", "cyberdrop-dl", "async-mega-py")


class SmartUpdaterManager:
    """Manages all 3 decoupled update channels and runtime overlays."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_check_cache: Dict[str, Any] = {}

    @property
    def runtime_dir(self) -> Path:
        d = config.get_data_dir() / "runtime"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_installed_engine_versions(self) -> Dict[str, str]:
        """Return currently active versions of managed Python engines (overlay first, then system/venv)."""
        versions: Dict[str, str] = {}
        state_file = self.runtime_dir / "state.json"
        overlay_versions: Dict[str, str] = {}
        if state_file.is_file():
            try:
                st = json.loads(state_file.read_text(encoding="utf-8"))
                overlay_versions = dict(st.get("installed_wheels") or {})
            except Exception:
                pass

        for pkg in MANAGED_PACKAGES:
            if pkg in overlay_versions and (self.runtime_dir / "python_overlay_active").is_dir():
                versions[pkg] = str(overlay_versions[pkg])
                continue
            try:
                versions[pkg] = importlib.metadata.version(pkg)
            except Exception:
                # Modül üzerinden yedek sorgu
                try:
                    if pkg == "yt-dlp":
                        import yt_dlp
                        versions[pkg] = getattr(yt_dlp.version, "__version__", "0.0.0")
                    elif pkg == "gallery-dl":
                        import gallery_dl
                        versions[pkg] = getattr(gallery_dl, "__version__", "0.0.0")
                    else:
                        versions[pkg] = "0.0.0"
                except Exception:
                    versions[pkg] = "0.0.0"
        return versions

    def get_update_center_status(self, app_version: str = "2.0.0") -> Dict[str, Any]:
        """Return immediate local snapshot for the Settings -> Updates UI."""
        manifest = site_registry.manifest
        state_path = self.runtime_dir / "state.json"
        state: Dict[str, Any] = {}
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        has_rollback = (self.runtime_dir / "python_overlay_prev").is_dir()
        rust_bin = locate_rust_engine()

        return {
            "ok": True,
            "app_version": app_version,
            "rust_engine_active": rust_bin is not None,
            "rust_engine_path": rust_bin or "",
            "manifest_seq": state.get("manifest_seq") or manifest.get("manifest_seq", 0),
            "manifest_published_at": manifest.get("published_at", ""),
            "last_hotfix_check": state.get("last_hotfix_check_epoch", 0),
            "installed_engines": self.get_installed_engine_versions(),
            "has_rollback": has_rollback,
            "supported_sites_count": len(site_registry.get_supported_sites()),
        }

    def sync_hotfix_channel(self, custom_manifest_url: Optional[str] = None) -> Dict[str, Any]:
        """Sync Channel 2 (Zero-Release Hotfix Feed) via Rust engine or Python fallback, then reload SiteRegistry."""
        payload: Dict[str, Any] = {
            "app_root": str(config.get_app_root()),
            "data_dir": str(config.get_data_dir()),
        }
        if custom_manifest_url:
            payload["manifest_url"] = custom_manifest_url

        res = run_rust_command("sync-hotfix", payload, timeout=30.0)
        if res and res.get("ok"):
            site_registry.reload()
            return res

        # Python Ed25519 yedek doğrulayıcı (Rust ikilisi yoksa)
        return self._python_sync_hotfix_fallback(custom_manifest_url)

    def _python_sync_hotfix_fallback(self, custom_manifest_url: Optional[str] = None) -> Dict[str, Any]:
        try:
            sys.path.insert(0, str(config.get_app_root() / "hotfixes" / "tools"))
            from ed25519_util import get_official_public_key_hex, parse_Signature_text, verify_bytes

            local_dist = config.get_app_root() / "hotfixes" / "dist"
            m_path = local_dist / "hotfix-manifest.json"
            s_path = local_dist / "hotfix-manifest.sig"
            if custom_manifest_url and custom_manifest_url.startswith("file://"):
                m_path = Path(custom_manifest_url.removeprefix("file://"))
                s_path = m_path.with_extension(".sig")

            if not (m_path.is_file() and s_path.is_file()):
                return {"ok": False, "error": "İmzalı hotfix manifesti bulunamadı."}

            m_bytes = m_path.read_bytes()
            sig_bytes = parse_Signature_text(s_path.read_text(encoding="utf-8"))
            pub_bytes = bytes.fromhex(get_official_public_key_hex())

            if not verify_bytes(m_bytes, sig_bytes, pub_bytes):
                return {"ok": False, "error": "Ed25519 imza doğrulaması başarısız!"}

            parsed = json.loads(m_bytes.decode("utf-8"))
            incoming_seq = int(parsed.get("manifest_seq") or 0)
            state_path = self.runtime_dir / "state.json"
            state: Dict[str, Any] = {}
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            if int(state.get("manifest_seq") or 0) > incoming_seq:
                return {"ok": False, "error": "Downgrade saldırısı engellendi (eski manifest_seq)."}

            (self.runtime_dir / "hotfix-manifest.json").write_bytes(m_bytes)
            (self.runtime_dir / "hotfix-manifest.sig").write_text(s_path.read_text(encoding="utf-8"), encoding="utf-8")
            state["manifest_seq"] = incoming_seq
            state["last_hotfix_check_epoch"] = int(time.time())
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            site_registry.reload()
            return {"ok": True, "status": "updated", "manifest_seq": incoming_seq}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def check_all_channels(self, app_version: str = "2.0.0", custom_manifest_url: Optional[str] = None) -> Dict[str, Any]:
        """Check Channel 1 (App Release) + Channel 2 (Hotfix Feed) + Channel 3 (PyPI Engines)."""
        local_wheels = self.get_installed_engine_versions()
        payload: Dict[str, Any] = {
            "app_root": str(config.get_app_root()),
            "data_dir": str(config.get_data_dir()),
            "app_version": app_version,
            "local_wheels": local_wheels,
        }
        if custom_manifest_url:
            payload["manifest_url"] = custom_manifest_url

        res = run_rust_command("check-all-updates", payload, timeout=45.0)
        if res and res.get("ok"):
            site_registry.reload()
            self._last_check_cache = res
            return res

        return {
            "ok": False,
            "error": "Güncelleme denetimi tamamlanamadı.",
            "local_wheels": local_wheels,
        }

    def update_pypi_engines(self, packages: Optional[List[str]] = None) -> Dict[str, Any]:
        """Download, verify SHA-256, smoke-test, and atomically activate updated PyPI wheels."""
        if not self._last_check_cache.get("channel3_engines"):
            self.check_all_channels()

        engines_info = self._last_check_cache.get("channel3_engines") or {}
        targets = packages or [
            pkg for pkg, meta in engines_info.items()
            if isinstance(meta, dict) and meta.get("has_update")
        ]

        if not targets:
            return {"ok": True, "updated": [], "message": "Tüm indirme motorları güncel."}

        py_bin = sys.executable or "python3"
        updated: List[Dict[str, Any]] = []
        errors: List[str] = []

        for pkg in targets:
            meta = engines_info.get(pkg) or {}
            wheel_url = str(meta.get("wheel_url") or "")
            sha256 = str(meta.get("sha256") or "")
            ver = str(meta.get("latest_version") or "")
            if not (wheel_url and sha256 and ver):
                continue

            payload = {
                "app_root": str(config.get_app_root()),
                "data_dir": str(config.get_data_dir()),
                "package": pkg,
                "version": ver,
                "wheel_url": wheel_url,
                "sha256": sha256,
                "python_bin": py_bin,
            }
            res = run_rust_command("install-wheel", payload, timeout=90.0)
            if res and res.get("ok"):
                updated.append({"package": pkg, "version": ver})
            else:
                err_msg = (res or {}).get("error") or f"{pkg} güncellenemedi"
                errors.append(str(err_msg))

        site_registry.reload()
        return {
            "ok": len(errors) == 0,
            "updated": updated,
            "errors": errors,
            "installed_engines": self.get_installed_engine_versions(),
            "has_rollback": (self.runtime_dir / "python_overlay_prev").is_dir(),
        }

    def rollback_engines(self) -> Dict[str, Any]:
        """Atomically roll back `python_overlay_active` to `python_overlay_prev`."""
        payload = {
            "app_root": str(config.get_app_root()),
            "data_dir": str(config.get_data_dir()),
        }
        res = run_rust_command("rollback-wheels", payload, timeout=15.0)
        if res and res.get("ok"):
            site_registry.reload()
            return {
                "ok": True,
                "installed_engines": self.get_installed_engine_versions(),
                "has_rollback": (self.runtime_dir / "python_overlay_prev").is_dir(),
            }
        return {"ok": False, "error": (res or {}).get("error") or "Geri alınacak yedek sürüm bulunamadı."}


smart_updater = SmartUpdaterManager()
