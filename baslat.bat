@echo off
chcp 65001 >nul 2>&1
title Suylios Downloader
cd /d "%~dp0"

echo [INFO] Suylios Downloader baslatiliyor...

if exist "venv\Scripts\activate.bat" (
    echo [INFO] Sanal ortam venv aktif ediliyor...
    call venv\Scripts\activate.bat
)

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [HATA] Python bulunamadi! Lutfen Python'un kurulu oldugundan ve PATH ortam degiskenine eklendiginden emin olun.
    echo.
    pause
    exit /b 1
)

python src\main.py
if %errorlevel% neq 0 (
    echo.
    echo [HATA] Uygulama calistirilirken bir hata olustu (Hata Kodu: %errorlevel%).
) else (
    echo.
    echo [INFO] Uygulama normal sekilde kapandi.
)
pause
