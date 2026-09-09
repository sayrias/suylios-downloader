#!/usr/bin/env python3
"""
Suylios Downloader - Master Derleme ve Paketleme Üssü
======================================================
Kullanım:
    python build.py portable   # Taşınabilir ZIP (Suylios-Portable.zip)
    python build.py setup      # Kurulum Sihirbazı (Suylios-Setup.exe)
    python build.py onefile    # Tek Dosya Standalone (Suylios / Suylios.exe)
    python build.py all        # Hepsini sırayla derle
"""

import os
import sys
import shutil
import subprocess
import zipfile
import platform
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).parent.resolve()
SRC_DIR      = PROJECT_ROOT / "src"
DIST_DIR     = PROJECT_ROOT / "dist"
BUILD_DIR    = PROJECT_ROOT / "build"
BIN_DIR      = PROJECT_ROOT / "bin"

IS_WINDOWS = os.name == "nt"
IS_LINUX   = sys.platform.startswith("linux")

# ──────────────────────────────────────────────
#  Uygulama meta
# ──────────────────────────────────────────────
def _get_app_version() -> str:
    try:
        text = (SRC_DIR / "main.py").read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip().startswith("APP_VERSION ="):
                return line.split("=")[1].strip().strip('"\'')
    except Exception:
        pass
    return "1.4.3"

APP_NAME      = "Suylios Downloader"
APP_VERSION   = _get_app_version()
APP_PUBLISHER = "Suylios"
APP_URL       = "https://github.com/sayrias/suylios-downloader"
APP_EXE       = "suylios.exe"
DEFAULT_DIR   = r"{localappdata}\Programs\Suylios Downloader"

# ──────────────────────────────────────────────
#  Inno Setup şablonu
# ──────────────────────────────────────────────
INFO_TR = """\
=========================================================
   Suylios Downloader - Kurulum Sihirbazına Hoş Geldiniz
=========================================================

Modern ve kullanıcı dostu indirme uygulaması.
YouTube, Gofile, SimpCity ve 1000+ platform desteği.

Kuruluma devam etmek için İleri butonuna tıklayın.
"""

INFO_EN = """\
=========================================================
   Welcome to Suylios Downloader Setup Wizard
=========================================================

Modern and user-friendly download application.
Supports YouTube, Gofile, SimpCity and 1000+ platforms.

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
DefaultDirName={default_dir}
DefaultGroupName={app_name}
OutputDir={output_dir}
OutputBaseFilename=Suylios-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile={icon_file}
UninstallDisplayName={app_name}
UninstallDisplayIcon={{app}}\{app_exe}

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"; InfoBeforeFile: "{info_tr}"
Name: "english"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "{info_en}"

[Tasks]
Name: "desktopicon"; Description: "Masaüstünde kısayol / Desktop shortcut"; GroupDescription: "Kısayollar:"

[Files]
Source: "{staging_dir}\{app_exe}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{staging_dir}\_internal\*"; DestDir: "{{app}}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs; Attribs: hidden
Source: "{staging_dir}\bin\*"; DestDir: "{{app}}\bin"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{staging_dir}\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[INI]
Filename: "{{app}}\install_lang.ini"; Section: "Setup"; Key: "Language"; String: "tr"; Languages: turkish
Filename: "{{app}}\install_lang.ini"; Section: "Setup"; Key: "Language"; String: "en"; Languages: english
Filename: "{{app}}\installed_by_setup.flag"; Section: "Setup"; Key: "Installed"; String: "true"

[Icons]
Name: "{{autoprograms}}\{app_name}"; Filename: "{{app}}\{app_exe}"
Name: "{{autodesktop}}\{app_name}"; Filename: "{{app}}\{app_exe}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\{app_exe}"; Description: "{app_name} Başlat"; Flags: nowait postinstall skipifsilent
"""

