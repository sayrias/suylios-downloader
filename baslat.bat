@echo off
chcp 65001 >nul
title Suylios Downloader
cd /d "%~dp0"

echo =========================================================
echo        Suylios Downloader v2.0 - Windows Baslatici
echo =========================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [HATA] Python bulunamadi!
    echo Lutfen Python 3.11+ yukleyin: https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Sanal ortam ^(venv^) olusturuluyor...
    python -m venv venv
)

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

if not exist "hotfixes\dist\hotfix-manifest.json" (
    if exist "hotfixes\tools\build_and_sign.py" (
        python hotfixes\tools\build_and_sign.py --out hotfixes\dist >nul 2>&1
    )
)

if not exist "bin\suylios-engine.exe" (
    where cargo >nul 2>&1
    if %errorlevel% equ 0 (
        if exist "rust\Cargo.toml" (
            echo [INFO] Rust Hibrit Motoru ^(suylios-engine.exe^) derleniyor...
            if not exist "bin" mkdir bin
            cargo build --manifest-path rust\Cargo.toml --release -p suylios-engine
            if exist "rust\target\release\suylios-engine.exe" (
                copy /y "rust\target\release\suylios-engine.exe" "bin\suylios-engine.exe" >nul
                echo [INFO] Rust Hibrit Motoru hazir: bin\suylios-engine.exe
            )
        )
    )
)

echo [INFO] Suylios Downloader baslatiliyor...
python src\main.py

if %errorlevel% neq 0 (
    echo.
    echo [HATA] Uygulama bir hata ile kapandi.
    echo Bagimliliklari yuklemek icin: pip install -r requirements.txt
    pause
)
