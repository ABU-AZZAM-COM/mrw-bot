@echo off
REM =====================================================================
REM  تشغيل بوت بلاغات المخالفات العسكرية (نسخة ويندوز / خدمة دائمة)
REM  يُستدعى تلقائيًا بواسطة "جدولة المهام" عند كل إقلاع للجهاز
REM =====================================================================
setlocal

REM --- مسار المشروع (يُعدّل تلقائيًا عند التثبيت) ---
set "APP_DIR=%~dp0..\.."

cd /d "%APP_DIR%"

REM --- تحميل الأسرار من ملف .env إن كان موجودًا ---
if exist "%APP_DIR%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%APP_DIR%\.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

REM --- سجل التشغيل ---
if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"

echo [%date% %time%] تشغيل البوت من %APP_DIR%
"%APP_DIR%\.venv\Scripts\python.exe" "%APP_DIR%\main.py" >> "%APP_DIR%\logs\windows_service.log" 2>&1
echo [%date% %time%] توقف البوت (رمز الخروج %ERRORLEVEL%) — يعيد تشغيله المجدول
exit /b %ERRORLEVEL%
