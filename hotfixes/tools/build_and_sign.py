#!/usr/bin/env python3
"""
Suylios Downloader - Kanal 2 Zero-Release Paketleme ve Ed25519 Imzalama Araci.

Kullanim:
    python3 hotfixes/tools/build_and_sign.py --out hotfixes/dist
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ed25519_util import (
    DEFAULT_DEV_SEED_HEX,
    keypair_from_seed,
    sign_bytes,
    verify_bytes,
)
from validate_schema import validate_all

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
HOTFIX_DIR = ROOT_DIR / "hotfixes"


def _deterministic_zip(src_dir: Path, out_zip: Path) -> tuple[str, int]:
    """Create a deterministic ZIP archive of *.py files in src_dir and return (sha256_hex, file_count)."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(src_dir.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc")]
    if not files:
        return "", 0

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            rel = file_path.relative_to(src_dir).as_posix()
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, file_path.read_bytes())

    digest = hashlib.sha256(out_zip.read_bytes()).hexdigest()
    return digest, len(files)


def build_and_sign(out_dir: Path, base_url: str, bump_seq: bool = False) -> int:
    if not validate_all():
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    bundles_dir = out_dir / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)

    manifest_src = HOTFIX_DIR / "manifest.source.json"
    manifest = json.loads(manifest_src.read_text(encoding="utf-8"))

    if bump_seq:
        now_seq = int(datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H"))
        manifest["manifest_seq"] = max(int(manifest.get("manifest_seq", 0)) + 1, now_seq)

    manifest["published_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seq_str = str(manifest["manifest_seq"])

    bundles_meta = manifest.get("bundles") or {}

    # 1. gdl_plugins paketle (varsa hotfixes/gdl_plugins, yoksa src/extractors/gdl_plugins)
    gdl_src = HOTFIX_DIR / "gdl_plugins"
    if not gdl_src.is_dir() or not list(gdl_src.glob("*.py")):
        gdl_src = ROOT_DIR / "src" / "extractors" / "gdl_plugins"
    if gdl_src.is_dir():
        zip_name = f"gdl_plugins-{seq_str}.zip"
        zip_path = bundles_dir / zip_name
        sha, cnt = _deterministic_zip(gdl_src, zip_path)
        if cnt > 0:
            bundles_meta["gdl_plugins"] = {
                "version": seq_str,
                "min_app_version": manifest.get("min_app_version", "2.0.0"),
                "file_count": cnt,
                "sha256": sha,
                "url": f"{base_url.rstrip('/')}/bundles/{zip_name}",
            }

    # 2. yt_dlp_plugins paketle (varsa)
    ytdlp_src = HOTFIX_DIR / "yt_dlp_plugins"
    if ytdlp_src.is_dir():
        zip_name = f"yt_dlp_plugins-{seq_str}.zip"
        zip_path = bundles_dir / zip_name
        sha, cnt = _deterministic_zip(ytdlp_src, zip_path)
        if cnt > 0:
            bundles_meta["yt_dlp_plugins"] = {
                "version": seq_str,
                "min_app_version": manifest.get("min_app_version", "2.0.0"),
                "file_count": cnt,
                "sha256": sha,
                "url": f"{base_url.rstrip('/')}/bundles/{zip_name}",
            }

    # 3. Acil site hotfix override modulleri (hotfixes/overrides/<site>_hotfix.py)
    overrides_src = HOTFIX_DIR / "overrides"
    if overrides_src.is_dir():
        for py_override in sorted(overrides_src.glob("*_hotfix.py")):
            site_key = py_override.stem.replace("_hotfix", "")
            zip_name = f"{site_key}-r{seq_str}.zip"
            zip_path = bundles_dir / zip_name
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                info = zipfile.ZipInfo(py_override.name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, py_override.read_bytes())
            sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            if site_key in manifest.get("sites", {}):
                existing_ov = manifest["sites"][site_key].get("hotfix_override") or {}
                manifest["sites"][site_key]["hotfix_override"] = {
                    "hotfix_seq": manifest["manifest_seq"],
                    "retired_in_app_version": existing_ov.get("retired_in_app_version", "99.0.0"),
                    "url": f"{base_url.rstrip('/')}/bundles/{zip_name}",
                    "sha256": sha,
                    "entry": f"{py_override.stem}:HotfixExtractor",
                    "handoff_to_rust_transfer": existing_ov.get("handoff_to_rust_transfer", True),
                }

    # 4. supported_sites.delta.json kopyala ve hashle
    delta_src = HOTFIX_DIR / "supported_sites.delta.json"
    if delta_src.is_file():
        delta_dst = out_dir / "supported_sites.delta.json"
        shutil.copyfile(delta_src, delta_dst)
        delta_sha = hashlib.sha256(delta_dst.read_bytes()).hexdigest()
        bundles_meta["supported_sites_delta"] = {
            "version": seq_str,
            "sha256": delta_sha,
            "url": f"{base_url.rstrip('/')}/supported_sites.delta.json",
        }

    manifest["bundles"] = bundles_meta

    # 5. Kanonik JSON serilestirme ve Ed25519 imzalama
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    out_manifest = out_dir / "hotfix-manifest.json"
    out_manifest.write_bytes(manifest_bytes)

    priv_hex = os.environ.get("HOTFIX_PRIVKEY", "").strip() or DEFAULT_DEV_SEED_HEX
    seed_bytes = bytes.fromhex(priv_hex)
    _, pub_bytes = keypair_from_seed(seed_bytes)
    sig_bytes = sign_bytes(manifest_bytes, seed_bytes)

    if not verify_bytes(manifest_bytes, sig_bytes, pub_bytes):
        print("[KRITIK HATA] Uretilen Ed25519 imzasi dogrulanamadi!", file=sys.stderr)
        return 1

    out_sig = out_dir / "hotfix-manifest.sig"
    out_sig.write_text(sig_bytes.hex() + "\n", encoding="utf-8")

    out_pub = out_dir / "public_key.pub"
    out_pub.write_text(pub_bytes.hex() + "\n", encoding="utf-8")

    print(f"[TAMAM] hotfix-manifest.json uretildi ve Ed25519 ile imzalandi.")
    print(f"        Public Key (hex): {pub_bytes.hex()}")
    print(f"        Signature  (hex): {sig_bytes.hex()[:32]}...")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and sign Suylios Channel 2 hotfix feed")
    parser.add_argument("--out", default=str(HOTFIX_DIR / "dist"), help="Output directory")
    parser.add_argument(
        "--base-url",
        default="https://sayrias.github.io/suylios-downloader/hotfix",
        help="Public CDN base URL for bundle links",
    )
    parser.add_argument("--bump-seq", action="store_true", help="Bump manifest_seq automatically")
    args = parser.parse_args()
    sys.exit(build_and_sign(Path(args.out), args.base_url, args.bump_seq))


if __name__ == "__main__":
    main()
