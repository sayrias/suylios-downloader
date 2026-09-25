#!/usr/bin/env bash
# =========================================================
#    Suylios Downloader - Derleme Üssü
#    OS'a göre otomatik araç seçimi
# =========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# ── Sanal ortam hazırlığı ─────────────────────────────────
if [ ! -f "venv/bin/activate" ]; then
    echo "[INFO] Sanal ortam oluşturuluyor (--system-site-packages)..."
    python3 -m venv --system-site-packages venv
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
else
    if [ -f "venv/pyvenv.cfg" ]; then
        sed -i 's/include-system-site-packages = false/include-system-site-packages = true/g' venv/pyvenv.cfg
    fi
    source venv/bin/activate
fi

PY_CMD="python3"

# ── OS tespiti ───────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)   HOST_OS="Linux" ;;
        Darwin*)  HOST_OS="macOS" ;;
        CYGWIN*|MINGW*|MSYS*) HOST_OS="Windows" ;;
        *)        HOST_OS="Unknown" ;;
    esac
}
detect_os

# ── Araç kontrolleri ──────────────────────────────────────
has_wine() { command -v wine &>/dev/null; }
has_upx()  { command -v upx  &>/dev/null; }

find_wine_python() {
    for ver in Python313 Python312 Python311 Python310; do
        p="$HOME/.wine/drive_c/$ver/python.exe"
        [ -f "$p" ] && echo "$p" && return
    done
    echo ""
}

find_inno() {
    # Wine'da Inno Setup
    for path in \
        "$HOME/.wine/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe" \
        "$HOME/.wine/drive_c/Program Files/Inno Setup 6/ISCC.exe"; do
        [ -f "$path" ] && echo "wine" && return
    done
    # Native (Windows ortamı)
    command -v ISCC &>/dev/null && echo "ISCC" && return
    echo ""
}

has_nsis() { command -v makensis &>/dev/null; }

install_inno_wine() {
    echo "[INFO] Inno Setup Wine'a kuruluyor..."
    local INNO_TMP="/tmp/innosetup-$(date +%s).exe"

    # Birden fazla mirror dene
    local URLS=(
        "https://files.jrsoftware.org/is/6/innosetup-6.3.3.exe"
        "https://github.com/jrsoftware/issrc/releases/download/is-6_3_3/innosetup-6.3.3.exe"
        "https://jrsoftware.org/download.php/is.exe"
    )

    local downloaded=0
    for url in "${URLS[@]}"; do
        echo "[INFO] Deneniyor: $url"
        curl -L -A "Mozilla/5.0" --max-time 60 -o "$INNO_TMP" "$url" 2>/dev/null
        # PE/EXE mi kontrol et (magic bytes: MZ)
        if [ -f "$INNO_TMP" ] && [ "$(head -c 2 "$INNO_TMP" | xxd -p 2>/dev/null)" = "4d5a" ]; then
            echo "[INFO] İndirme başarılı: $(du -sh "$INNO_TMP" | cut -f1)"
            downloaded=1
            break
        else
            echo "[UYARI] Geçersiz dosya, sonraki mirror deneniyor..."
            rm -f "$INNO_TMP"
        fi
    done

    if [ "$downloaded" -eq 0 ]; then
        echo "[HATA] Inno Setup indirilemedi. Manuel kurulum:"
        echo "   1. https://jrsoftware.org/isdl.php adresinden indir"
        echo "   2. wine innosetup-6.x.x.exe /VERYSILENT"
        return 1
    fi

    echo "[INFO] Wine ile sessiz kurulum yapılıyor..."
    wine "$INNO_TMP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART 2>/dev/null
    local exit_code=$?
    rm -f "$INNO_TMP"

    if [ $exit_code -eq 0 ] || find_inno | grep -q wine; then
        echo "[OK] Inno Setup kuruldu."
    else
        echo "[UYARI] Kurulum çıkış kodu: $exit_code (yine de devam ediliyor)"
    fi
    find_inno
}

