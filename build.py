#!/usr/bin/env python3
"""
Suylios Downloader - Master Derleme ve Paketleme Üssü
======================================================
Tek bir script üzerinden tüm paketleme alternatiflerini (Portable ZIP, Setup EXE, Tek Dosya EXE) yönetir.

Kullanım:
    python build.py portable   # Taşınabilir ZIP (Suylios-Portable.zip)
    python build.py setup      # Kurulum Sihirbazı (Suylios-Setup.exe)
    python build.py onefile    # Tek Dosya Standalone (Suylios.exe)
    python build.py all        # Hepsini sırayla derle
"""

import os
import sys
import shutil
import subprocess
import zipfile
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

APP_NAME = "Suylios Downloader"
APP_VERSION = "1.1.0"
APP_PUBLISHER = "Suylios"
APP_URL = "https://github.com/sayrias/suylios-downloader"
APP_EXE = "suylios.exe"
DEFAULT_DIR = r"{autopf}\Suylios Downloader"

INFO_TR = """=========================================================
   Suylios Downloader - Kurulum Sihirbazına Hoş Geldiniz
=========================================================

Suylios Downloader, YouTube ve diğer platformlardan en yüksek kalitede video ve ses indirmenizi sağlayan modern bir uygulamadır.

Özellikler:
- 4K / 1080p Kayıpsız Video İndirme
- MP3 / FLAC Yüksek Kaliteli Ses İndirme
- Gelişmiş İndirme Kuyruğu ve Hız Kontrolü
- Modern ve Kullanıcı Dostu Arayüz

Kuruluma devam etmek için İleri (Next) butonuna tıklayın.
"""

INFO_EN = """=========================================================
   Welcome to Suylios Downloader Setup Wizard
=========================================================

Suylios Downloader is a modern application that allows you to download videos and audio from YouTube and other platforms in the highest quality.

Features:
- 4K / 1080p Lossless Video Downloading
- MP3 / FLAC High Quality Audio Downloading
- Advanced Download Queue and Speed Control
- Modern and User-Friendly Interface

Click Next to continue with the installation.
"""

INNO_SCRIPT_TEMPLATE = r"""
; ===============================================
;  Suylios Downloader - Inno Setup Script
; ===============================================

[Setup]
AppId={{{{B8E3F2A1-5D4C-4A3B-9F1E-7C2D6A8B5E4F}}}}
AppName={app_name}
AppVersion={app_version}
AppPublisher={app_publisher}
AppPublisherURL={app_url}
AppSupportURL={app_url}
DefaultDirName={default_dir}
DefaultGroupName={app_name}
OutputDir={output_dir}
OutputBaseFilename=Suylios-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableDirPage=no
DisableProgramGroupPage=no
ShowLanguageDialog=yes
SetupIconFile={icon_file}
UninstallDisplayName={app_name}
UninstallDisplayIcon={{app}}\{app_exe}

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"; InfoBeforeFile: "{info_tr}"
Name: "english"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "{info_en}"

[CustomMessages]
turkish.DesktopShortcut=Masaüstünde kısayol oluştur
english.DesktopShortcut=Create a desktop shortcut
turkish.StartMenuShortcut=Başlat Menüsüne kısayol ekle
english.StartMenuShortcut=Create a Start Menu shortcut
turkish.AdditionalIcons=Ek Kısayollar:
english.AdditionalIcons=Additional shortcuts:

[Tasks]
Name: "desktopicon"; Description: "{{cm:DesktopShortcut}}"; GroupDescription: "{{cm:AdditionalIcons}}"
Name: "startmenuicon"; Description: "{{cm:StartMenuShortcut}}"; GroupDescription: "{{cm:AdditionalIcons}}"

[Files]
Source: "{staging_dir}\{app_exe}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{staging_dir}\_internal\*"; DestDir: "{{app}}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs; Attribs: hidden
Source: "{staging_dir}\bin\*"; DestDir: "{{app}}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{staging_dir}\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{{autoprograms}}\{app_name}"; Filename: "{{app}}\{app_exe}"; Tasks: startmenuicon
Name: "{{autodesktop}}\{app_name}"; Filename: "{{app}}\{app_exe}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\{app_exe}"; Description: "{app_name} Başlat / Launch"; Flags: nowait postinstall skipifsilent
"""


