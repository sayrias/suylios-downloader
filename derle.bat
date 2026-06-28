@echo off
title Suylios Derleme ve Paketleme Ussu
cd /d "%~dp0"

:MENU
cls
echo =========================================================
echo    Suylios Downloader - Derleme ve Paketleme Ussu
echo =========================================================
echo.
echo   1 - Tasinabilir ZIP Paketi Derle (Suylios-Portable.zip)
echo   2 - Kurulum Sihirbazi Derle (Suylios-Setup.exe)
echo   3 - Tek Dosya Standalone Derle (Suylios.exe)
echo   4 - Hepsini Sirayla Derle (Tumunu Uret)
echo   0 - Cikis
echo.
echo =========================================================
set /p secim="Seciminiz (0-4): "

set PY_CMD=python
if exist "venv\Scripts\python.exe" set PY_CMD="venv\Scripts\python.exe"

if "%secim%"=="1" goto BUILD_PORTABLE
if "%secim%"=="2" goto BUILD_SETUP
if "%secim%"=="3" goto BUILD_TEKDOSYA
if "%secim%"=="4" goto BUILD_ALL
if "%secim%"=="0" goto EXIT
goto MENU

:BUILD_PORTABLE
cls
%PY_CMD% build.py portable
echo.
echo Islem tamamlandi! Devam etmek icin bir tusa basin...
pause >nul
goto MENU

:BUILD_SETUP
cls
%PY_CMD% build.py setup
echo.
echo Islem tamamlandi! Devam etmek icin bir tusa basin...
pause >nul
goto MENU

:BUILD_TEKDOSYA
cls
%PY_CMD% build.py onefile
echo.
echo Islem tamamlandi! Devam etmek icin bir tusa basin...
pause >nul
goto MENU

:BUILD_ALL
cls
%PY_CMD% build.py all
echo.
echo Islem tamamlandi! Devam etmek icin bir tusa basin...
pause >nul
goto MENU

:EXIT
exit
