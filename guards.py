"""
طبقة الحماية: الإساءة، الإغراق، البلاغات الكيدية، والتخريب.

كل منطق الحماية في مكان واحد ليكون قابلًا للمراجعة والتدقيق.
لا تحظر هذه الطبقة أي جلسة تيليغرام ولا تلمس إلا ما يلزم لصدّ الإساءة.

المحاور:
  1) حدود المعدّل (لكل مبلّغ وللنظام كله) مع تجميد تصاعدي.
  2) منع الاستقبال خارج المحادثات الخاصة (Recycler ضد الإبلاغ الجماعي).
  3) حد رسائل البوت الصادرة لكل مستخدم (حماية من وسم السبام).
  4) كشف البلاغات المكررة (بصمة الرابط + تشابه النص + بصمة الصورة).
  5) فحص سلامة الرابط (حماية SSRF + نطاقات محجوبة).
  6) فحص مضمون البلاغ (رفض الفراغ والفوضى).
  7) الإيقاف الطارئ للنظام.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import config
import utils

logger = logging.getLogger(__name__)

# معاملات التتبع التي تُحذف قبل مقارنة الروابط (لكشف التكرار بدقة)
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "si", "feature", "ref", "ref_src", "fbref",
}

# حدود خاصة — تُرفض الروابط التي تشير إلى أجهزة أو شبكات داخلية
_PRIVATE_HOST_PATTERNS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "169.254.169.254",   # خدمة بيانات وصفية في السحابة
)

# ==================================================================
# منصات التواصل الاجتماعي المعروفة
# (نطاق النظام: مخالفات مُنشورة على وسائل التواصل الاجتماعي فقط)
# ==================================================================
SOCIAL_PLATFORMS: dict[str, str] = {
    "facebook.com": "فيسبوك",
    "fb.com": "فيسبوك",
    "m.facebook.com": "فيسبوك",
    "instagram.com": "إنستغرام",
    "instagr.am": "إنستغرام",
    "twitter.com": "إكس (تويتر)",
    "x.com": "إكس (تويتر)",
    "tiktok.com": "تيك توك",
    "vm.tiktok.com": "تيك توك",
    "snapchat.com": "سناب شات",
    "youtube.com": "يوتيوب",
    "youtu.be": "يوتيوب",
    "t.me": "تيليغرام",
    "telegram.me": "تيليغرام",
    "wa.me": "واتساب",
    "reddit.com": "ريديت",
    "tumblr.com": "تومblr",
    "pinterest.com": "بنترست",
    "linkedin.com": "لينكدإن",
}


def detect_platform(url: str) -> str:
    """يحدّد اسم منصة التواصل التي نُشر عليها المخالفة (أو "" إن لم تُعرف)."""
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if host in SOCIAL_PLATFORMS:
        return SOCIAL_PLATFORMS[host]
    for domain, name in SOCIAL_PLATFORMS.items():
        if host.endswith("." + domain):
            return name
    return ""


@dataclass
class Verdict:
    """نتيجة فحص واحد: مسموح أو مرفوض، مع سبب ونص جاهز للعرض."""

    allowed: bool
    code: str = "ok"
    message: str = ""
    severity: str = "info"          # info | warn | block
    extra: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return not self.allowed


# ==================================================================
# 1) حدود المعدّل والتجميد
# ==================================================================
def _since_iso(seconds: int) -> str:
    """بداية نافذة زمنية بعينها (UTC ISO)."""
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat(timespec="seconds")


async def check_rate_limits(db, user_id: int) -> Verdict:
    """يفحص حدود المعدّل لمبلّغ واحد، ويجمّده عند التجاوز المتكرر."""
    block = await db.get_block(user_id)
    frozen_until = block.get("frozen_until")
    if frozen_until:
        # هل انتهت مدة التجميد؟
        from datetime import datetime, timezone

        try:
            end = datetime.fromisoformat(frozen_until)
        except ValueError:
            end = None

        if end and end > datetime.now(timezone.utc):
            minutes = int((end - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            return Verdict(
                allowed=False,
                code="frozen",
                severity="block",
                message=(
                    f"⛔ تم إيقاف حسابك مؤقتًا بسبب تكرار المحاولات غير النظامية.\n"
                    f"المدة المتبقية: {minutes} دقيقة.\n"
                    f"إذا كان هذا خطأً تواصل مع المشرف."
                ),
                extra={"frozen_until": frozen_until},
            )
        # انتهت المدة → مسح التجميد
        await db.clear_block(user_id)

    # الحد العام للنظام (كشف الإغراق أو الهجوم)
    global_hour = await db.count_events(_since_iso(3600))
    if global_hour >= config.MAX_REPORTS_PER_HOUR_GLOBAL:
        await db.set_state("emergency", "1")
        await db.audit(None, "emergency_auto", "global",
                       f"تجاوز الحد العام: {global_hour} محاولة/ساعة")
        return Verdict(
            allowed=False,
            code="global_flood",
            severity="block",
            message=(
                "🛑 تم إيقاف الاستقبال مؤقتًا لتجاوز حد الأمان للنظام ككل.\n"
                "تم إبلاغ المشرف، وسيعود الاستقبال تلقائيًا."
            ),
        )

    # حدود المبلّغ: دقيقة ثم ساعة
    in_minute = await db.count_events(_since_iso(60), user_id=user_id)
    if in_minute >= config.MAX_REPORTS_PER_MINUTE:
        await _register_strike(db, user_id, "تجاوز الحد الزمني في الدقيقة")
        return Verdict(
            allowed=False,
            code="minute_limit",
            severity="warn",
            message=(
                "⏱️ يرجى التأنّي — أُرسلت محاولات كثيرة خلال دقيقة واحدة.\n"
                "الحد المسموح: "
                f"{config.MAX_REPORTS_PER_MINUTE} بلاغ/دقيقة."
            ),
        )

    in_hour = await db.count_events(_since_iso(3600), user_id=user_id)
    if in_hour >= config.MAX_REPORTS_PER_HOUR:
        await _register_strike(db, user_id, "تجاوز الحد الساعي")
        return Verdict(
            allowed=False,
            code="hour_limit",
            severity="warn",
            message=(
                "⏳ تجاوزت الحد الساعي للبلاغات.\n"
                f"الحد المسموح: {config.MAX_REPORTS_PER_HOUR} بلاغ/ساعة."
            ),
        )

    return Verdict(allowed=True)


async def _register_strike(db, user_id: int, reason: str) -> int:
    """يسجّل مخالفة ويزيد مدة التجميد تصاعديًا."""
    block = await db.get_block(user_id)
    strikes = int(block.get("strikes") or 0) + 1
    minutes = (
        config.FREEZE_MINUTES_FIRST
        if strikes == 1
        else config.FREEZE_MINUTES_SECOND * min(strikes - 1, 1)
    )
    if strikes > 2:
        minutes = config.FREEZE_MINUTES_SECOND * 6      # أسبوع
    from datetime import datetime, timedelta, timezone

    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    await db.save_block(user_id, strikes, until, reason)
    await db.audit(None, "user_frozen", str(user_id), f"{reason} — تجميد {minutes} دقيقة")
    logger.warning("تجميد المستخدم %s (%s) لمدة %d دقيقة", user_id, reason, minutes)
    return strikes


async def check_daily_limit(db, user_id: int) -> Verdict:
    """الحد اليومي لكل مبلّغ."""
    used = await db.count_since(utils.local_day_start_iso(), user_id=user_id)
    if used >= config.MAX_REPORTS_PER_DAY:
        return Verdict(
            allowed=False,
            code="daily_limit",
            severity="warn",
            message=(
                f"📅 بلغت الحد اليومي ({config.MAX_REPORTS_PER_DAY} بلاغ).\n"
                "يمكنك الإرسال غدًا، أو التواصل مع المشرف إن كان بلاغك مستثنى."
            ),
        )
    return Verdict(allowed=True)


# ==================================================================
# 2) منع الاستقبال خارج المحادثات الخاصة
# ==================================================================
def is_private_chat(chat_type: Optional[str]) -> bool:
    """هل المحادثة خاصة (مستخدم ↔ بوت)؟"""
    return str(chat_type) == "private"


# ==================================================================
# 3) حد رسائل البوت الصادرة (حماية من وسم السبام)
# ==================================================================
class OutgoingLimiter:
    """عدّاد رسائل صادرة لكل مستخدم — يمنع إغراق المستخدم برسائل."""

    def __init__(self, per_minute: int = 6) -> None:
        self._limit = per_minute
        self._hits: dict[int, list[float]] = {}

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(user_id, []) if now - t < 60]
        if len(hits) >= self._limit:
            self._hits[user_id] = hits
            return False
        hits.append(now)
        self._hits[user_id] = hits
        return True

    def reset(self, user_id: int) -> None:
        self._hits.pop(user_id, None)


outgoing_limiter = OutgoingLimiter(config.MAX_BOT_MESSAGES_PER_MINUTE)


# ==================================================================
# 4) كشف التكرار
# ==================================================================
def normalize_link(url: str) -> str:
    """تطبيع الرابط: حذف www ومعاملات التتبع وتوحيد المضيف."""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunparse((parts.scheme.lower(), host, path, "", urlencode(query), ""))


def link_hash(url: str) -> str:
    """بصمة الرابط بعد التطبيع."""
    return hashlib.sha256(normalize_link(url).encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """تطبيع النص العربي: إزالة التشكيل وتوحيد الألف والياء والتاء المربوطة."""
    text = (text or "").strip().lower()
    text = re.sub(r"[ً-ْٰـ]", "", text)      # تشكيل وتطويل
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ؤ", "و", text)
    text = re.sub(r"ئ", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def text_hash(text: str) -> str:
    """بصمة النص بعد التطبيع."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def similarity(a: str, b: str) -> float:
    """تشابه نصي بين 0 و1 (.SequenceMatcher على النص المطبَّع)."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def photo_hash(data: bytes) -> str:
    """بصمة الصورة (sha256 للمحتوى) — تكشف إعادة رفع نفس الملف."""
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()


async def check_duplicate(db, link: str, description: str,
                          photo_hash_value: Optional[str] = None) -> Verdict:
    """يكشف البلاغات المكررة/الكيدية عبر البصمات والتشابه."""
    lh = link_hash(link) if link else None
    th = text_hash(description) if description else None

    # 1) تطابق تام (رابط أو نص أو صورة)
    rows = await db.find_fingerprints(link_hash=lh, text_hash=th, photo_hash=photo_hash_value)
    for row in rows:
        if (lh and row.get("link_hash") == lh) or (th and row.get("text_hash") == th) \
                or (photo_hash_value and row.get("photo_hash") == photo_hash_value):
            return Verdict(
                allowed=True,
                code="exact_duplicate",
                severity="warn",
                message=(
                    f"⚠️ يوجد بلاغ مطابق سابق (رقمه <code>{row.get('report_id')}</code>).\n"
                    "سيُسجَّل بلاغك للمراجعة فقط ولن يُنشر تلقائيًا."
                ),
                extra={"duplicate_of": row.get("report_id")},
            )

    # 2) تشابه نصي مع بلاغات أخيرة
    recent = await db.recent_descriptions(limit=60)
    best_id, best_score = None, 0.0
    for rid, desc, _user in recent:
        score = similarity(description, desc)
        if score > best_score:
            best_id, best_score = rid, score

    if best_score >= config.DUPLICATE_TEXT_SIMILARITY:
        return Verdict(
            allowed=True,
            code="similar_duplicate",
            severity="warn",
            message=(
                f"⚠️ يوجد بلاغ مشابه لـ <code>{best_id}</code> "
                f"(تشابه {int(best_score * 100)}%).\n"
                "سيُسجَّل بلاغك للمراجعة فقط ولن يُنشر تلقائيًا."
            ),
            extra={"duplicate_of": best_id, "similarity": best_score},
        )

    return Verdict(allowed=True)


# ==================================================================
# 5) فحص الرابط
# ==================================================================
def check_link(url: str) -> Verdict:
    """يتأكد أن الرابط آمن وصالح: http/https فقط، بلا عناوين داخلية."""
    if not utils.is_valid_url(url):
        return Verdict(allowed=False, code="bad_url", severity="block",
                       message="❌ الرابط غير صالح. تأكد من أنه يبدأ بـ http أو https.")

    try:
        parts = urlparse(url)
    except ValueError:
        return Verdict(allowed=False, code="bad_url", severity="block",
                       message="❌ الرابط غير صالح.")

    host = (parts.hostname or "").lower()
    if not host:
        return Verdict(allowed=False, code="bad_url", severity="block",
                       message="❌ الرابط لا يحتوي اسم موقع.")

    # نطاقات محجوبة
    for blocked in config.BLOCKED_DOMAINS:
        if host == blocked or host.endswith("." + blocked):
            return Verdict(allowed=False, code="blocked_domain", severity="block",
                           message="❌ هذا النطاق محجوب في النظام.")

    # عناوين داخلية/خاصة (حماية SSRF إن أضفنا لاحقًا جلب الروابط)
    if any(pattern in host for pattern in _PRIVATE_HOST_PATTERNS):
        return Verdict(allowed=False, code="private_host", severity="block",
                       message="❌ الرابط يشير إلى عنوان داخلي غير مسموح.")

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return Verdict(allowed=False, code="private_host", severity="block",
                           message="❌ الرابط يشير إلى عنوان داخلي غير مسموح.")
    except ValueError:
        pass  # ليس عنوان IP — عادي

    platform = detect_platform(url)
    if not platform:
        # رابط من مصدر غير معروف: يُقبل لكن يُوسم للمراجعة
        # (نطاق النظام: مخالفات مُنشورة على وسائل التواصل الاجتماعي)
        return Verdict(
            allowed=True,
            code="unknown_platform",
            severity="warn",
            message=(
                "ℹ️ لم أتمكّن من تحديد منصة التواصل من هذا الرابط.\n"
                "اذكر في الوصف أين نُشر المنشور، وسيُراجع البلاغ للتأكد."
            ),
            extra={"platform": ""},
        )

    return Verdict(allowed=True, extra={"platform": platform})


# ==================================================================
# 6) فحص مضمون البلاغ
# ==================================================================
def check_substance(description: str) -> Verdict:
    """يتأكد أن الوصف يحمل معلومات فعلية (يرفض الفراغ والفراغ شبه الكامل)."""
    text = (description or "").strip()
    if len(text) < config.MIN_DESC_LEN:
        return Verdict(
            allowed=False,
            code="thin_description",
            severity="warn",
            message=(
                "✍️ الوصف قصير جدًا.\n"
                f"اكتب وصفًا أوضح (من {config.MIN_DESC_LEN} حرفًا على الأقل): "
                "ما الذي حدث؟ أين؟ متى؟ وما تفاصيل المخالفة؟"
            ),
        )
    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) < config.MIN_DESC_WORDS:
        return Verdict(
            allowed=False,
            code="thin_description",
            severity="warn",
            message=(
                "✍️ الوصف لا يحتوي معلومات كافية.\n"
                f"اكتب وصفًا أوضح (من {config.MIN_DESC_WORDS} كلمات على الأقل)."
            ),
        )
    return Verdict(allowed=True)


# ==================================================================
# 7) الإيقاف الطارئ
# ==================================================================
async def is_emergency(db) -> bool:
    """هل النظام في وضع الإيقاف الطارئ؟"""
    return (await db.get_state("emergency") or "") == "1"


async def set_emergency(db, on: bool, actor_id: Optional[int] = None,
                        reason: str = "") -> None:
    """يشغّل/يوقف وضع الإيقاف الطارئ (يستدعيه المشرف أو الحماية التلقائية)."""
    await db.set_state("emergency", "1" if on else None)
    await db.audit(actor_id, "emergency_on" if on else "emergency_off", None, reason)
    logger.warning("وضع الإيقاف الطارئ: %s (%s)", "مُفعّل" if on else "معطّل", reason)