# ── Windows kurulum önkoşulları ───────────────────────────
setup_wine_windows() {
    echo ""
    echo "[INFO] Windows EXE/Setup için ön koşullar kontrol ediliyor..."

    if ! has_wine; then
        echo "[HATA] Wine kurulu değil!"
        echo "       Kurmak için:"
        echo "       Fedora/RHEL : sudo dnf install wine"
        echo "       Ubuntu/Debian: sudo apt install wine"
        return 1
    fi

    WIN_PY=$(find_wine_python)
    if [ -z "$WIN_PY" ]; then
        echo ""
        echo "[UYARI] Wine içinde Windows Python bulunamadı."
        echo "        Şu adımları izleyin:"
        echo "  1) winecfg  → Windows 10 seçin"
        echo "  2) Windows Python 3.13 indirin:"
        echo "     https://www.python.org/downloads/windows/"
        echo "  3) wine python-3.13.x-amd64.exe  → 'Add to PATH' seçin"
        echo "  4) wine python.exe -m pip install pyinstaller pywebview yt-dlp gallery-dl cyberdrop-dl"
        echo "  5) (Setup.exe için) wine msiexec /i innosetup-6.x.x.exe"
        return 1
    fi

    echo "[OK] Wine Python: $WIN_PY"
    return 0
}

# .run paketi kaldırıldı - gerek yok
make_linux_run() { :; }

# ── Wine ile Windows EXE (tek dosya) ──────────────────────
build_windows_wine_onefile() {
    WIN_PY=$(find_wine_python)
    echo "[INFO] Wine PyInstaller başlatılıyor..."

    # Linux mutlak yolları Wine Z:\ formatına çevir
    WINE_SRC_UI="Z:$(echo "$SCRIPT_DIR/src/ui" | sed 's|/|\\\\|g')"
    WINE_MAIN="Z:$(echo "$SCRIPT_DIR/src/main.py" | sed 's|/|\\\\|g')"
    WINE_DIST="Z:$(echo "$SCRIPT_DIR/dist" | sed 's|/|\\\\|g')"
    WINE_WORK="Z:$(echo "$SCRIPT_DIR/build/temp_wine_onefile" | sed 's|/|\\\\|g')"
    WINE_SPEC="Z:$(echo "$SCRIPT_DIR/build" | sed 's|/|\\\\|g')"
    WINE_ICON="Z:$(echo "$SCRIPT_DIR/src/ui/icon.ico" | sed 's|/|\\\\|g')"

    # Wine Python'da gerekli paketleri kur (henüz kurulmadıysa)
    echo "[INFO] Wine Python bağımlılıkları kontrol ediliyor..."
    wine "$WIN_PY" -m pip install --quiet --upgrade \
        pyinstaller pywebview yt-dlp gallery-dl requests \
        aiohttp aiofiles cyberdrop-dl pystray pillow \
        lxml beautifulsoup4 mutagen async-mega-py pycryptodome 2>/dev/null
    echo "[INFO] Wine pip kurulumu tamamlandı."

    mkdir -p "$SCRIPT_DIR/dist" "$SCRIPT_DIR/build/temp_wine_onefile" "$SCRIPT_DIR/build"

    WINE_SITES_JSON="Z:$(echo "$SCRIPT_DIR/SUPPORTED_SITES.json" | sed 's|/|\\\\|g')"

    wine "$WIN_PY" -m PyInstaller \
        --noconfirm --onefile --windowed \
        --name "Suylios" \
        --icon "$WINE_ICON" \
        --add-data "${WINE_SRC_UI};ui" \
        --add-data "${WINE_SITES_JSON};." \
        --collect-submodules gallery_dl \
        --collect-data gallery_dl \
        --collect-submodules yt_dlp \
        --collect-data yt_dlp \
        --collect-submodules cyberdrop_dl \
        --collect-data cyberdrop_dl \
        --collect-all webview \
        --collect-all pythonnet \
        --collect-all curl_cffi \
        --hidden-import clr \
        --hidden-import webview.platforms.winforms \
        --hidden-import webview.platforms.edgechromium \
        --hidden-import aiohttp \
        --hidden-import aiofiles \
        --hidden-import pystray \
        --hidden-import PIL \
        --hidden-import mutagen \
        --exclude-module unittest \
        --exclude-module test \
        --exclude-module scipy \
        --exclude-module numpy \
        --exclude-module pandas \
        --exclude-module tkinter \
        --exclude-module PyQt6.QtWebEngineWidgets \
        --exclude-module PyQt6.QtWebEngineCore \
        --collect-submodules mega \
        --collect-all Crypto \
        --distpath "$WINE_DIST" \
        --workpath "$WINE_WORK" \
        --specpath "$WINE_SPEC" \
        "$WINE_MAIN" 2>&1 | grep -v "^0.*fixme:" | grep -v "^0.*warn:"

    # Wine exit code (PIPESTATUS[0]) is unreliable – check the actual EXE
    if [ -f "$SCRIPT_DIR/dist/Suylios.exe" ]; then
        echo "[OK] Windows EXE: dist/Suylios.exe ($(du -sh "$SCRIPT_DIR/dist/Suylios.exe" 2>/dev/null | cut -f1))"
    else
        echo "[HATA] Wine derleme başarısız – dist/Suylios.exe bulunamadı."
    fi
}

