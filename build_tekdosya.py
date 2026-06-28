#!/usr/bin/env python3
"""
Suylios Downloader - Tek Dosya (.exe) Derleyici
=================================================
Tüm arayüzü, Python motorunu ve FFmpeg'i tek bir bağımsız .exe dosyası içine gömer.
Kurulum veya klasör gerektirmez, direkt çalışır.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

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

APP_NAME = "Suylios"


def compile_single_exe():
    print("=" * 60)
    print("  Suylios Downloader - Tek Dosya (Standalone EXE) Derleyici")
    print("=" * 60)
    print()
    print("[*] PyInstaller ile tek dosya derleme basliyor (yaklasik 20 saniye)...")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    main_script = SRC_DIR / "main.py"
    icon_path = SRC_DIR / "ui" / "icon.ico"
    ffmpeg_path = BIN_DIR / "ffmpeg.exe"
    sep = ";" if os.name == "nt" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        f"--name={APP_NAME}",
        f"--add-data={SRC_DIR / 'ui'}{sep}ui",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR / 'temp_onefile'}",
        f"--specpath={BUILD_DIR}",
    ]

    if ffmpeg_path.exists():
        cmd.append(f"--add-binary={ffmpeg_path}{sep}bin")

    if icon_path.exists():
        cmd.append(f"--icon={icon_path}")

    cmd.append(str(main_script))

    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        print("[HATA] Derleme basarisiz oldu!")
        sys.exit(1)

    output_exe = DIST_DIR / f"{APP_NAME}.exe"
    print()
    print("=" * 60)
    if output_exe.exists():
        size_mb = output_exe.stat().st_size / (1024 * 1024)
        print(f"  [TAMAM] Tek Dosya EXE Hazir: {output_exe} ({size_mb:.1f} MB)")
    else:
        print("  [HATA] Çıktı dosyası bulunamadı.")
    print("=" * 60)


if __name__ == "__main__":
    compile_single_exe()
