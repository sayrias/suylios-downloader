#!/usr/bin/env bash

# =========================================================
#    Suylios Downloader - Linux Derleme Üssü (derle.sh)
# =========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Sanal ortam kontrolü ve aktif etme (--system-site-packages zorunludur)
if [ ! -f "venv/bin/activate" ]; then
    echo "[INFO] Sanal ortam (venv) bulunamadı. Otomatik oluşturuluyor (--system-site-packages ile)..."
    python3 -m venv --system-site-packages venv
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
        pip install --upgrade pip
        pip install -r requirements.txt
    fi
else
    if [ -f "venv/pyvenv.cfg" ]; then
        sed -i 's/include-system-site-packages = false/include-system-site-packages = true/g' venv/pyvenv.cfg
    fi
    source venv/bin/activate
fi

PY_CMD="python3"

show_menu() {
    clear
    echo "========================================================="
    echo "    Suylios Downloader - Linux Derleme Üssü"
    echo "========================================================="
    echo ""
    echo "   1 - Taşınabilir ZIP Paketi Derle (Suylios-Portable.zip)"
    echo "   2 - Kurulum Sihirbazı Derle (Suylios-Setup.exe / Installer)"
    echo "   3 - Tek Dosya Standalone Derle (Onefile Binary)"
    echo "   4 - Hepsini Sırayla Derle (Tümünü Üret)"
    echo "   0 - Çıkış"
    echo ""
    echo "========================================================="
    read -p "Seçiminiz (0-4): " secim
    echo ""

    case "$secim" in
        1)
            $PY_CMD build.py portable
            read -p "İşlem tamamlandı! Devam etmek için Enter tuşuna basın..."
            show_menu
            ;;
        2)
            $PY_CMD build.py setup
            read -p "İşlem tamamlandı! Devam etmek için Enter tuşuna basın..."
            show_menu
            ;;
        3)
            $PY_CMD build.py onefile
            read -p "İşlem tamamlandı! Devam etmek için Enter tuşuna basın..."
            show_menu
            ;;
        4)
            $PY_CMD build.py all
            read -p "İşlem tamamlandı! Devam etmek için Enter tuşuna basın..."
            show_menu
            ;;
        0)
            exit 0
            ;;
        *)
            show_menu
            ;;
    esac
}

show_menu
