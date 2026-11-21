@echo off
chcp 65001 >nul
title Suylios Downloader - Build
cd /d "%~dp0"

echo ==================================================
echo      SUYLIOS DOWNLOADER v2.0 BUILD ARACI
echo ==================================================
echo.
echo   1 - Tam Build (Portable ZIP + Setup.exe + Suylios.exe)
echo   2 - Taşınabilir ZIP (Suylios-Portable.zip)
echo   3 - Kurulum Sihirbazı (Suylios-Setup.exe)
echo   4 - Tek Dosya EXE (Suylios.exe)
echo   5 - Bağımlılıkları Güncelle (pip upgrade)
echo   6 - Sadece Rust Hibrit Motoru Derle (bin\suylios-engine.exe)
echo   7 - Temizle (build/ ve dist/ sil)
echo   0 - Çıkış
echo.
set /p choice="Seçiminiz (0-7): "

if not exist "build\logs" mkdir "build\logs"

if "%choice%"=="1" (
    python build.py all
    echo [LOG] Detayli ciktilar build\logs\ klasorune kaydedildi.
    pause
) else if "%choice%"=="2" (
    python build.py portable
    pause
) else if "%choice%"=="3" (
    python build.py setup
    pause
) else if "%choice%"=="4" (
    python build.py onefile
    pause
) else if "%choice%"=="5" (
    python build.py deps
    pause
) else if "%choice%"=="6" (
    if exist "rust\Cargo.toml" (
        if not exist "bin" mkdir bin
        cargo build --manifest-path rust\Cargo.toml --release -p suylios-engine
        if exist "rust\target\release\suylios-engine.exe" (
            copy /y "rust\target\release\suylios-engine.exe" "bin\suylios-engine.exe"
            echo [OK] bin\suylios-engine.exe hazir.
        )
    ) else (
        echo [HATA] rust\Cargo.toml bulunamadi!
    )
    pause
) else if "%choice%"=="7" (
    python build.py clean
    pause
) else if "%choice%"=="0" (
    exit /b 0
) else (
    echo Geçersiz seçim!
    pause
)
