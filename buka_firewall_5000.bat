@echo off
echo ============================================================
echo  Exbot - Membuka Firewall Port 5000 (TCP)
echo ============================================================
echo.

REM Tambah rule firewall TCP inbound port 5000
netsh advfirewall firewall add rule name="Exbot Dashboard TCP 5000" dir=in action=allow protocol=TCP localport=5000 profile=any

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo [OK] Rule firewall berhasil ditambahkan!
) ELSE (
    echo.
    echo [ERROR] Gagal menambahkan rule. Pastikan jalankan sebagai Administrator.
    pause
    exit /b 1
)

REM Verifikasi rule sudah masuk
echo.
echo Verifikasi rule:
netsh advfirewall firewall show rule name="Exbot Dashboard TCP 5000"

echo.
echo ============================================================
echo  IP LAN Anda (untuk akses dari jaringan lokal yang sama):
echo ============================================================
ipconfig | findstr /i "IPv4"

echo.
echo ============================================================
echo  LANGKAH SELANJUTNYA untuk akses dari INTERNET (IP Publik):
echo ============================================================
echo  1. Buka router: http://192.168.1.1
echo  2. Login ke admin router
echo  3. Cari menu: Port Forwarding / Virtual Server / NAT
echo  4. Tambahkan rule:
echo     - External Port : 5000
echo     - Internal IP   : 192.168.1.9
echo     - Internal Port : 5000
echo     - Protocol      : TCP
echo  5. Cek IP Publik di: https://whatismyip.com
echo  6. Akses dashboard: http://[IP-PUBLIK]:5000
echo ============================================================
echo.
pause