# ──────────────────────────────────────────────
#  Boyut optimizasyonu — büyük gereksiz modüller
# ──────────────────────────────────────────────
# gallery-dl 300+ extractor'ı içeriyor; hepsini hidden import yerine
# subprocess üzerinden çağırıyoruz zaten, bu yüzden collect-all yerine
# sadece gerekli alt paketleri alıyoruz.
EXCLUDES = [
    # Test / geliştirme araçları
    # NOT: distutils exclude edilmemeli - PyInstaller hook-distutils.py alias çakışması yapar!
    "unittest", "test", "tests", "pip",
    "setuptools", "pkg_resources", "_pytest",
    # Kullanılmayan Python stdlib
    "tkinter", "turtle", "idlelib", "lib2to3",
    "xmlrpc", "ftplib", "imaplib", "poplib", "smtplib",
    "telnetlib", "sndhdr", "sunau", "aifc", "wave",
    "colorsys", "antigravity", "cgi", "cgitb",
    # Jupyter / IPython
    "IPython", "ipykernel", "jupyter",
    # Scipy / numpy / pandas (kullanılmıyor)
    "scipy", "numpy", "pandas", "matplotlib",
    # Android / iOS (gallery-dl platform shim'leri)
    "webview.platforms.android",
    "webview.platforms.ios",
    "android",
]

HIDDEN_IMPORTS_WIN = [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "pythonnet",
]

HIDDEN_IMPORTS_LINUX = [
    "webview.platforms.gtk",
    "webview.platforms.qt",
]

# gallery-dl ve yt-dlp core (extractor listesi runtime'da dinamik yükleniyor,
# subprocess subcommand olarak çalıştığı için full collect gerekmez)
HIDDEN_IMPORTS_COMMON = [
    "gallery_dl",
    "gallery_dl.extractor",
    "gallery_dl.downloader",
    "gallery_dl.postprocessor",
    "yt_dlp",
    "yt_dlp.extractor",
    "cyberdrop_dl",
    "webview",
    "curl_cffi",
    "aiohttp",
    "aiofiles",
    "bs4",
    "lxml",
    "lxml.etree",
    "PIL",
    "pystray",
    "mutagen",
]


# ──────────────────────────────────────────────
#  Yardımcılar
# ──────────────────────────────────────────────
def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[*] PyInstaller yükleniyor...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    # Python 3.14 setuptools hook yaması
    try:
        import PyInstaller.hooks
        hook_path = Path(PyInstaller.hooks.__file__).parent / "hook-setuptools.py"
        if hook_path.exists():
            content = hook_path.read_text(encoding="utf-8", errors="ignore")
            if "if setuptools_info.version < (71, 0):" in content:
                content = content.replace(
                    "if setuptools_info.version < (71, 0):",
                    "if setuptools_info.version and setuptools_info.version < (71, 0):",
                )
                hook_path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"PyInstaller patch (görmezden gelindi): {e}")


def _size_mb(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1024 / 1024:.1f} MB"
    except Exception:
        return "?"


# Qt modülleri – PyInstaller'ın exclude-module ile tutamadıklarını
# sıkıştırma öncesinde doğrudan silerek boyutu düşürüyoruz.
_QT_EXCLUDES_DIRS = [
    # Qt Quick / QML / 3D (pywebview tarafından kullanılmıyor)
    "Qt6/qml",
    "Qt6/lib/libQt6Quick",
    "Qt6/lib/libQt6Qml",
    "Qt6/lib/libQt6Quick3D",
    "Qt6/lib/libQt6VirtualKeyboard",
    "Qt6/lib/libQt6Location",
    "Qt6/lib/libQt6Positioning",
    "Qt6/lib/libQt6Sensors",
    "Qt6/lib/libQt6Charts",
    "Qt6/lib/libQt6DataVisualization",
    "Qt6/lib/libQt6Designer",
    "Qt6/lib/libQt6ShaderTools",
    "Qt6/lib/libQt6Pdf",
    "Qt6/lib/libQt6Quick3DPhysics",
    # Qt platformları (xcb dışındakiler)
    "Qt6/plugins/platforms/libqeglfs",
    "Qt6/plugins/platforms/libqminimal",
    "Qt6/plugins/platforms/libqoffscreen",
    "Qt6/plugins/platforms/libqvnc",
    "Qt6/plugins/platforms/libqwasm",
    # Asset importers (3D sahne)
    "Qt6/plugins/assetimporters",
    "Qt6/plugins/sceneparsers",
    "Qt6/plugins/qmlls",
    # Translations (sadece tr ve en bırak)
    "Qt6/translations",
    # Qt6 Quick Styles
    "Qt6/qsci",
]

