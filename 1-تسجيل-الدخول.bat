@echo off
chcp 65001 >nul
title تسجيل الدخول المحلي - الحساب التجريبي
cd /d "%~dp0"

echo ============================================
echo   مرحبًا! هذه نافذة تسجيل الدخول فقط
echo   (الكود الذي يصلك على الهاتف تُكتبه هنا)
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

if not exist data mkdir data
echo windows.bat-start >> data\login_log.txt

".venv\Scripts\python.exe" -c "import telethon" 2>nul
if errorlevel 1 (
    echo جاري تثبيت المكتبات اللازمة... انتظر لحظات
    ".venv\Scripts\pip.exe" install -r requirements.txt --quiet
)

".venv\Scripts\python.exe" tg_login.py
echo.
echo ============================================
echo اضغط أي زر للخروج
echo ============================================
pause >nul
