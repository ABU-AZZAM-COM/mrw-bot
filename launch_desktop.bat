@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "WEB_DIR=%~dp0admin_web\build\web"
set "API_URL=https://mrw-bot.onrender.com"
set "FLUTTER_CMD="
set "PYTHONW_CMD="

for /f "delims=" %%F in ('where flutter.bat 2^>nul') do if not defined FLUTTER_CMD set "FLUTTER_CMD=%%F"
if not defined FLUTTER_CMD (
    echo [خطأ] Flutter غير موجود في PATH. ثبّت Flutter ثم أعد تشغيل هذا الملف.
    pause
    exit /b 1
)
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYTHONW_CMD set "PYTHONW_CMD=%%P"
if not defined PYTHONW_CMD (
    echo [خطأ] لم أجد pythonw.exe. ثبّت Python 3 ثم أعد المحاولة.
    pause
    exit /b 1
)

echo [1/3] بناء لوحة الإدارة للاتصال بالبوت على Render...
pushd admin_web
call "%FLUTTER_CMD%" build web --release --base-href=/ --dart-define=ADMIN_API_BASE_URL=%API_URL%
if errorlevel 1 (
    popd
    echo [خطأ] فشل بناء لوحة الإدارة.
    pause
    exit /b 1
)
popd

echo [2/3] تشغيل خادم ملفات محلي فقط على 127.0.0.1:9000...
start "" /min "%PYTHONW_CMD%" -m http.server 9000 --bind 127.0.0.1 --directory "%WEB_DIR%"

echo [3/3] فتح لوحة الإدارة...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:9000/"

echo.
echo تعمل واجهة اللوحة محليًا، بينما تبقى API والبوت على خدمة Render.
echo لا تغلق نافذة خادم الملفات إذا أردت إبقاء اللوحة متاحة.
endlocal