_QT_LARGE_LIBS = [
    # WebEngine kendi Chromium motorunu taşıyan devasa kütüphane - 195MB!
    # pywebview GTK/Qt backend; WebEngineWidgets WebView için gerekli AMA
    # pywebview zaten sisteminizin GTK WebKit'ini kullanıyor Linux'ta.
    # Onefile build'de bu lib system Qt'ye link edildiğinden pakete girmesi gerekmez.
    "libQt6WebEngineCore.so*",
    "libQt6WebEngineWidgets.so*",
    "libQt6WebChannel.so*",
    "libQt6WebEngineQuick.so*",
    # ICU data (çok büyük, sistem kütüphanesi olarak zaten var)
    "libicudata.so*",
    # ffmpeg benzeri codec kütüphaneleri (sisteme güveneceğiz)
    "libavcodec.so*",
    "libavformat.so*",
    "libavutil.so*",
]


def _strip_qt(search_root: Path) -> float:
    """Verilen dizin altındaki gereksiz Qt dosyalarını sil. Boyut tasarrufunu MB olarak döndür."""
    removed_mb = 0.0

    def _try_remove(p: Path):
        nonlocal removed_mb
        try:
            sz = p.stat().st_size if p.is_file() else sum(
                f.stat().st_size for f in p.rglob("*") if f.is_file()
            )
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed_mb += sz / 1024 / 1024
        except Exception:
            pass

    # 1) Büyük klasörleri / prefix'leri sil
    for rel in _QT_EXCLUDES_DIRS:
        for match in search_root.rglob(rel.split("/")[-1] + "*" if "/" not in rel else ""):
            pass  # handled below
        # Doğrudan prefix arama
        for match in search_root.rglob("*"):
            if any(rel.replace("/", os.sep) in str(match) for rel in _QT_EXCLUDES_DIRS):
                if match.exists():
                    _try_remove(match)

    # 2) Büyük .so dosyalarını sil
    for pattern in _QT_LARGE_LIBS:
        for match in search_root.rglob(pattern):
            if match.is_file():
                _try_remove(match)

    return removed_mb


def _strip_internal(directory: Path):
    """Onedir _internal içindeki gereksiz Qt dosyalarını temizler."""
    internal = directory / "_internal"
    target = internal if internal.exists() else directory
    removed_mb = _strip_qt(target)
    if removed_mb > 0:
        print(f"[*] Qt temizliği: -{removed_mb:.0f} MB tasarruf")


