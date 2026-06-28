#!/usr/bin/env python3
"""
Suylios Downloader - Setup EXE Builder
========================================
PyInstaller ile hızlıca derler ve Inno Setup ile çok dilli kurulum sihirbazı (.exe) oluşturur.
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
SETUP_STAGING = DIST_DIR / "setup-staging"

APP_NAME = "Suylios Downloader"
APP_VERSION = "1.0.0"
APP_PUBLISHER = "Suylios"
APP_URL = "https://github.com/suylios/suylios-downloader"
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


def find_inno_setup() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    common_paths = [
        Path(local_app_data) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
    ]
    for p in common_paths:
        if p.exists():
            return p

    result = shutil.which("ISCC")
    if result:
        return Path(result)
    return None


def compile_app():
    print("[*] PyInstaller ile hizli derleme basliyor (yaklasik 15 saniye)...")
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
    print("[TAMAM] Uygulama derlendi.")


def prepare_staging():
    print("[*] Setup icin klasorler hazirlaniyor...")
    if SETUP_STAGING.exists():
        shutil.rmtree(SETUP_STAGING)
    SETUP_STAGING.mkdir(parents=True, exist_ok=True)

    build_out = BUILD_DIR / "SuyliosDownloader"
    if build_out.exists():
        for item in build_out.iterdir():
            if item.is_dir():
                shutil.copytree(item, SETUP_STAGING / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, SETUP_STAGING / item.name)

        if (SETUP_STAGING / "SuyliosDownloader.exe").exists():
            if (SETUP_STAGING / "suylios.exe").exists():
                os.remove(SETUP_STAGING / "suylios.exe")
            os.rename(SETUP_STAGING / "SuyliosDownloader.exe", SETUP_STAGING / "suylios.exe")

    staging_bin = SETUP_STAGING / "bin"
    staging_bin.mkdir(exist_ok=True)
    ffmpeg_src = BIN_DIR / "ffmpeg.exe"
    if ffmpeg_src.exists():
        shutil.copy2(ffmpeg_src, staging_bin / "ffmpeg.exe")

    internal_dir = SETUP_STAGING / "_internal"
    if internal_dir.exists():
        try:
            subprocess.run(["attrib", "+h", str(internal_dir)], check=False)
        except Exception:
            pass

    print("[TAMAM] Setup staging hazir.")


def build_setup_exe():
    iscc = find_inno_setup()
    
    icon_file = SRC_DIR / "ui" / "icon.ico"
    icon_line = str(icon_file) if icon_file.exists() else ""

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
        staging_dir=str(SETUP_STAGING),
        info_tr=str(info_tr_file),
        info_en=str(info_en_file),
        icon_file=icon_line,
    )

    iss_path = BUILD_DIR / "suylios_setup.iss"
    iss_path.write_text(iss_content, encoding="utf-8")

    if not iscc:
        print("\n" + "="*60)
        print("[BILGI] Inno Setup bilgisayarinizda kurulu degil!")
        print("Setup (.exe) kurulum dosyasi olusturmak icin ucretsiz Inno Setup gereklidir.")
        print("1. Indirme Linki: https://jrsoftware.org/isdl.php")
        print("2. 'Inno Setup 6' programini indirip kurun.")
        print("3. Bu komutu tekrar calistirdiginizda otomatik olarak Suylios-Setup.exe olusacaktir.")
        print("="*60 + "\n")
        return

    print("[*] Inno Setup ile kurulum sihirbazi (Setup EXE) olusturuluyor...")
    result = subprocess.run([str(iscc), str(iss_path)])
    if result.returncode != 0:
        print("[HATA] Inno Setup derleme basarisiz!")
        sys.exit(1)

    print("[TAMAM] Setup EXE olusturuldu!")


def main():
    print("=" * 60)
    print(f"  {APP_NAME} - Setup Derleyici")
    print("=" * 60)
    print()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    compile_app()
    prepare_staging()
    build_setup_exe()

    print()
    print("=" * 60)
    setup_exe = DIST_DIR / "Suylios-Setup.exe"
    if setup_exe.exists():
        size_mb = setup_exe.stat().st_size / (1024 * 1024)
        print(f"  [TAMAM] Setup Hazir: {setup_exe} ({size_mb:.1f} MB)")
    else:
        print(f"  [BILGI] Dosyalar hazirlandi. Inno Setup kurduktan sonra tekrar deneyin.")
    print("=" * 60)


if __name__ == "__main__":
    main()
