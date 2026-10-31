# ============================================================
#  بناء لوحة الإدارة (Flutter Web) ونشرها في admin_web/dist
#  الاستخدام:  .\build_release.ps1
#  متغير اختياري: $env:API_BASE = "https://..." لتثبيت عنوان API
#  (الافتراضي: نفس منبع الصفحة — وهو الأنسب على Render)
# ============================================================
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
Set-Location $root

$buildTime = [DateTime]::UtcNow
$versionBase = '2.7.' + $buildTime.ToString(
    'yyyyMMdd',
    [Globalization.CultureInfo]::InvariantCulture
)
$version = $versionBase
$releaseDate = $buildTime.ToString(
    'yyyy-MM-dd',
    [Globalization.CultureInfo]::InvariantCulture
)
$distDir = Join-Path $root 'dist'
$versionFile = Join-Path $distDir 'version.json'
if (Test-Path $versionFile) {
    try {
        $previousVersion = (
            Get-Content -Path $versionFile -Raw | ConvertFrom-Json
        ).version
        $sameDayVersion = [regex]::Match(
            [string]$previousVersion,
            ('^' + [regex]::Escape($versionBase) + '(?:\.(\d+))?$')
        )
        if ($sameDayVersion.Success) {
            $sequence = 0
            if ($sameDayVersion.Groups[1].Success) {
                $sequence = [int]$sameDayVersion.Groups[1].Value
            }
            $version = "$versionBase.$($sequence + 1)"
        }
    } catch {
        Write-Warning "تعذر قراءة رقم الإصدار السابق؛ سيُستخدم رقم تاريخ اليوم."
    }
}
$apiBase = if ($env:API_BASE) { $env:API_BASE.TrimEnd('/') } else { '' }

Write-Host "==> الإصدار: $version"
if ($apiBase) { Write-Host "==> عنوان API: $apiBase" }
else { Write-Host "==> عنوان API: نفس منبع الصفحة" }

flutter pub get
if ($LASTEXITCODE -ne 0) { throw 'flutter pub get فشل' }

$buildArgs = @(
    'build', 'web', '--release',
    '--base-href=/admin/',
    '--pwa-strategy=none',
    "--dart-define=APP_VERSION=$version"
)
if ($apiBase) { $buildArgs += "--dart-define=ADMIN_API_BASE_URL=$apiBase" }

flutter @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'flutter build web فشل' }

$buildDir = Join-Path $root 'build\web'
if (-not (Test-Path $buildDir)) { throw "لم يُعثر على مخرجات البناء: $buildDir" }

if (Test-Path $distDir) {
    Get-ChildItem $distDir -Force | Remove-Item -Recurse -Force
} else {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}
Get-ChildItem $buildDir -Force | Copy-Item -Destination $distDir -Recurse -Force

# ملف الإصدار المرافق للوحة
$meta = [ordered]@{
    version      = $version
    release_date = $releaseDate
    author       = 'ABU-AZZAM-COM / Azzam'
    github       = 'https://github.com/ABU-AZZAM-COM/mrw-bot'
    copyright    = '© 2026 — جميع الحقوق محفوظة'
}
$json = $meta | ConvertTo-Json
$json | Set-Content -Path (Join-Path $distDir 'version.json') -Encoding UTF8
$json | Set-Content -Path (Join-Path $root 'lib\version.json') -Encoding UTF8

Write-Host "✅ تم البناء والنشر في: $distDir"
Write-Host "   اللوحة على Render: https://mrw-bot.onrender.com/admin/"