# ── Menü ──────────────────────────────────────────────────
show_menu() {
    clear
    echo "========================================================="
    echo "    Suylios Downloader - Derleme Üssü"
    echo "    Host OS: $HOST_OS"
    echo ""

    if [ "$HOST_OS" = "Linux" ]; then
        echo "    Wine: $(has_wine && echo "Kurulu ✓" || echo "Yok ✗")"
        WIN_PY=$(find_wine_python)
        echo "    Wine Python: $([ -n "$WIN_PY" ] && echo "Kurulu ✓ ($WIN_PY)" || echo "Yok ✗")"
        INNO=$(find_inno)
        echo "    Inno Setup: $([ -n "$INNO" ] && echo "Kurulu ✓" || echo "Yok ✗")"
    fi
    echo "    UPX: $(has_upx && echo "Kurulu ✓" || echo "Yok (olmasa da çalışır)")"
    echo ""
    echo "========================================================="
    echo ""
    echo "   1 - Linux Portable  → Suylios-Portable.zip"
    echo "   2 - Linux Binary    → Suylios-Linux (tek dosya)"
    echo "   3 - Windows EXE     → Wine ile Suylios.exe"
    echo "   4 - Windows Setup   → Wine + Inno Setup installer"
    echo "   5 - Hepsini Derle   → 1+2+3+4 sırayla"
    echo "   0 - Çıkış"
    echo ""
    echo "========================================================="
    read -p "Seçiminiz (0-5): " secim
    echo ""

    case "$secim" in
        1)
            echo "[INFO] Linux Portable ZIP derleniyor..."
            $PY_CMD build.py portable
            read -p "Tamamlandı! Enter'a basın..."
            show_menu
            ;;
        2)
            echo "[INFO] Linux tek dosya binary derleniyor..."
            $PY_CMD build.py onefile
            read -p "Tamamlandı! Enter'a basın..."
            show_menu
            ;;
        3)
            if [ "$HOST_OS" = "Windows" ]; then
                echo "[INFO] Windows EXE derleniyor (native)..."
                $PY_CMD build.py onefile
            else
                setup_wine_windows || { read -p "Enter'a basın..."; show_menu; return; }
                build_windows_wine_onefile
            fi
            read -p "Tamamlandı! Enter'a basın..."
            show_menu
            ;;
        4)
            if [ "$HOST_OS" = "Windows" ]; then
                echo "[INFO] Windows Setup.exe derleniyor (native Inno Setup)..."
                $PY_CMD build.py setup
            else
                setup_wine_windows || { read -p "Enter'a basın..."; show_menu; return; }
                INNO=$(find_inno)
                if [ -z "$INNO" ]; then
                    echo "[INFO] Inno Setup bulunamadı, otomatik kuruluyor..."
                    install_inno_wine
                    INNO=$(find_inno)
                fi
                if [ -n "$INNO" ]; then
                    # Önce Windows EXE derle (wine), sonra Inno ile paketle
                    build_windows_wine_onefile
                    $PY_CMD build.py setup
                else
                    echo "[HATA] Setup.exe üretilemedi: Inno Setup kurulamadı."
                    echo "  Manuel kurulum için: https://jrsoftware.org/isdl.php"
                fi
            fi
            read -p "Tamamlandı! Enter'a basın..."
            show_menu
            ;;
        5)
            LOG_FILE="$SCRIPT_DIR/logs/build-$(date +%Y%m%d-%H%M%S).log"
            mkdir -p "$SCRIPT_DIR/logs"
            echo "[INFO] Tüm paketler sırayla derleniyor..."
            echo "[INFO] Build logu: $LOG_FILE"
            echo ""

            # 1) Linux binary (önce bu; portable için gerekli)
            echo "─── [1/4] Linux Tek Dosya Binary ───" | tee -a "$LOG_FILE"
            $PY_CMD build.py onefile 2>&1 | tee -a "$LOG_FILE"

            # 2) Linux Portable ZIP
            echo "" | tee -a "$LOG_FILE"
            echo "─── [2/4] Linux Portable ZIP ───" | tee -a "$LOG_FILE"
            $PY_CMD build.py portable 2>&1 | tee -a "$LOG_FILE"

            # 3) Windows EXE (varsa)
            echo "" | tee -a "$LOG_FILE"
            echo "─── [3/4] Windows EXE ───" | tee -a "$LOG_FILE"
            if [ "$HOST_OS" = "Windows" ]; then
                echo "[INFO] Windows native: EXE zaten mevcut." | tee -a "$LOG_FILE"
            elif has_wine; then
                WIN_PY=$(find_wine_python)
                if [ -n "$WIN_PY" ]; then
                    build_windows_wine_onefile 2>&1 | tee -a "$LOG_FILE"
                else
                    echo "[UYARI] Windows EXE atlandı: Wine Python yok." | tee -a "$LOG_FILE"
                fi
            else
                echo "[UYARI] Windows EXE atlandı: Wine yok." | tee -a "$LOG_FILE"
            fi

            # 4) Windows Setup (varsa)
            echo "" | tee -a "$LOG_FILE"
            echo "─── [4/4] Windows Setup.exe ───" | tee -a "$LOG_FILE"
            INNO=$(find_inno)
            if [ -z "$INNO" ] && has_wine; then
                echo "[INFO] Inno Setup yok, otomatik kuruluyor..." | tee -a "$LOG_FILE"
                install_inno_wine 2>&1 | tee -a "$LOG_FILE"
                INNO=$(find_inno)
            fi
            if [ -n "$INNO" ]; then
                $PY_CMD build.py setup 2>&1 | tee -a "$LOG_FILE"
            else
                echo "[UYARI] Setup.exe atlandı: Inno Setup kurulamadı." | tee -a "$LOG_FILE"
                echo "  İndir: https://jrsoftware.org/isdl.php → wine innosetup-6.x.exe /VERYSILENT" | tee -a "$LOG_FILE"
            fi

            echo "" | tee -a "$LOG_FILE"
            echo "==========================================" | tee -a "$LOG_FILE"
            echo "[TAMAM] Tüm derleme işlemleri tamamlandı." | tee -a "$LOG_FILE"
            echo "" | tee -a "$LOG_FILE"
            echo "dist/ klasöründeki çıktılar:" | tee -a "$LOG_FILE"
            ls -lh dist/ 2>/dev/null | awk 'NR>1 {printf "  %-40s %s\n", $NF, $5}' | tee -a "$LOG_FILE"
            echo "==========================================" | tee -a "$LOG_FILE"
            echo ""
            echo "[LOG] Tam build logu: $LOG_FILE"
            read -p "Devam etmek için Enter'a basın..."
            show_menu
            ;;
        0)
            echo "Çıkılıyor..."
            exit 0
            ;;
        *)
            show_menu
            ;;
    esac
}

show_menu
