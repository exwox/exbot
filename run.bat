@echo off
title EXBOT - DCA Bot (Python + Node.js)
chcp 65001 >nul
cd /d "%~dp0"
echo ==================================================
echo [ROBOT] EXBOT DCA BOT - PYTHON + NODE.JS
echo ==================================================
echo.

:: Cek apakah perintah python terdaftar di PATH
where python >nul 2>nul
if %errorlevel% equ 0 (
    set PYTHON_CMD=python
) else (
    if exist "C:\Users\elang\AppData\Local\Microsoft\WindowsApps\python.exe" (
        set PYTHON_CMD="C:\Users\elang\AppData\Local\Microsoft\WindowsApps\python.exe"
    ) else (
        echo [ERROR] Python tidak ditemukan di sistem.
        pause
        exit /b 1
    )
)

:: Cek apakah Node.js terinstal
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Node.js tidak ditemukan di sistem.
    echo [INFO] Install Node.js dari https://nodejs.org/
    pause
    exit /b 1
)

:: Cek apakah config.py ada
if not exist config.py (
    echo [ERROR] File config.py tidak ditemukan!
    echo [INFO] Salin config.py.example ke config.py dan edit API keys
    pause
    exit /b 1
)

:: Cek apakah dependensi python sudah terinstal
%PYTHON_CMD% -c "import requests, dotenv, cryptography" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [INSTALL] Menginstal dependensi Python dari requirements.txt...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Gagal menginstal dependensi Python.
        pause
        exit /b 1
    )
    echo [OK] Dependensi Python berhasil diinstal!
    echo.
)

:: Cek apakah .env sudah ada
if not exist .env (
    echo [WARN] File .env belum ada. Menyalin dari .env.example...
    copy .env.example .env >nul
    echo.
    echo [WARN] Edit file .env dan set ENCRYPTION_KEY terlebih dahulu!
    echo [WARN] Generate key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    echo.
    pause
)

:: Setup database jika belum pernah
if not exist data\dca_bot.db (
    echo [SETUP] Menjalankan setup database...
    %PYTHON_CMD% app.py --setup
    if %errorlevel% neq 0 (
        echo [ERROR] Setup database gagal.
        pause
        exit /b 1
    )
    echo [OK] Setup database selesai.
    echo.
)

:: Cek apakah node_modules terinstal
if not exist node_modules (
    echo [INSTALL] Menginstal dependensi Node.js...
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Gagal menginstal dependensi Node.js
        pause
        exit /b 1
    )
    echo [OK] Dependensi Node.js berhasil diinstal!
    echo.
)

set PYTHONIOENCODING=utf-8
echo [START] Menjalankan EXBOT (Python Bot + Node.js Dashboard)...
echo.
echo [INFO] Komponen yang akan dijalankan:
echo   - Python Bot Manager (tanpa Flask dashboard)
echo   - Node.js Dashboard (port 5000)
echo.

:: Jalankan Python bot manager di window terpisah (tanpa dashboard)
echo [START] Memulai Python Bot Manager...
start "EXBOT Python Bot" cmd /c "%PYTHON_CMD% app.py --no-dashboard"

:: Tunggu sebentar agar bot siap
timeout /t 5 /nobreak >nul

:: Jalankan Node.js dashboard di window terpisah
echo [START] Memulai Node.js Dashboard...
start "EXBOT Node.js Dashboard" cmd /c "node dashboard.js"

echo.
echo [OK] Semua komponen berhasil dijalankan!
echo.
echo [WEB] Node.js Dashboard: http://localhost:5000
echo [BOT] Python Bot Manager berjalan di background
echo.
echo [INFO] Tekan Ctrl+C di window masing-masing untuk menghentikan
echo [INFO] Atau tutup window ini untuk menghentikan semua komponen
echo.
pause
