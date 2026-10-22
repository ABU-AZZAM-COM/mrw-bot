"""
أدوات مساعدة مشتركة: تنسيق التوقيت، التحقق من الروابط، الهروب من HTML.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape as _escape

import config

# المنطقة الزمنية المحلية بحسب الإعداد
_LOCAL_TZ = timezone(timedelta(hours=config.LOCAL_UTC_OFFSET))


# ------------------------------------------------------------------
# التوقيت
# ------------------------------------------------------------------
def utc_now_iso() -> str:
    """إرجاع الوقت الحالي (UTC) بنسق ISO-8601 — يُخزّن في قاعدة البيانات."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_local(iso: str | None) -> str:
    """تحويل وقت ISO المخزّن (UTC) إلى نص توقيت محلي مقروء: 2026/10/22 - 14:30"""
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return str(iso)
    local = dt.astimezone(_LOCAL_TZ)
    return local.strftime("%Y/%m/%d - %H:%M")


def local_day_start_iso() -> str:
    """بداية اليوم المحلي الحالي (ساعة 00:00) محولة إلى UTC بنسق ISO.

    تُستخدم لحساب البلاغات المسجلة "اليوم" في حدّ البلاغات اليومي.
    """
    today_00 = datetime.now(_LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return today_00.astimezone(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------
# التحقق من الروابط
# ------------------------------------------------------------------
_URL_RE = re.compile(r"^https?://\S{4,500}$", re.IGNORECASE)


def is_valid_url(text: str) -> bool:
    """التحقق أن النص رابط صحيح يبدأ بـ http/https (يمنع روابط ملفات أو أوامر)."""
    return bool(_URL_RE.match((text or "").strip()))


# ------------------------------------------------------------------
# الهروب من HTML (حماية أساسية عند إرسال نصوص المستخدمين)
# ------------------------------------------------------------------
def esc(text: object, quote: bool = False) -> str:
    """تحويل المحارف الخاصة إلى كيانات HTML حتى لا تنكسر الرسالة أو تُحقن."""
    return _escape(str(text if text is not None else ""), quote=quote)


def link_anchor(url: str) -> str:
    """إرجاع رابط منسق HTML قابل للنقر داخل الرسائل."""
    return f'<a href="{esc(url, quote=True)}">فتح المنشور ↗</a>'


def trunc(text: str, limit: int = 500) -> str:
    """اقتطاع نص طويل مع علامة "…" عند الحاجة."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
