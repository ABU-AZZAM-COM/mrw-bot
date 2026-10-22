#!/usr/bin/env bash
# =====================================================================
#  تثبيت نظام بلاغات المخالفات العسكرية على خادم Ubuntu (Oracle Cloud)
#  التشغيل:  sudo bash install.sh
#  ملاحظة: لا يمسّ هذا السكربت أي ملف خارج مجلد /opt/mrw-bot
# =====================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mrw-bot}"
SERVICE_USER="${SERVICE_USER:-mrw}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ---------------------------------------------------------------- 1
log "1/9) تحديث النظام وتثبيت الحزم الأساسية …"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y software-properties-common ca-certificates curl wget gnupg
add-apt-repository -y universe >/dev/null 2>&1 || true
apt-get update -y
apt-get install -y python3 python3-venv python3-pip python3-dev build-essential \
                    tzdata logrotate unattended-upgrades

# ---------------------------------------------------------------- 2
log "2/9) ضبط المنطقة الزمنية (Asia/Riyadh) …"
timedatectl set-timezone Asia/Riyadh || true

# ---------------------------------------------------------------- 3
log "3/9) اختيار أفضل نسخة بايثون متاحة (3.12 ثم 3.11 ثم 3.10) …"
PY=""
for v in python3.12 python3.11 python3.10 python3; do
  if command -v "$v" >/dev/null 2>&1; then
    ver="$("$v" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    major="${ver%%.*}"; minor="${ver##*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then PY="$v"; break; fi
  fi
done
[ -n "$PY" ] || { echo "❌ لم يُعثر على بايثون 3.10+"; exit 1; }
log "    النسخة المختارة: $PY ($($PY --version))"

# ---------------------------------------------------------------- 4
log "4/9) إنشاء مساحة تبديل (swap) لتفادي انقطاع الخادم عند ضيق الذاكرة …"
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  log "    ✅ تم إنشاء swap بحجم 2GB"
fi

# ---------------------------------------------------------------- 5
log "5/9) إنشاء مستخدم الخدمة $SERVICE_USER …"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$SERVICE_USER"
fi

# ---------------------------------------------------------------- 6
log "6/9) تجهيز البيئة الافتراضية وتثبيت المكتبات …"
cd "$APP_DIR"
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip wheel setuptools >/dev/null
./.venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------- 7
log "7/9) ضبط الصلاحيات والمجلدات …"
mkdir -p data media exports logs
if [ ! -f .env ]; then
  cp -n deploy/bot.env.example .env 2>/dev/null || cp -n "$SRC_DIR/bot.env.example" .env 2>/dev/null || true
  chmod 600 .env
  log "    ⚠️  تم إنشاء .env من القالب — عدّل التوكن والمشرف ثم: systemctl restart mrw-bot"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod 700 "$SERVICE_USER" 2>/dev/null || true

# ---------------------------------------------------------------- 8
log "8/9) تثبيت خدمة systemd وتشغيلها …"
cp "$SRC_DIR/mrw-bot.service" /etc/systemd/system/mrw-bot.service
cp "$SRC_DIR/keepalive.service" /etc/systemd/system/mrw-bot-keepalive.service
cp "$SRC_DIR/keepalive.timer"   /etc/systemd/system/mrw-bot-keepalive.timer
systemctl daemon-reload
systemctl enable mrw-bot.service >/dev/null
systemctl enable mrw-bot-keepalive.timer >/dev/null
systemctl restart mrw-bot-keepalive.timer
systemctl restart mrw-bot.service

# ---------------------------------------------------------------- 9
log "9/9) إعداد جدار الحماية والتحديثات التلقائية …"
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
  log "    ✅ جدار الحماية: مفتوح على SSH فقط"
fi
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

echo
echo "============================================================"
echo "  ✅ اكتمل التثبيت"
echo "============================================================"
echo "  حالة الخدمة : systemctl status mrw-bot"
echo "  السجلات     : journalctl -u mrw-bot -f"
echo "  إعادة تشغيل: systemctl restart mrw-bot"
echo "============================================================"