def _build_pyinstaller(name: str, onefile: bool, workpath: Path) -> bool:
    """Ortak PyInstaller çağrısı. True döndürürse başarılı."""
    ensure_pyinstaller()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    main_script = SRC_DIR / "main.py"
    icon_path   = SRC_DIR / "ui" / "icon.ico"
    sep = ";" if IS_WINDOWS else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile" if onefile else "--onedir",
        "--windowed" if IS_WINDOWS else "--console",
        f"--name={name}",
        f"--add-data={SRC_DIR / 'ui'}{sep}ui",
        f"--distpath={DIST_DIR if onefile else BUILD_DIR}",
        f"--workpath={workpath}",
        f"--specpath={BUILD_DIR}",
        # UPX ile sıkıştır (varsa)
        "--upx-dir=/usr/bin" if shutil.which("upx") else "--noupx",
    ]

    # Exclude listesi
    for exc in EXCLUDES:
        cmd += ["--exclude-module", exc]

    # PyQt6 büyük modüllerini exclude et (pywebview onları direkt import etmiyor)
    qt_module_excludes = [
        "PyQt6.QtWebEngineWidgets",  # 195MB WebEngine - sistem GTK WebKit kullanılıyor
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineQuick",
        "PyQt6.QtWebChannel",
        "PyQt6.QtQuick",
        "PyQt6.QtQml",
        "PyQt6.QtQuickWidgets",
        "PyQt6.QtQuick3D",
        "PyQt6.Qt3DCore",
        "PyQt6.Qt3DRender",
        "PyQt6.Qt3DInput",
        "PyQt6.Qt3DLogic",
        "PyQt6.Qt3DAnimation",
        "PyQt6.Qt3DExtras",
        "PyQt6.QtDesigner",
        "PyQt6.QtMultimedia",
        "PyQt6.QtLocation",
        "PyQt6.QtPositioning",
        "PyQt6.QtSensors",
        "PyQt6.QtCharts",
        "PyQt6.QtPdf",
        "PyQt6.QtShaderTools",
        "PyQt6.QtSpatialAudio",
        "PyQt6.QtVirtualKeyboard",
        "PySide6",  # PySide6 varsa onu da dışla
    ] if not IS_WINDOWS else [
        # Windows'ta da aynı dışlamalar geçerli
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtQuick", "PyQt6.QtQml",
        "PySide6",
    ]
    for qexc in qt_module_excludes:
        cmd += ["--exclude-module", qexc]

    # Hidden imports
    hidden = HIDDEN_IMPORTS_COMMON[:]
    if IS_WINDOWS:
        hidden += HIDDEN_IMPORTS_WIN
    else:
        hidden += HIDDEN_IMPORTS_LINUX

    for hi in hidden:
        cmd += ["--hidden-import", hi]

    # gallery-dl + yt-dlp: collect-submodules (binary değil python modülleri)
    cmd += [
        "--collect-submodules=gallery_dl",
        "--collect-submodules=yt_dlp",
        "--collect-data=gallery_dl",
        "--collect-data=yt_dlp",
        "--collect-submodules=cyberdrop_dl",
        "--collect-data=cyberdrop_dl",
        "--collect-all=webview",
        "--collect-all=curl_cffi",
    ]

    # Windows'a özel
    if IS_WINDOWS:
        cmd += [
            "--collect-all=pythonnet",
            "--hidden-import=clr",
        ]

    json_sites_path = PROJECT_ROOT / "SUPPORTED_SITES.json"
    if json_sites_path.exists():
        cmd.append(f"--add-data={json_sites_path}{sep}.")

    ffmpeg_path = BIN_DIR / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
    if onefile and ffmpeg_path.exists():
        cmd.append(f"--add-binary={ffmpeg_path}{sep}bin")

    if icon_path.exists() and IS_WINDOWS:
        cmd.append(f"--icon={icon_path}")

    cmd.append(str(main_script))

    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return res.returncode == 0


# ──────────────────────────────────────────────
#  1. Taşınabilir ZIP
# ──────────────────────────────────────────────
def build_onedir() -> Path:
    build_out = BUILD_DIR / "SuyliosDownloader"

    # Önbellek kontrolü
    exe_name = "SuyliosDownloader.exe" if IS_WINDOWS else "SuyliosDownloader"
    if build_out.exists() and (build_out / exe_name).exists():
        print("[*] Önbellekten kullanılıyor: onedir derlemesi mevcut.")
        return build_out
    # Temiz build için önbelleği sil
    if build_out.exists():
        shutil.rmtree(build_out)

    print("[*] PyInstaller onedir derleniyor...")
    ok = _build_pyinstaller("SuyliosDownloader", onefile=False,
                             workpath=BUILD_DIR / "temp")
    if not ok:
        print("[HATA] Derleme başarısız!")
        sys.exit(1)

    _strip_internal(build_out)
    return build_out


