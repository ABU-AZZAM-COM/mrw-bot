@echo off
rem لوحة إدارة بلاغات المخالفات العسكرية — تشغيل من بيئة المشروع
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [خطأ] لم أجد بيئة المشروع .venv - أنشئها أولاً:
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r desktop_panel\requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "desktop_panel\panel.py"
