"""
Suylios Downloader - Tek Merkez Site & Kural Kayit Defteri (`SiteRegistry`).

5 ayri yerde dagilmis site katalogunu (`SUPPORTED_SITES.json`, `index.html` 29 kart,
`app.js` `BUILTIN_KEYS`, `config.py` `site_settings`, `download_manager.py` `if/elif`)
ve Kanal 2 (`hotfix-manifest.json` + `supported_sites.delta.json`) canli kurallarini
tek bir atomik serviste birlestirir.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.config import config

logger = logging.getLogger(__name__)


def parse_semver(ver: str) -> tuple[int, int, int, int]:
    clean = str(ver or "0.0.0").strip().lstrip("vV")
    nums = [0, 0, 0, 0]
    for idx, part in enumerate(clean.replace("-", ".").replace("_", ".").split(".")[:4]):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            nums[idx] = int(digits)
    return (nums[0], nums[1], nums[2], nums[3])


class SiteRegistry:
    """Thread-safe unified site & hotfix rule registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manifest: Dict[str, Any] = {}
        self._domain_map: Dict[str, Dict[str, Any]] = {}
        self._supported_sites_cache: Optional[List[Dict[str, Any]]] = None
        self.reload()

    def _candidate_manifest_paths(self) -> List[Path]:
        app_root = config.get_app_root()
        data_dir = config.get_data_dir()
        paths = [
            data_dir / "runtime" / "hotfix-manifest.json",
            app_root / "hotfixes" / "dist" / "hotfix-manifest.json",
            app_root / "hotfixes" / "manifest.source.json",
        ]
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                paths.insert(1, Path(meipass) / "hotfixes" / "dist" / "hotfix-manifest.json")
                paths.insert(2, Path(meipass) / "hotfixes" / "manifest.source.json")
        return paths

    def reload(self) -> Dict[str, Any]:
        """Reload hotfix manifest and rebuild fast O(1) domain lookup tables."""
        with self._lock:
            self._activate_runtime_overlays()
            loaded: Dict[str, Any] = {}
            for p in self._candidate_manifest_paths():
                if p.is_file():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        if isinstance(data, dict) and data.get("schema_version"):
                            loaded = data
                            break
                    except Exception as exc:
                        logger.warning("Failed reading manifest %s: %s", p, exc)

            self._manifest = loaded
            self._domain_map = {}
            self._supported_sites_cache = None

            platforms = loaded.get("builtin_platforms") or {}
            sites_cfg = loaded.get("sites") or {}

            for site_key, meta in platforms.items():
                if not isinstance(meta, dict):
                    continue
                s_cfg = sites_cfg.get(site_key) if isinstance(sites_cfg.get(site_key), dict) else {}
                domains = list(meta.get("domains") or [])
                for extra_d in (s_cfg.get("domains") or []):
                    if extra_d not in domains:
                        domains.append(extra_d)

                entry = {
                    "site_key": site_key,
                    "name": meta.get("name") or site_key.title(),
                    "folder": meta.get("folder") or s_cfg.get("folder") or site_key.title(),
                    "engine": meta.get("engine") or "yt-dlp",
                    "is_gallery": bool(meta.get("is_gallery", False)),
                    "domains": domains,
                    "transport": s_cfg.get("transport") or {},
                    "extract_rules": s_cfg.get("extract_rules") or {},
                    "site_config": s_cfg,
                }
                for dom in domains:
                    d_low = str(dom).lower().strip().removeprefix("www.")
                    if d_low:
                        self._domain_map[d_low] = entry

            return self._manifest

    def _activate_runtime_overlays(self) -> None:
        """Ensure runtime overlay directories are at the front of sys.path."""
        runtime_dir = config.get_data_dir() / "runtime"
        overlay_py = runtime_dir / "python_overlay_active"
        if overlay_py.is_dir():
            p_str = str(overlay_py)
            if p_str not in sys.path:
                sys.path.insert(0, p_str)
                importlib.invalidate_caches()

        plugins_dir = runtime_dir / "plugins_active"
        if plugins_dir.is_dir():
            for sub in sorted(plugins_dir.iterdir()):
                if sub.is_dir() and str(sub) not in sys.path:
                    sys.path.insert(0, str(sub))

        hotfixes_dir = runtime_dir / "hotfixes_active"
        if hotfixes_dir.is_dir():
            for sub in sorted(hotfixes_dir.iterdir()):
                if sub.is_dir() and str(sub) not in sys.path:
                    sys.path.insert(0, str(sub))

    @property
    def manifest(self) -> Dict[str, Any]:
        with self._lock:
            return self._manifest

    def get_site_rule(self, site_key: str) -> Dict[str, Any]:
        with self._lock:
            sites = self._manifest.get("sites") or {}
            return dict(sites.get(site_key) or {})

    def get_domains_for(self, site_key: str, default: Optional[List[str]] = None) -> List[str]:
        rule = self.get_site_rule(site_key)
        domains = rule.get("domains")
        if isinstance(domains, list) and domains:
            return [str(d) for d in domains]
        with self._lock:
            plat = (self._manifest.get("builtin_platforms") or {}).get(site_key) or {}
            if isinstance(plat.get("domains"), list) and plat["domains"]:
                return [str(d) for d in plat["domains"]]
        return list(default or [])

    def get_runtime_patches(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._manifest.get("runtime_patches") or {})

    def resolve_url(self, url: str, app_version: str = "2.0.0") -> Dict[str, Any]:
        """Resolve any URL to its platform key, target subfolder, engine, and live rules."""
        url_str = str(url or "").strip()
        url_lower = url_str.lower()
        try:
            host = (urlparse(url_str).hostname or "").lower().removeprefix("www.")
        except Exception:
            host = ""

        # 1. Kullanıcının eklediği özel siteler (custom_sites)
        custom_sites = config.get("custom_sites", []) or []
        for cs in custom_sites:
            if not isinstance(cs, dict):
                continue
            c_dom = str(cs.get("domain", "")).lower().strip().removeprefix("www.")
            if c_dom and (host == c_dom or host.endswith(f".{c_dom}") or c_dom in url_lower):
                key = str(cs.get("key") or "other")
                site_settings = config.get("site_settings", {}) or {}
                s_cfg = site_settings.get(key) or {}
                folder = str(s_cfg.get("folder") or cs.get("folder") or cs.get("name") or "Others")
                return {
                    "site_key": key,
                    "name": str(cs.get("name") or folder),
                    "folder": folder,
                    "engine": str(cs.get("engine") or "yt-dlp"),
                    "is_gallery": False,
                    "transport": {},
                    "extract_rules": {},
                    "hotfix_override": None,
                }

        with self._lock:
            # 2. Doğrudan veya alt-domain eşleşmesi
            matched: Optional[Dict[str, Any]] = None
            if host in self._domain_map:
                matched = self._domain_map[host]
            else:
                for dom, entry in self._domain_map.items():
                    if host.endswith(f".{dom}") or (dom.startswith("bunkr") and ("bunkr" in host or "balbums" in host)):
                        matched = entry
                        break
                    if not host and dom in url_lower:
                        matched = entry
                        break

            if matched:
                site_key = matched["site_key"]
                site_settings = config.get("site_settings", {}) or {}
                user_site_cfg = site_settings.get(site_key) or {}
                folder = str(user_site_cfg.get("folder") or matched["folder"] or "Others")
                s_cfg = matched.get("site_config") or {}
                ov = s_cfg.get("hotfix_override")
                active_ov = None
                if isinstance(ov, dict):
                    retired_ver = str(ov.get("retired_in_app_version") or "99.0.0")
                    if parse_semver(app_version) < parse_semver(retired_ver):
                        active_ov = ov

                return {
                    "site_key": site_key,
                    "name": matched["name"],
                    "folder": folder,
                    "engine": matched["engine"],
                    "is_gallery": matched["is_gallery"],
                    "transport": dict(matched.get("transport") or {}),
                    "extract_rules": dict(matched.get("extract_rules") or {}),
                    "hotfix_override": active_ov,
                }

            # 3. Video host aynaları (Streamtape, DoodStream, StreamWish, Filemoon, VoE)
            vhosts = (self._manifest.get("sites") or {}).get("video_hosts") or {}
            for vh_name, vh_cfg in vhosts.items():
                if not isinstance(vh_cfg, dict):
                    continue
                for d in (vh_cfg.get("domains") or []):
                    d_low = str(d).lower()
                    if host == d_low or host.endswith(f".{d_low}") or d_low in url_lower:
                        site_settings = config.get("site_settings", {}) or {}
                        other_cfg = site_settings.get("other") or {}
                        return {
                            "site_key": "other",
                            "name": vh_name.title(),
                            "folder": str(other_cfg.get("folder") or "Others"),
                            "engine": "video_host",
                            "is_gallery": False,
                            "transport": {},
                            "extract_rules": vh_cfg,
                            "hotfix_override": None,
                        }

        site_settings = config.get("site_settings", {}) or {}
        other_cfg = site_settings.get("other") or {}
        return {
            "site_key": "other",
            "name": "Diğer",
            "folder": str(other_cfg.get("folder") or "Others"),
            "engine": "yt-dlp",
            "is_gallery": False,
            "transport": {},
            "extract_rules": {},
            "hotfix_override": None,
        }

    def get_supported_sites(self) -> List[Dict[str, Any]]:
        """Return merged SUPPORTED_SITES.json + supported_sites.delta.json with normalized Capabilities/features."""
        with self._lock:
            if self._supported_sites_cache is not None:
                return self._supported_sites_cache

            app_root = config.get_app_root()
            data_dir = config.get_data_dir()
            base_candidates = [
                app_root / "hotfixes" / "SUPPORTED_SITES.json",
                app_root / "SUPPORTED_SITES.json",
            ]
            if getattr(sys, "frozen", False):
                meipass = getattr(sys, "_MEIPASS", None)
                if meipass:
                    base_candidates.insert(0, Path(meipass) / "hotfixes" / "SUPPORTED_SITES.json")
                    base_candidates.insert(1, Path(meipass) / "SUPPORTED_SITES.json")

            items: List[Dict[str, Any]] = []
            seen_domains: set[str] = set()

            for bp in base_candidates:
                if bp.is_file():
                    try:
                        raw_list = json.loads(bp.read_text(encoding="utf-8"))
                        if isinstance(raw_list, list):
                            for entry in raw_list:
                                if isinstance(entry, dict):
                                    caps = str(entry.get("Capabilities") or entry.get("features") or "")
                                    entry["Capabilities"] = caps
                                    entry["features"] = caps
                                    dom = str(entry.get("domain") or "").lower()
                                    if dom:
                                        seen_domains.add(dom)
                                    items.append(entry)
                        break
                    except Exception as exc:
                        logger.warning("Failed loading SUPPORTED_SITES.json from %s: %s", bp, exc)

            delta_candidates = [
                data_dir / "runtime" / "supported_sites.delta.json",
                app_root / "hotfixes" / "dist" / "supported_sites.delta.json",
                app_root / "hotfixes" / "supported_sites.delta.json",
            ]
            for dp in delta_candidates:
                if dp.is_file():
                    try:
                        delta_list = json.loads(dp.read_text(encoding="utf-8"))
                        if isinstance(delta_list, list):
                            for entry in delta_list:
                                if isinstance(entry, dict):
                                    dom = str(entry.get("domain") or "").lower()
                                    if dom and dom not in seen_domains:
                                        caps = str(entry.get("Capabilities") or entry.get("features") or "")
                                        entry["Capabilities"] = caps
                                        entry["features"] = caps
                                        seen_domains.add(dom)
                                        items.append(entry)
                        break
                    except Exception:
                        pass

            self._supported_sites_cache = items
            return items


site_registry = SiteRegistry()