def build_portable():
    print("\n" + "="*60)
    print("  1. Taşınabilir ZIP Paketi (Suylios-Portable.zip)")
    print("="*60)
    build_out = build_onedir()

    portable_dir = DIST_DIR / "Suylios-Portable"
    if portable_dir.exists():
        shutil.rmtree(portable_dir)
    portable_dir.mkdir(parents=True, exist_ok=True)

    try:
        for item in build_out.iterdir():
            dst = portable_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)

        # Kopyalanan _internal içinden de Qt temizliği yap
        _strip_internal(portable_dir)

        # Yürütülebilir adını düzenle
        exe_src = "SuyliosDownloader.exe" if IS_WINDOWS else "SuyliosDownloader"
        exe_dst = "suylios.exe" if IS_WINDOWS else "suylios"
        old = portable_dir / exe_src
        new = portable_dir / exe_dst
        if old.exists() and not new.exists():
            os.rename(old, new)
            if not IS_WINDOWS:
                new.chmod(0o755)

        # Dizin yapısı
        (portable_dir / "Downloads").mkdir(exist_ok=True)
        (portable_dir / "data").mkdir(exist_ok=True)
        (portable_dir / "portable.flag").write_text("Suylios Portable Mode", encoding="utf-8")

        # Linux başlatıcı scripti
        if not IS_WINDOWS:
            launcher = portable_dir / "baslat.sh"
            launcher.write_text(
                '#!/usr/bin/env bash\n'
                'SCRIPT_DIR="$(cd "$(dirname \"${BASH_SOURCE[0]}\")" && pwd)"\n'
                'exec "$SCRIPT_DIR/suylios" "$@"\n',
                encoding="utf-8"
            )
            launcher.chmod(0o755)

        # ffmpeg
        portable_bin = portable_dir / "bin"
        portable_bin.mkdir(exist_ok=True)
        for ff in ("ffmpeg.exe", "ffmpeg"):
            src = BIN_DIR / ff
            if src.exists():
                shutil.copy2(src, portable_bin / ff)

        # ZIP oluştur
        zip_path = DIST_DIR / "Suylios-Portable.zip"
        if zip_path.exists():
            zip_path.unlink()
        print("[*] ZIP sıkıştırılıyor... (bu işlem birkaç dakika sürebilir)")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for root, _, files in os.walk(portable_dir):
                for file in files:
                    fp = Path(root) / file
                    arcname = Path("Suylios-Portable") / fp.relative_to(portable_dir)
                    zf.write(fp, arcname)

        print(f"[TAMAM] Taşınabilir ZIP: {zip_path} ({_size_mb(zip_path)})")

    except Exception as exc:
        print(f"[HATA] Portable ZIP oluşturulamadı: {exc}")
        raise
    finally:
        # Her koşulda geçici klasörü temizle
        shutil.rmtree(portable_dir, ignore_errors=True)


# ──────────────────────────────────────────────
#  2. Kurulum Sihirbazı
# ──────────────────────────────────────────────
def find_inno_setup() -> Path | None:
    # Windows native yollar
    local = os.environ.get("LOCALAPPDATA", "")
    for p in [
        Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]:
        if p.exists():
            return p
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    # Linux'ta Wine içinde ara
    if IS_LINUX:
        home = Path.home()
        for wine_path in [
            home / ".wine/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe",
            home / ".wine/drive_c/Program Files/Inno Setup 6/ISCC.exe",
        ]:
            if wine_path.exists():
                return wine_path  # Caller wine prefix ile çağırır
    return None


