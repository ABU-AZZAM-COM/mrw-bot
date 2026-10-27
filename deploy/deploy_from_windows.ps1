<#
    =====================================================================
     نشر نظام بلاغات المخالفات العسكرية إلى خادم Oracle Cloud
     يعمل من ويندوز بأمر واحد — ينسخ مجلد المشروع (بدون .venv
     وبدون جلسة الحساب الشخصي) ويرفعه ثم يشغّل سكربت التثبيت.

     الاستخدام:
       .\deploy_from_windows.ps1 -ServerIP 130.61.xxx.xxx -KeyPath "$HOME\Downloads\mrw-key.pem"
    =====================================================================
#>
param(
    [Parameter(Mandatory = $true)][string]$ServerIP,
    [Parameter(Mandatory = $true)][string]$KeyPath,
    [string]$RemoteUser = "ubuntu",
    [string]$RemoteDir = "/opt/mrw-bot"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $env:TEMP "mrw-deploy-$stamp"

Write-Host "==> تجهيز حزمة النشر من: $projectRoot" -ForegroundColor Cyan

# 1) نسخ ملفات المشروع (مع استثناء ما لا يُرفع)
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$excludeDirs = @(".venv", "logs", "exports", "__pycache__", ".git", "media")
$excludeFiles = @("user_session.session", "user_session.session-journal", "*.pyc")

robocopy $projectRoot $stage /E /XD @excludeDirs /XF @excludeFiles /NFL /NDL /NJH /NJS /NP | Out-Null

# 2) التأكد من وجود ملفات أساسية
foreach ($f in @(
    "main.py",
    "config.py",
    "handlers.py",
    "admin.py",
    "database\__init__.py",
    "app\main.py",
    "app\space_app.py",
    "bot\handlers.py",
    "api\admin_api.py",
    "services\archive.py",
    "requirements.txt"
)) {
    if (-not (Test-Path (Join-Path $stage $f))) { throw "ملف أساسي مفقود: $f" }
}

# 3) ضغط الحزمة
$tar = Join-Path $env:TEMP "mrw-deploy-$stamp.tar.gz"
tar -czf $tar -C $stage .
Write-Host "==> الحزمة جاهزة: $tar" -ForegroundColor Green

# 4) الاتصال بالخادم وإنشاء المجلد
Write-Host "==> الاتصال بالخادم $ServerIP …" -ForegroundColor Cyan
ssh -i $KeyPath -o StrictHostKeyChecking=accept-new "$RemoteUser@$ServerIP" "mkdir -p $RemoteDir && echo 'الاتصال OK'"

# 5) رفع الحزمة
Write-Host "==> رفع الملفات (قد يستغرق دقيقة) …" -ForegroundColor Cyan
scp -i $KeyPath $tar "$RemoteUser@$ServerIP`:/tmp/mrw-deploy.tar.gz"
ssh -i $KeyPath "$RemoteUser@$ServerIP" "cd $RemoteDir && tar -xzf /tmp/mrw-deploy.tar.gz && rm -f /tmp/mrw-deploy.tar.gz && echo 'تم الرفع'"

# 6) تشغيل سكربت التثبيت
Write-Host "==> تشغيل سكربت التثبيت على الخادم …" -ForegroundColor Cyan
ssh -i $KeyPath -t "$RemoteUser@$ServerIP" "cd $RemoteDir && sudo bash deploy/install.sh"

# 7) تنظيف الملفات المؤقتة
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
Remove-Item -Force $tar -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ اكتمل النشر على الخادم"
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  1) أدخل القيم في ملف .env على الخادم:"
Write-Host "     ssh -i $KeyPath $RemoteUser@$ServerIP"
Write-Host "     nano $RemoteDir/.env"
Write-Host "  2) ثم شغّل البوت:"
Write-Host "     sudo systemctl restart mrw-bot"
Write-Host "  3) تابع السجلات:"
Write-Host "     journalctl -u mrw-bot -f"
Write-Host "============================================================" -ForegroundColor Green
