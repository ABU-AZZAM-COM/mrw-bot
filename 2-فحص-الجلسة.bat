@echo off
chcp 65001 >nul
title فحص الجلسة المحلية
cd /d "%~dp0"
".venv\Scripts\python.exe" tg_check.py
echo.
pause >nul