def _run_inno(iss_path: Path):
    """Inno Setup'ı doğru ortamda çalıştır (native veya Wine)."""
    iscc = find_inno_setup()
    if not iscc:
        return False
    if IS_LINUX and ".wine" in str(iscc):
        # Wine path'ini Windows formatına çevir
        win_path = str(iscc).replace(str(Path.home() / ".wine/drive_c"), "C:").replace("/", "\\")
        win_iss  = str(iss_path).replace(str(Path.home() / ".wine/drive_c"), "C:").replace("/", "\\")
        res = subprocess.run(["wine", "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe", win_iss])
    else:
        res = subprocess.run([str(iscc), str(iss_path)])
    return res.returncode == 0


def find_wine_python() -> Path | None:
    candidates = [
        Path.home() / ".wine/drive_c/Python313/python.exe",
        Path.home() / ".wine/drive_c/Python312/python.exe",
        Path.home() / ".wine/drive_c/Python311/python.exe",
        Path.home() / ".wine/drive_c/Python310/python.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _build_setup_windows(build_out: Path):
    """Windows veya Wine+Inno ile setup.exe üret."""
    iscc = find_inno_setup()
    if not iscc:
        print("[UYARI] Inno Setup bulunamadı!")
        if IS_LINUX:
            print("        Linux'ta Setup.exe için: sudo dnf install wine && winetricks iscc")
        return

    staging_dir = DIST_DIR / "setup-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    for item in build_out.iterdir():
        dst = staging_dir / item.name
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, dst, **({"dirs_exist_ok": True} if item.is_dir() else {}))

    for old_name in ("SuyliosDownloader.exe", "SuyliosDownloader"):
        old = staging_dir / old_name
        if old.exists() and not (staging_dir / "suylios.exe").exists():
            os.rename(old, staging_dir / "suylios.exe")

    staging_bin = staging_dir / "bin"
    staging_bin.mkdir(exist_ok=True)
    for ff in ("ffmpeg.exe", "ffmpeg"):
        src = BIN_DIR / ff
        if src.exists():
            shutil.copy2(src, staging_bin / ff)

    icon_file    = SRC_DIR / "ui" / "icon.ico"
    info_tr_file = BUILD_DIR / "info_tr.txt"
    info_en_file = BUILD_DIR / "info_en.txt"
    info_tr_file.write_text(INFO_TR, encoding="utf-8")
    info_en_file.write_text(INFO_EN, encoding="utf-8")

    iss = INNO_SCRIPT_TEMPLATE.format(
        app_name=APP_NAME, app_version=APP_VERSION,
        app_publisher=APP_PUBLISHER, app_url=APP_URL, app_exe=APP_EXE,
        default_dir=DEFAULT_DIR, output_dir=str(DIST_DIR),
        staging_dir=str(staging_dir),
        info_tr=str(info_tr_file), info_en=str(info_en_file),
        icon_file=str(icon_file) if icon_file.exists() else "",
    )
    iss_path = BUILD_DIR / "suylios_setup.iss"
    iss_path.write_text(iss, encoding="utf-8")

    print("[*] Inno Setup derleniyor...")
    if IS_LINUX and not IS_WINDOWS:
        cmd = ["wine", str(iscc), str(iss_path)]
    else:
        cmd = [str(iscc), str(iss_path)]
    res = subprocess.run(cmd)
    shutil.rmtree(staging_dir, ignore_errors=True)

    if res.returncode == 0:
        out = DIST_DIR / "Suylios-Setup.exe"
        if out.exists():
            print(f"[TAMAM] Setup EXE: {out} ({_size_mb(out)})")


