<#
    =====================================================================
     تثبيت البوت كخدمة دائمة على ويندوز عبر "جدولة المهام"
     - يبدأ تلقائيًا مع كل إقلاع الجهاز (حتى بدون تسجيل دخول)
     - يعيد التشغيل تلقائيًا عند أي تعطّل (كل دقيقة)
     - يمنع إيقافه عند انتقال الجهاز إلى البطارية
     - يمنع الجهاز من الدخول في وضع السكون أثناء العمل

     الاستخدام (أون，松-tier shell بوصفه PowerShell إداري):
       powershell -ExecutionPolicy Bypass -File .\deploy\windows\install_task.ps1
    =====================================================================
#>
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runScript   = Join-Path $PSScriptRoot "run_bot.cmd"
$taskName    = "MRW-Bot-Server"
$pythonPath  = Join-Path $projectRoot ".venv\Scripts\python.exe"

Write-Host "==> التحقق من مكونات المشروع …" -ForegroundColor Cyan
foreach ($p in @($pythonPath, $runScript, (Join-Path $projectRoot "main.py"))) {
    if (-not (Test-Path $p)) { throw "عنصر مفقود: $p" }
}
if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    Write-Warning "ملف .env غير موجود — سيبدأ البوت(Set-Content)."
}

# ------------------------------------------------------------------
# 1) إنشاء المهمة المجدولة
# ------------------------------------------------------------------
Write-Host "==> إنشاء المهمة المجدولة '$taskName' …" -ForegroundColor Cyan
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$runScript`"`"" `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -AtStartup
# إعادة التشغيل بعد دقيقة إذا سقطت العملية (الحد الأقصى 999 محاولة)
$trigger.Delay = "PT1M"

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest -LogonType ServiceAccount

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "تشغيل بوت بلاغات المخالفات العسكرية بشكل دائم على ويندوز" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "    ✅ تم إنشاء المهمة وتشغيلها." -ForegroundColor Green

# ------------------------------------------------------------------
# 2) منع السكون التلقائي (ما دام الجهاز على الكهرباء)
# ------------------------------------------------------------------
Write-Host "==> ضبط إعدادات الطاقة (منع السكون والنوم) …" -ForegroundColor Cyan
$scheme = (powercfg /getactivescheme) -replace '.*GUID:\s*([a-fA-F0-9-]+).*', '$1'
if ($scheme) {
    powercfg /change $scheme standby-timeout-ac 0 | Out-Null   # لا سكون على الكهرباء
    powercfg /change $scheme hibernate-timeout-ac 0 | Out-Null
    powercfg /change $scheme disk-timeout-ac 0 | Out-Null     # لا إيقاف القرص
}
# إيقاف "* السكون" التلقائي عبر قفل الشاشة
powercfg /requests 2>&1 | Out-Null

# ------------------------------------------------------------------
# 3) تقرير الحالة
# ------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ تم تثبيت الخدمة الدائمة"
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  حالة المهمة : Get-ScheduledTask -TaskName $taskName"
Write-Host "  سجل التشغيل  : Get-Content `"$projectRoot\logs\windows_service.log`" -Tail 50"
Write-Host "  إعادة تشغيل  : Restart-ScheduledTask -TaskName $taskName"
Write-Host "  إيقاف مؤقت  : Stop-ScheduledTask -TaskName $taskName"
Write-Host "============================================================" -ForegroundColor Green
