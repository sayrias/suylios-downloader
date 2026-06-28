#!/usr/bin/env python3
"""
Suylios Downloader - Portable Derleyici
========================================
PyInstaller ile uygulamayi yaklasik 15-20 saniyede derler ve portable ZIP paketi olusturur.
"""

import os
import sys
import shutil
import subprocess
import zipfile
from pathlib import Path

# Fix console encoding errors on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).parent.resolve()
SRC_DIR = PROJECT_ROOT / "src"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
BIN_DIR = PROJECT_ROOT / "bin"
PORTABLE_NAME = "Suylios-Portable"
PORTABLE_DIR = DIST_DIR / PORTABLE_NAME


def clean_build_dirs():
    print("[*] Onceki derleme dosyalari temizleniyor...")
    for d in [BUILD_DIR, DIST_DIR / PORTABLE_NAME]:
        if d.exists():
            shutil.rmtree(d)
    DIST_DIR.mkdir(parents=True, exist_ok=True)


def build_app():
    print("[*] Uygulama derleniyor (yaklasik 15 saniye)...")
    try:
        import PyInstaller
    except ImportError:
        print("[*] 'pyinstaller' yukleniyor...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    main_script = SRC_DIR / "main.py"
    icon_path = SRC_DIR / "ui" / "icon.ico"
    sep = ";" if os.name == "nt" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        f"--name=SuyliosDownloader",
        f"--add-data={SRC_DIR / 'ui'}{sep}ui",
        f"--distpath={BUILD_DIR}",
        f"--workpath={BUILD_DIR / 'temp'}",
    ]

    if icon_path.exists():
        cmd.append(f"--icon={icon_path}")

    cmd.append(str(main_script))

    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        print("[HATA] Derleme basarisiz oldu!")
        sys.exit(1)
    print("[TAMAM] Derleme tamamlandi.")


def create_portable_package():
    print("[*] Portable klasor yapisi duzenleniyor...")
    PORTABLE_DIR.mkdir(parents=True, exist_ok=True)

    build_out = BUILD_DIR / "SuyliosDownloader"
    if build_out.exists():
        for item in build_out.iterdir():
            if item.is_dir():
                shutil.copytree(item, PORTABLE_DIR / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, PORTABLE_DIR / item.name)

        if (PORTABLE_DIR / "SuyliosDownloader.exe").exists():
            if (PORTABLE_DIR / "suylios.exe").exists():
                os.remove(PORTABLE_DIR / "suylios.exe")
            os.rename(PORTABLE_DIR / "SuyliosDownloader.exe", PORTABLE_DIR / "suylios.exe")

    portable_bin = PORTABLE_DIR / "bin"
    portable_bin.mkdir(exist_ok=True)
    ffmpeg_source = BIN_DIR / "ffmpeg.exe"
    if ffmpeg_source.exists():
        shutil.copy2(ffmpeg_source, portable_bin / "ffmpeg.exe")

    (PORTABLE_DIR / "Downloads").mkdir(exist_ok=True)
    (PORTABLE_DIR / "data").mkdir(exist_ok=True)

    internal_dir = PORTABLE_DIR / "_internal"
    if internal_dir.exists():
        try:
            subprocess.run(["attrib", "+h", str(internal_dir)], check=False)
        except Exception:
            pass


def create_zip():
    zip_path = DIST_DIR / f"{PORTABLE_NAME}.zip"
    print(f"[*] ZIP arsivi olusturuluyor: {zip_path.name}")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in PORTABLE_DIR.rglob("*"):
            arcname = file_path.relative_to(DIST_DIR)
            zf.write(file_path, arcname)
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"[TAMAM] Portable paket hazir: {zip_path} ({size_mb:.1f} MB)")


def main():
    print("=" * 60)
    print("  Suylios Downloader - Tek Tikla Hizli Derleyici")
    print("=" * 60)
    clean_build_dirs()
    build_app()
    create_portable_package()
    create_zip()
    print("=" * 60)


if __name__ == "__main__":
    main()