def build_setup():
    print("\n" + "="*60)
    print("  2. Kurulum Sihirbazı (Suylios-Setup.exe)")
    print("="*60)

    if IS_LINUX:
        # Linux: Wine ile Windows onedir oluştur + Inno ile paketle
        wine_py = find_wine_python()
        if wine_py:
            print(f"[INFO] Wine Python bulundu: {wine_py}")
            print("[*] Wine ile Windows onedir derleniyor...")
            res = subprocess.run([
                "wine", str(wine_py), "-m", "PyInstaller",
                "--noconfirm", "--onedir", "--windowed",
                "--name=SuyliosDownloader",
                f"--add-data=src/ui;ui",
                "--collect-submodules=gallery_dl",
                "--collect-submodules=yt_dlp",
                "--collect-data=gallery_dl",
                "--collect-data=yt_dlp",
                "--collect-submodules=cyberdrop_dl",
                "--collect-all=webview",
                "--collect-all=pythonnet",
                "--hidden-import=clr",
                "--hidden-import=webview.platforms.winforms",
                "--hidden-import=webview.platforms.edgechromium",
                *[f"--exclude-module={e}" for e in EXCLUDES],
                f"--distpath={BUILD_DIR}",
                f"--workpath={BUILD_DIR / 'temp_wine'}",
                f"--specpath={BUILD_DIR}",
                "src/main.py",
            ], cwd=str(PROJECT_ROOT))
            if res.returncode == 0:
                _build_setup_windows(BUILD_DIR / "SuyliosDownloader")
            else:
                print("[HATA] Wine derleme başarısız.")
        else:
            # Wine Python yok; mevcut Linux onedir'den Wine+Inno ile dene
            print("[UYARI] Wine Python bulunamadı. Mevcut Linux derlemesinden setup.exe oluşturuluyor...")
            build_out = build_onedir()
            _build_setup_windows(build_out)
    else:
        # Windows: doğrudan derle + Inno Setup
        build_out = build_onedir()
        _build_setup_windows(build_out)


# ──────────────────────────────────────────────
#  3. Tek Dosya Standalone
# ──────────────────────────────────────────────
def build_onefile():
    print("\n" + "="*60)
    suffix = " (Windows .exe)" if IS_WINDOWS else " (Linux binary)"
    print(f"  3. Tek Dosya Standalone{suffix}")
    print("="*60)

    workpath = BUILD_DIR / "temp_onefile"
    # İsim: Windows'ta Suylios-Windows.exe, Linux'ta Suylios-Linux
    out_name = "Suylios-Windows" if IS_WINDOWS else "Suylios-Linux"
    ok = _build_pyinstaller(out_name, onefile=True, workpath=workpath)
    if ok:
        out_ext = ".exe" if IS_WINDOWS else ""
        out = DIST_DIR / f"{out_name}{out_ext}"
        if out.exists():
            if not IS_WINDOWS:
                out.chmod(0o755)
            size_before = out.stat().st_size / 1024 / 1024
            print(f"[TAMAM] Tek Dosya: {out} ({size_before:.1f} MB)")

            # UPX ile ek sıkıştırma (varsa)
            if shutil.which("upx"):
                print("[*] UPX sıkıştırması uygulanıyor...")
                try:
                    subprocess.run(["upx", "--best", "--lzma", str(out)],
                                   capture_output=True, timeout=300)
                    size_after = out.stat().st_size / 1024 / 1024
                    print(f"[*] UPX sonrası: {size_after:.1f} MB "
                          f"(tasarruf: {size_before - size_after:.1f} MB)")
                except Exception:
                    pass


# ──────────────────────────────────────────────
#  Temizlik
# ──────────────────────────────────────────────
def clean_temp():
    print("[*] Geçici derleme dosyaları temizleniyor...")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    shutil.rmtree(DIST_DIR / "setup-staging", ignore_errors=True)
    for f in PROJECT_ROOT.glob("*.spec"):
        try:
            f.unlink()
        except Exception:
            pass


# ──────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────
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
        print("Kullanım: build.py [portable|setup|onefile|all]")


if __name__ == "__main__":
    main()
