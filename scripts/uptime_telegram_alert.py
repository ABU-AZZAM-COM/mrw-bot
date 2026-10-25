#!/usr/bin/env python3
"""جسر تنبيهات UptimeRobot إلى تلغرام.

يعمل من GitHub Actions كل 5 دقائق (مستقلًا عن Render، فيصل التنبيه حتى لو
توقّفت الخدمة نفسها):

1) يقرأ حالة كل المراقبات من UptimeRobot API.
2) يرسل رسالة تلغرام فقط عند تغيّر الحالة (توقف/عودة) أو تذكيرًا دوريًا
   أثناء استمرار التوقف.
3) يتجاهل أول محاولة فاشلة (Seems Down) وينتظر تأكيد التوقف في الفحص
   التالي، لتقليل الإنذارات الكاذبة.
4) يحفظ آخر حالة معروفة في .github/uptime-state.json لمنع تكرار التنبيهات.

المتغيرات المطلوبة:
    UPTIMEROBOT_API_KEY   مفتاح UptimeRobot الرئيسي
    TELEGRAM_BOT_TOKEN    توكن البوت الذي يرسل التنبيه
    TELEGRAM_CHAT_ID      معرّف المحادثة المستلمة

متغيرات اختيارية:
    TEST_MODE=1           إرسال رسالة اختبار فقط دون تعديل الحالة
    LOCAL_UTC_OFFSET=3    فرق التوقيت المحلي عن UTC (افتراضي 3)
    REMINDER_MINUTES=60   إعادة التنبيه كل كم دقيقة أثناء التوقف
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://api.uptimerobot.com/v2/getMonitors"
ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / ".github" / "uptime-state.json"

UTC_OFFSET = int(os.getenv("LOCAL_UTC_OFFSET", "3"))
LOCAL_TZ = timezone(timedelta(hours=UTC_OFFSET))
REMINDER_MINUTES = int(os.getenv("REMINDER_MINUTES", "60"))

DOWN_STATUSES = {8, 9}
STATUS_TEXT = {
    0: "متوقف مؤقتًا (Paused)",
    1: "لم يُفحص بعد (Not Checked)",
    2: "يعمل (Up)",
    8: "تعذّر الوصول (Seems Down)",
    9: "متوقف (Down)",
}
STATUS_ICON = {8: "🟠", 9: "🔴"}


def log(message: str) -> None:
    print(message, flush=True)


def http_post(url: str, data: dict[str, str], timeout: int = 30) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = http_post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
    )
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")


def fmt_time(timestamp: float | None = None) -> str:
    moment = datetime.fromtimestamp(timestamp or time.time(), tz=LOCAL_TZ)
    return moment.strftime("%Y-%m-%d %H:%M") + f" (UTC+{UTC_OFFSET})"


def fmt_duration(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f"{minutes} دقيقة"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ساعة و{minutes} دقيقة"
    days, hours = divmod(hours, 24)
    return f"{days} يوم و{hours} ساعة"


def monitor_header(monitor: dict) -> str:
    name = html.escape(str(monitor.get("friendly_name") or monitor.get("id")))
    url = html.escape(str(monitor.get("url") or ""))
    return f"<b>{name}</b>\n{url}"


def down_message(monitor: dict, status: int, down_since: float | None) -> str:
    icon = STATUS_ICON.get(status, "🔴")
    lines = [
        f"{icon} <b>تنبيه: توقف الخدمة</b>",
        "",
        monitor_header(monitor),
        f"<b>الحالة:</b> {STATUS_TEXT.get(status, status)}",
        f"<b>الوقت:</b> {fmt_time()}",
    ]
    if down_since:
        lines.append(f"<b>مدة التوقف:</b> {fmt_duration(time.time() - down_since)}")
    lines += ["", "<i>UptimeRobot ← mrw-bot</i>"]
    return "\n".join(lines)


def up_message(monitor: dict, downtime: float | None) -> str:
    lines = [
        "🟢 <b>عادت الخدمة للعمل</b>",
        "",
        monitor_header(monitor),
        f"<b>الحالة:</b> {STATUS_TEXT[2]}",
    ]
    if downtime:
        lines.append(f"<b>مدة التوقف:</b> {fmt_duration(downtime)}")
    lines += [f"<b>الوقت:</b> {fmt_time()}", "", "<i>UptimeRobot ← mrw-bot</i>"]
    return "\n".join(lines)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"monitors": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"تعذّر قراءة ملف الحالة ({exc}) — سيُعاد إنشاؤه")
        return {"monitors": {}}
    monitors = data.get("monitors")
    if not isinstance(monitors, dict):
        return {"monitors": {}}
    return {"monitors": monitors}


def save_state(state: dict) -> bool:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if STATE_PATH.exists() and STATE_PATH.read_text(encoding="utf-8") == text:
        return False
    STATE_PATH.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    api_key = os.getenv("UPTIMEROBOT_API_KEY", "").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    test_mode = os.getenv("TEST_MODE", "").strip().lower() in {"1", "true", "yes"}

    missing = [
        name
        for name, value in (
            ("UPTIMEROBOT_API_KEY", api_key),
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        log("متغيرات ناقصة: " + ", ".join(missing))
        return 2

    if test_mode:
        send_telegram(
            token,
            chat_id,
            "✅ <b>اختبار جسر التنبيهات</b>\n\n"
            "ربط UptimeRobot ← تلغرام يعمل بنجاح.\n"
            f"<b>الوقت:</b> {fmt_time()}",
        )
        log("تم إرسال رسالة الاختبار بنجاح")
        return 0

    payload = http_post(
        API_URL,
        {
            "api_key": api_key,
            "format": "json",
            "logs": "0",
            "response_times": "0",
            "alert_contacts": "0",
        },
    )
    if payload.get("stat") != "ok":
        log(f"فشل استدعاء UptimeRobot: {payload}")
        return 1

    monitors = payload.get("monitors") or []
    state = load_state()
    known: dict = state["monitors"]
    seen: set[str] = set()
    messages: list[str] = []
    now = time.time()

    for monitor in monitors:
        monitor_id = str(monitor.get("id"))
        seen.add(monitor_id)
        try:
            status = int(monitor.get("status", 1))
        except (TypeError, ValueError):
            continue
        if status in (0, 1):  # موقوف مؤقتًا أو لم يُفحص بعد
            continue

        entry = known.get(monitor_id) or {}
        prev_status = entry.get("status")
        down_since = entry.get("down_since")
        last_alert = entry.get("last_alert")
        alerted = bool(entry.get("alerted"))

        if status in DOWN_STATUSES:
            if prev_status is None:
                # أول تشغيل: نسجّل الحالة دون تنبيه لتجنّب ضجيج التهيئة.
                down_since = now
            elif prev_status not in DOWN_STATUSES:
                # دخل حالة التوقف الآن.
                down_since = now
                if status == 9:
                    alerted = True
                    last_alert = now
                    messages.append(down_message(monitor, status, None))
                else:
                    # محاولة فاشلة أولى: ننتظر تأكيد التوقف في الفحص القادم.
                    alerted = False
            elif not alerted:
                # تأكّد التوقف بعد محاولة فاشلة سابقة.
                alerted = True
                last_alert = now
                messages.append(down_message(monitor, status, down_since))
            elif last_alert and now - float(last_alert) >= REMINDER_MINUTES * 60:
                last_alert = now
                messages.append(down_message(monitor, status, down_since))
        elif status == 2:
            if prev_status in DOWN_STATUSES and alerted:
                downtime = now - float(down_since) if down_since else None
                messages.append(up_message(monitor, downtime))
            down_since = None
            last_alert = None
            alerted = False

        entry_core = {
            "name": monitor.get("friendly_name") or monitor_id,
            "url": monitor.get("url") or "",
            "status": status,
            "down_since": down_since,
            "last_alert": last_alert,
            "alerted": alerted,
        }
        # لا نغيّر updated_at إن لم تتغيّر الحالة فعليًا، حتى لا يُنشأ commit كل تشغيل.
        previous_core = {key: entry.get(key) for key in entry_core}
        if previous_core == entry_core and entry.get("updated_at"):
            updated_at = entry["updated_at"]
        else:
            updated_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
        known[monitor_id] = {**entry_core, "updated_at": updated_at}

    for removed in [mid for mid in known if mid not in seen]:
        del known[removed]

    for text in messages:
        send_telegram(token, chat_id, text)
        log("تم إرسال تنبيه: " + text.splitlines()[0])

    changed = save_state(state)
    log(
        f"المراقبات: {len(monitors)} | التنبيهات: {len(messages)} | "
        f"تغيّرت الحالة: {changed}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
