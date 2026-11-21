#!/usr/bin/env python3
"""
Suylios Downloader - Hotfix Kaynak ve Sema Dogrulayici.

`hotfixes/manifest.source.json`, `hotfixes/supported_sites.delta.json` ve
eklenti dizinlerindeki Python betiklerini yayin oncesi denetler.
"""

from __future__ import annotations

import json
import py_compile
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
HOTFIX_DIR = ROOT_DIR / "hotfixes"


def validate_all() -> bool:
    manifest_path = HOTFIX_DIR / "manifest.source.json"
    delta_path = HOTFIX_DIR / "supported_sites.delta.json"

    if not manifest_path.is_file():
        print(f"[HATA] Eksik dosya: {manifest_path}", file=sys.stderr)
        return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[HATA] manifest.source.json gecersiz JSON: {exc}", file=sys.stderr)
        return False

    required_top = ("schema_version", "manifest_seq", "global_network", "pypi_pins", "builtin_platforms", "sites")
    for key in required_top:
        if key not in manifest:
            print(f"[HATA] manifest.source.json icinde zorunlu '{key}' alani eksik!", file=sys.stderr)
            return False

    if not isinstance(manifest["manifest_seq"], int) or manifest["manifest_seq"] <= 0:
        print("[HATA] manifest_seq pozitif tamsayi olmalidir!", file=sys.stderr)
        return False

    if delta_path.is_file():
        try:
            delta = json.loads(delta_path.read_text(encoding="utf-8"))
            if not isinstance(delta, list):
                print("[HATA] supported_sites.delta.json bir JSON dizi (array) olmalidir!", file=sys.stderr)
                return False
            for idx, item in enumerate(delta):
                if not isinstance(item, dict) or not item.get("domain") or not item.get("name"):
                    print(f"[HATA] supported_sites.delta.json [{idx}] icinde name/domain eksik!", file=sys.stderr)
                    return False
        except Exception as exc:
            print(f"[HATA] supported_sites.delta.json gecersiz: {exc}", file=sys.stderr)
            return False

    # Python eklenti ve override betiklerinin sozdizimi kontrolu
    py_dirs = [
        HOTFIX_DIR / "overrides",
        HOTFIX_DIR / "gdl_plugins",
        HOTFIX_DIR / "yt_dlp_plugins",
        ROOT_DIR / "src" / "extractors" / "gdl_plugins",
    ]
    checked_py = 0
    for d in py_dirs:
        if d.is_dir():
            for py_file in sorted(d.rglob("*.py")):
                try:
                    py_compile.compile(str(py_file), doraise=True)
                    checked_py += 1
                except py_compile.PyCompileError as exc:
                    print(f"[HATA] Python sozdizimi hatasi ({py_file}): {exc}", file=sys.stderr)
                    return False

    print(
        f"[TAMAM] Hotfix semasi gecerli: seq={manifest['manifest_seq']}, "
        f"platform={len(manifest['builtin_platforms'])}, site_kurali={len(manifest['sites'])}, "
        f"kontrol_edilen_py={checked_py}"
    )
    return True


if __name__ == "__main__":
    sys.exit(0 if validate_all() else 1)
