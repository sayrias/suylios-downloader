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
    local INNO_URL="https://files.jrsoftware.org/is/6/innosetup-6.3.3.exe"
    local INNO_TMP="/tmp/innosetup.exe"
    curl -L --progress-bar "$INNO_URL" -o "$INNO_TMP" 2>/dev/null
    wine "$INNO_TMP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART 2>/dev/null
    rm -f "$INNO_TMP"
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

# ── Linux .run paketi ─────────────────────────────────────
make_linux_run() {
    PORTABLE_ZIP=$(ls dist/Suylios-Portable*.zip 2>/dev/null | head -1)
    if [ -z "$PORTABLE_ZIP" ]; then
        echo "[UYARI] Linux .run atlandı: önce Portable ZIP gerekli."
        return
    fi

    RUN_OUT="${PORTABLE_ZIP%.zip}.run"
    TMP_DIR=$(mktemp -d)
    TMP_PAYLOAD="$TMP_DIR/payload.tar.gz"
    TMP_HEADER="$TMP_DIR/header.sh"

    cd dist
    unzip -q "$(basename "$PORTABLE_ZIP")" -d "$TMP_DIR/content" 2>/dev/null
    tar czf "$TMP_PAYLOAD" -C "$TMP_DIR/content" .
    cd "$SCRIPT_DIR"

    cat > "$TMP_HEADER" << 'RUNEOF'
#!/usr/bin/env bash
TMPDIR=$(mktemp -d)
SELF=$(readlink -f "$0")
SKIP=$(awk '/^__PAYLOAD__$/{print NR+1; exit}' "$SELF")
tail -n +"$SKIP" "$SELF" | tar -xz -C "$TMPDIR"
cd "$TMPDIR" && bash baslat.sh
rm -rf "$TMPDIR"
exit 0
__PAYLOAD__
RUNEOF

    cat "$TMP_HEADER" "$TMP_PAYLOAD" > "dist/$(basename "$RUN_OUT")"
    chmod +x "dist/$(basename "$RUN_OUT")"
    rm -rf "$TMP_DIR"
    echo "[OK] Linux .run: dist/$(basename "$RUN_OUT")"
}

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
    wine "$WIN_PY" -m pip show pyinstaller &>/dev/null || \
        wine "$WIN_PY" -m pip install --quiet pyinstaller pywebview yt-dlp gallery-dl requests aiohttp cyberdrop-dl 2>/dev/null

    mkdir -p "$SCRIPT_DIR/dist" "$SCRIPT_DIR/build/temp_wine_onefile" "$SCRIPT_DIR/build"

    wine "$WIN_PY" -m PyInstaller \
        --noconfirm --onefile --windowed \
        --name "Suylios" \
        --icon "$WINE_ICON" \
        --add-data "${WINE_SRC_UI};ui" \
        --collect-all gallery_dl \
        --collect-all yt_dlp \
        --collect-all webview \
        --collect-all pythonnet \
        --hidden-import clr \
        --hidden-import webview.platforms.winforms \
        --hidden-import webview.platforms.edgechromium \
        --exclude-module unittest \
        --exclude-module test \
        --exclude-module scipy \
        --exclude-module numpy \
        --exclude-module pandas \
        --exclude-module tkinter \
        --distpath "$WINE_DIST" \
        --workpath "$WINE_WORK" \
        --specpath "$WINE_SPEC" \
        "$WINE_MAIN" 2>&1 | grep -v "^0.*fixme:" | grep -v "^0.*warn:"
    if [ ${PIPESTATUS[0]} -eq 0 ]; then
        echo "[OK] Windows EXE: dist/Suylios.exe ($(du -sh "$SCRIPT_DIR/dist/Suylios.exe" 2>/dev/null | cut -f1))"
    else
        echo "[HATA] Wine derleme başarısız."
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
    echo "   1 - Linux Paketi  → Portable ZIP + .run"
    echo "   2 - Linux Binary  → Tek Dosya Çalıştırılabilir"
    echo "   3 - Windows EXE   → Wine ile .exe (onefile)"
    echo "   4 - Windows Setup → Wine + Inno Setup installer"
    echo "   5 - Hepsini Derle → 1+2+3+4 sırayla"
    echo "   0 - Çıkış"
    echo ""
    echo "========================================================="
    read -p "Seçiminiz (0-5): " secim
    echo ""

    case "$secim" in
        1)
            echo "[INFO] Linux Portable paketi derleniyor..."
            $PY_CMD build.py portable
            make_linux_run
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
                    echo "[UYARI] Inno Setup bulunamadı."
                    echo ""
                    echo "   Otomatik kurulsun mu? (Wine'a Inno Setup indirilir ~5MB)"
                    read -p "   Evet için [e], hayır için [h]: " inno_choice
                    if [ "$inno_choice" = "e" ] || [ "$inno_choice" = "E" ]; then
                        install_inno_wine
                        INNO=$(find_inno)
                    fi
                fi
                if [ -n "$INNO" ]; then
                    # Önce Windows EXE derle (wine), sonra Inno ile paketle
                    build_windows_wine_onefile
                    $PY_CMD build.py setup
                else
                    echo "[UYARI] Setup.exe atlandı. Inno Setup kurulmadı."
                fi
            fi
            read -p "Tamamlandı! Enter'a basın..."
            show_menu
            ;;
        5)
            echo "[INFO] Tüm paketler sırayla derleniyor..."
            echo ""

            # 1) Linux portable + .run
            echo "─── [1/4] Linux Portable + .run ───"
            $PY_CMD build.py portable
            make_linux_run

            # 2) Linux binary
            echo ""
            echo "─── [2/4] Linux Tek Dosya Binary ───"
            $PY_CMD build.py onefile

            # 3) Windows EXE (varsa)
            echo ""
            echo "─── [3/4] Windows EXE ───"
            if [ "$HOST_OS" = "Windows" ]; then
                # Windows native'de zaten onefile çalışır, EXE mevcut
                echo "[INFO] Windows native: EXE [2/4] adımında üretildi."
            elif has_wine; then
                WIN_PY=$(find_wine_python)
                if [ -n "$WIN_PY" ]; then
                    build_windows_wine_onefile
                else
                    echo "[UYARI] Windows EXE atlandı: Wine Python yok."
                fi
            else
                echo "[UYARI] Windows EXE atlandı: Wine yok."
            fi

            # 4) Windows Setup (varsa)
            echo ""
            echo "─── [4/4] Windows Setup.exe ───"
            INNO=$(find_inno)
            if [ -n "$INNO" ]; then
                $PY_CMD build.py setup
            else
                echo "[UYARI] Setup.exe atlandı: Inno Setup yok."
                if ! has_wine; then
                    echo "        (Wine: yok, Inno Setup: yok)"
                fi
            fi

            echo ""
            echo "=========================================="
            echo "[TAMAM] Tüm derleme işlemleri tamamlandı."
            echo ""
            echo "dist/ klasöründeki çıktılar:"
            ls -lh dist/ 2>/dev/null | awk 'NR>1 {printf "  %-40s %s\n", $NF, $5}'
            echo "=========================================="
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
