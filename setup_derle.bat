@echo off
title Suylios Setup Derleyici
cd /d "%~dp0"

echo ===================================================
echo   Suylios Downloader - Setup Kurulum Derleyici
echo ===================================================
echo.

if exist "venv\Scripts\activate.bat" (
    echo [INFO] Sanal ortam venv aktif ediliyor...
    call venv\Scripts\activate.bat
) else (
    echo [INFO] Sanal ortam bulunamadi, global python kullaniliyor...
)

echo [INFO] Gereksinimler kontrol ediliyor...
pip install pyinstaller >nul 2>&1

echo.
echo [INFO] Setup derleme basladi...
python build_setup.py

echo.
echo ===================================================
echo   Islem Tamamlandi!
echo ===================================================
pause
