@echo off
title Suylios Derleyici
cd /d "%~dp0"

echo ===================================================
echo   Suylios Downloader - Tek Tikla Derleyici
echo ===================================================
echo.

if exist "venv\Scripts\activate.bat" (
    echo [INFO] Sanal ortam venv aktif ediliyor...
    call venv\Scripts\activate.bat
) else (
    echo [INFO] Sanal ortam bulunamadi, global python kullaniliyor...
)

echo [INFO] Derleme gereksinimleri kontrol ediliyor...
pip install pyinstaller >nul 2>&1

echo.
echo [INFO] Derleme basladi, yaklasik 15 saniye surecek...
python build.py

echo.
echo ===================================================
echo   Islem Tamamlandi! Paket 'dist' klasorunde hazir.
echo ===================================================
pause
