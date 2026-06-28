@echo off
title Suylios Tek Dosya Derleyici
cd /d "%~dp0"

echo ===================================================
echo   Suylios Downloader - Tek Dosya (.exe) Derleyici
echo ===================================================
echo.

if exist "venv\Scripts\python.exe" (
    echo [*] Sanal ortam (venv) kullaniliyor...
    "venv\Scripts\python.exe" build_tekdosya.py
) else (
    echo [*] Sistem Python kullaniliyor...
    python build_tekdosya.py
)

echo.
echo Derleme tamamlandi. Cikmak icin bir tusa basin...
pause >nul
