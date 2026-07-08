#!/usr/bin/env bash

# =========================================================
#    Suylios Downloader - Linux Başlatıcı (baslat.sh)
# =========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "========================================================="
echo "       Suylios Downloader - Linux Başlatıcı"
echo "========================================================="
echo ""
echo "[INFO] Suylios Downloader başlatılıyor..."

# 1. Python 3 Kontrolü
if ! command -v python3 &> /dev/null; then
    echo "[HATA] Sisteminizde Python 3 bulunamadı!"
    echo "       Lütfen terminalden Python 3 kurun:"
    echo "       Ubuntu/Debian : sudo apt update && sudo apt install python3 python3-pip python3-venv"
    echo "       Arch Linux    : sudo pacman -S python python-pip"
    echo "       Fedora        : sudo dnf install python3 python3-pip"
    echo ""
    read -p "Çıkmak için Enter tuşuna basın..."
    exit 1
fi

# 2. Sanal Ortam (venv) Kontrolü, Otomatik Oluşturma ve Aktif Etme
# NOT: Linux GUI (GTK/WebKit) sistem kütüphanelerini görebilmesi için --system-site-packages zorunludur!
if [ ! -f "venv/bin/activate" ]; then
    echo "[INFO] Sanal ortam (venv) bulunamadı. Otomatik oluşturuluyor (--system-site-packages ile)..."
    python3 -m venv --system-site-packages venv
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
        echo "[INFO] Sanal ortam aktif edildi. Bağımlılıklar (requirements.txt) yükleniyor..."
        pip install --upgrade pip
        pip install -r requirements.txt
    else
        echo "[HATA] Sanal ortam oluşturulamadı! 'python3-venv' kurulu olmayabilir."
        echo "       Lütfen kurup tekrar deneyin: sudo apt update && sudo apt install python3-venv python3-pip"
        read -p "Çıkmak için Enter tuşuna basın..."
        exit 1
    fi
else
    echo "[INFO] Sanal ortam (venv) aktif ediliyor..."
    # Mevcut venv sistem paketlerini görmüyorsa pyvenv.cfg dosyasını otomatik düzelt
    if [ -f "venv/pyvenv.cfg" ]; then
        sed -i 's/include-system-site-packages = false/include-system-site-packages = true/g' venv/pyvenv.cfg
    fi
    source venv/bin/activate
fi

# 3. Linux GUI (WebKit2GTK / PyGObject) Kontrolü
if ! python3 -c "import gi" &> /dev/null && ! python -c "import gi" &> /dev/null; then
    echo "========================================================="
    echo "[ÖNEMLİ UYARI] Linux GUI (GTK/WebKit) Kütüphaneleri Eksik!"
    echo "========================================================="
    echo "PyWebView'in Linux üzerinde pencere açabilmesi için GTK ve WebKit sistem kütüphaneleri şarttır."
    echo "Şu an sisteminizde 'gi' (python3-gi / PyGObject) eksik olduğu için arayüz açılamaz."
    echo ""
    echo "Lütfen terminalinizde şu komutu çalıştırarak gerekli sistem paketlerini kurun:"
    echo "  apt update && apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 libwebkit2gtk-4.1-0"
    echo "========================================================="
    echo ""
fi

# 4. FFmpeg Kontrolü
if ! command -v ffmpeg &> /dev/null && [ ! -f "bin/ffmpeg" ]; then
    echo "[UYARI] Sisteminizde veya bin/ klasöründe 'ffmpeg' bulunamadı!"
    echo "        Video format dönüştürme ve yüksek kalite birleştirmeler için ffmpeg gereklidir."
    echo "        Kurmak için: apt install -y ffmpeg (Ubuntu/Debian) veya pacman -S ffmpeg (Arch)"
    echo ""
fi

# 5. Uygulamayı Başlat
echo "[INFO] Uygulama çalıştırılıyor (python3 src/main.py)..."
echo ""

python3 src/main.py
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -ne 0 ]; then
    echo "[HATA] Uygulama çalıştırılırken bir hata oluştu (Hata Kodu: $EXIT_CODE)."
    echo ""
    echo "--- LİNUX ÇÖZÜM REHBERİ ---"
    echo "Eğer yukarıda 'No module named gi' veya 'GTK cannot be loaded' hatası aldıysanız:"
    echo "1. Terminalde şu komutu çalıştırıp GTK arayüz kütüphanelerini kurun:"
    echo "   apt update && apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 libwebkit2gtk-4.1-0"
    echo ""
    echo "2. Not: Eğer bir VPS (Cloud Sunucu) üzerinde SSH üzerinden çalıştırıyorsanız,"
    echo "   sunucularda masaüstü ekranı (GUI) olmadığı için arayüz açılamaz."
else
    echo "[INFO] Uygulama normal şekilde kapandı."
fi

echo ""
read -p "Çıkmak için Enter tuşuna basın..."