def ensure_pyinstaller():
    try:
        import PyInstaller
    except ImportError:
        print("[*] PyInstaller yükleniyor...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def build_onedir() -> Path:
    ensure_pyinstaller()
    build_out = BUILD_DIR / "SuyliosDownloader"
    if build_out.exists() and (build_out / "SuyliosDownloader.exe").exists():
        return build_out

    print("[*] PyInstaller ile temel klasör derlemesi yapılıyor (yaklaşık 15 saniye)...")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

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
        print("[HATA] Derleme başarısız oldu!")
        sys.exit(1)
    return build_out


def build_portable():
    print("\n" + "="*60)
    print("  1. Taşınabilir ZIP Paketi (Suylios-Portable.zip) Hazırlanıyor")
    print("="*60)
    build_out = build_onedir()

    portable_dir = DIST_DIR / "Suylios-Portable"
    if portable_dir.exists():
        shutil.rmtree(portable_dir)
    portable_dir.mkdir(parents=True, exist_ok=True)

    for item in build_out.iterdir():
        if item.is_dir():
            shutil.copytree(item, portable_dir / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, portable_dir / item.name)

    if (portable_dir / "SuyliosDownloader.exe").exists():
        if (portable_dir / "suylios.exe").exists():
            os.remove(portable_dir / "suylios.exe")
        os.rename(portable_dir / "SuyliosDownloader.exe", portable_dir / "suylios.exe")

    portable_bin = portable_dir / "bin"
    portable_bin.mkdir(exist_ok=True)
    ffmpeg_src = BIN_DIR / "ffmpeg.exe"
    if ffmpeg_src.exists():
        shutil.copy2(ffmpeg_src, portable_bin / "ffmpeg.exe")

    (portable_dir / "Downloads").mkdir(exist_ok=True)
    (portable_dir / "data").mkdir(exist_ok=True)

    internal_dir = portable_dir / "_internal"
    if internal_dir.exists():
        try:
            subprocess.run(["attrib", "+h", str(internal_dir)], check=False)
        except Exception:
            pass

    zip_path = DIST_DIR / "Suylios-Portable.zip"
    if zip_path.exists():
        zip_path.unlink()

    print("[*] ZIP dosyası sıkıştırılıyor...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(portable_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = Path("Suylios-Portable") / file_path.relative_to(portable_dir)
                zf.write(file_path, arcname)

    shutil.rmtree(portable_dir, ignore_errors=True)
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"[TAMAM] Taşınabilir ZIP Hazır: {zip_path} ({size_mb:.1f} MB)")


def find_inno_setup() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    paths = [
        Path(local_app_data) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for p in paths:
        if p.exists():
            return p
    res = shutil.which("ISCC")
    return Path(res) if res else None


def build_setup():
    print("\n" + "="*60)
    print("  2. Kurulum Sihirbazı (Suylios-Setup.exe) Hazırlanıyor")
    print("="*60)
    build_out = build_onedir()

    staging_dir = DIST_DIR / "setup-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    for item in build_out.iterdir():
        if item.is_dir():
            shutil.copytree(item, staging_dir / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, staging_dir / item.name)

    if (staging_dir / "SuyliosDownloader.exe").exists():
        if (staging_dir / "suylios.exe").exists():
            os.remove(staging_dir / "suylios.exe")
        os.rename(staging_dir / "SuyliosDownloader.exe", staging_dir / "suylios.exe")

    staging_bin = staging_dir / "bin"
    staging_bin.mkdir(exist_ok=True)
    ffmpeg_src = BIN_DIR / "ffmpeg.exe"
    if ffmpeg_src.exists():
        shutil.copy2(ffmpeg_src, staging_bin / "ffmpeg.exe")

    internal_dir = staging_dir / "_internal"
    if internal_dir.exists():
        try:
            subprocess.run(["attrib", "+h", str(internal_dir)], check=False)
        except Exception:
            pass

    iscc = find_inno_setup()
    if not iscc:
        print("[UYARI] Inno Setup bulunamadı! Setup.exe oluşturulamıyor.")
        return

    icon_file = SRC_DIR / "ui" / "icon.ico"
    info_tr_file = BUILD_DIR / "info_tr.txt"
    info_tr_file.write_text(INFO_TR, encoding="utf-8")
    info_en_file = BUILD_DIR / "info_en.txt"
    info_en_file.write_text(INFO_EN, encoding="utf-8")

    iss_content = INNO_SCRIPT_TEMPLATE.format(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        app_publisher=APP_PUBLISHER,
        app_url=APP_URL,
        app_exe=APP_EXE,
        default_dir=DEFAULT_DIR,
        output_dir=str(DIST_DIR),
        staging_dir=str(staging_dir),
        info_tr=str(info_tr_file),
        info_en=str(info_en_file),
        icon_file=str(icon_file) if icon_file.exists() else "",
    )

    iss_path = BUILD_DIR / "suylios_setup.iss"
    iss_path.write_text(iss_content, encoding="utf-8")

    print("[*] Inno Setup derleniyor...")
    res = subprocess.run([str(iscc), str(iss_path)])
    shutil.rmtree(staging_dir, ignore_errors=True)

    if res.returncode == 0:
        setup_exe = DIST_DIR / "Suylios-Setup.exe"
        if setup_exe.exists():
            size_mb = setup_exe.stat().st_size / (1024 * 1024)
            print(f"[TAMAM] Kurulum Sihirbazı Hazır: {setup_exe} ({size_mb:.1f} MB)")


def build_onefile():
    print("\n" + "="*60)
    print("  3. Tek Dosya Standalone (Suylios.exe) Hazırlanıyor")
    print("="*60)
    ensure_pyinstaller()
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
        f"--name=Suylios",
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

    print("[*] Standalone tek dosya derlemesi yapılıyor (yaklaşık 20 saniye)...")
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode == 0:
        exe_path = DIST_DIR / "Suylios.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"[TAMAM] Tek Dosya EXE Hazır: {exe_path} ({size_mb:.1f} MB)")


def clean_temp():
    print("[*] Geçici derleme dosyaları temizleniyor...")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    for f in PROJECT_ROOT.glob("*.spec"):
        try:
            f.unlink()
        except Exception:
            pass


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "menu"
    if arg in ("1", "portable"):
        build_portable()
        clean_temp()
    elif arg in ("2", "setup"):
        build_setup()
        clean_temp()
    elif arg in ("3", "onefile"):
        build_onefile()
        clean_temp()
    elif arg in ("4", "all"):
        build_portable()
        build_setup()
        build_onefile()
        clean_temp()
    else:
        print("Lütfen bir seçenek belirtin: portable, setup, onefile, all")


if __name__ == "__main__":
    main()
