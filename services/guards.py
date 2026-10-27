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
import html
import ipaddress
import logging
import re
import string
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from app import config
from bot import texts
from services import utils, virustotal

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


BLOCK_MESSAGE_VARIABLES = {
    "frozen": {"minutes"},
    "minute_limit": {"limit"},
    "hour_limit": {"limit"},
    "daily_limit": {"limit"},
    "emergency": set(),
    "manual_block": {"kind", "reason"},
}


def clean_block_messages(value: object) -> dict[str, str]:
    """Validate plain-text user notices and their supported placeholders."""
    defaults = texts.BLOCK_MESSAGES
    if not isinstance(value, dict):
        raise ValueError("bad_block_messages")
    if set(value) - set(defaults):
        raise ValueError("bad_block_message_key")
    cleaned = dict(defaults)
    for key, raw in value.items():
        if not isinstance(raw, str):
            raise ValueError("bad_block_message")
        message = raw.strip()
        if not message or len(message) > 500:
            raise ValueError("bad_block_message")
        try:
            placeholders = set()
            for _literal, name, format_spec, conversion in string.Formatter().parse(message):
                if name is None:
                    continue
                if format_spec or conversion:
                    raise ValueError("bad_block_message_placeholder")
                placeholders.add(name)
        except ValueError as exc:
            raise ValueError("bad_block_message_placeholder") from exc
        if placeholders - BLOCK_MESSAGE_VARIABLES[key]:
            raise ValueError("bad_block_message_placeholder")
        cleaned[key] = message
    return cleaned


async def get_block_messages(db) -> dict[str, str]:
    """Return saved templates over safe defaults."""
    saved = await db.get_runtime_settings()
    value = saved.get("BLOCK_MESSAGES")
    if not isinstance(value, dict):
        return dict(texts.BLOCK_MESSAGES)
    return {
        key: message if isinstance(message, str) and 0 < len(message) <= 500 else default
        for key, default in texts.BLOCK_MESSAGES.items()
        for message in [value.get(key, default)]
    }


async def render_block_message(db, key: str, values: Optional[dict[str, object]] = None) -> str:
    """Render a configured plain-text notice, escaping it for HTML parse mode."""
    template = (await get_block_messages(db)).get(key, texts.BLOCK_MESSAGES["emergency"])
    rendered = html.escape(template)
    for name, value in (values or {}).items():
        rendered = rendered.replace("{" + name + "}", html.escape(str(value)))
    return rendered


async def _timed_pause_until(db, state_key: str, expired_action: str) -> Optional[str]:
    value = await db.get_state(state_key)
    if not value:
        return None
    from datetime import datetime, timezone

    try:
        until = datetime.fromisoformat(value)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("قيمة انتهاء الإيقاف المؤقت غير صالحة؛ تُمسح (%s)", state_key)
        await db.set_state(state_key, None)
        return None
    if until <= datetime.now(timezone.utc):
        await db.set_state(state_key, None)
        await db.audit(None, expired_action, None, "انتهت مدة الإيقاف المؤقت")
        return None
    return until.isoformat(timespec="seconds")


async def auto_freeze_pause_until(db) -> Optional[str]:
    """Return an active automatic-freeze pause, clearing it after expiration."""
    return await _timed_pause_until(
        db, "auto_freeze_paused_until", "auto_freeze_pause_expired"
    )


async def user_limits_pause_until(db) -> Optional[str]:
    """Return an active per-user limits pause, clearing it after expiration."""
    return await _timed_pause_until(
        db, "user_limits_paused_until", "user_limits_pause_expired"
    )


async def set_user_limits_pause(
    db, duration_minutes: int, actor_id: Optional[int], reason: str
) -> str:
    """Temporarily pause per-user rate caps; system-wide flood protection remains active."""
    if not config.USER_LIMITS_PAUSE_MINUTES_MIN <= duration_minutes <= config.USER_LIMITS_PAUSE_MINUTES_MAX:
        raise ValueError("duration_out_of_range")
    from datetime import datetime, timedelta, timezone

    until = (datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)).isoformat(
        timespec="seconds"
    )
    await db.set_state("user_limits_paused_until", until)
    await db.audit(
        actor_id, "user_limits_paused", None,
        f"{duration_minutes} دقيقة — {reason.strip()[:500]}",
    )
    return until


async def resume_user_limits(db, actor_id: Optional[int], reason: str = "") -> None:
    """Resume per-user limits immediately and record the administrative action."""
    await db.set_state("user_limits_paused_until", None)
    await db.audit(actor_id, "user_limits_resumed", None, reason.strip()[:500])


async def check_rate_limits(db, user_id: int) -> Verdict:
    """يفحص حدود المعدّل لمبلّغ واحد، ويجمّده عند التجاوز المتكرر."""
    block = await db.get_block(user_id)
    frozen_until = block.get("frozen_until")
    if frozen_until:
        # هل انتهت مدة التجميد؟
        from datetime import datetime, timezone

        try:
            end = datetime.fromisoformat(frozen_until)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
        except ValueError:
            end = None

        if end and end > datetime.now(timezone.utc):
            minutes = int((end - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            return Verdict(
                allowed=False,
                code="frozen",
                severity="block",
                message=await render_block_message(db, "frozen", {"minutes": minutes}),
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
            message=await render_block_message(db, "emergency"),
        )

    if await user_limits_pause_until(db):
        return Verdict(allowed=True, code="user_limits_paused")

    # حدود المبلّغ: دقيقة ثم ساعة
    in_minute = await db.count_events(_since_iso(60), user_id=user_id)
    if in_minute >= config.MAX_REPORTS_PER_MINUTE:
        await _register_strike(db, user_id, "تجاوز الحد الزمني في الدقيقة")
        return Verdict(
            allowed=False,
            code="minute_limit",
            severity="warn",
            message=await render_block_message(
                db, "minute_limit", {"limit": config.MAX_REPORTS_PER_MINUTE}
            ),
        )

    in_hour = await db.count_events(_since_iso(3600), user_id=user_id)
    if in_hour >= config.MAX_REPORTS_PER_HOUR:
        await _register_strike(db, user_id, "تجاوز الحد الساعي")
        return Verdict(
            allowed=False,
            code="hour_limit",
            severity="warn",
            message=await render_block_message(
                db, "hour_limit", {"limit": config.MAX_REPORTS_PER_HOUR}
            ),
        )

    return Verdict(allowed=True)


async def set_auto_freeze_pause(
    db, duration_minutes: int, actor_id: Optional[int], reason: str
) -> str:
    """Temporarily pause automatic freezes while keeping rate caps enforced."""
    if not (
        config.AUTO_FREEZE_PAUSE_MINUTES_MIN
        <= duration_minutes
        <= config.AUTO_FREEZE_PAUSE_MINUTES_MAX
    ):
        raise ValueError("duration_out_of_range")
    from datetime import datetime, timedelta, timezone

    until = (datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)).isoformat(
        timespec="seconds"
    )
    await db.set_state("auto_freeze_paused_until", until)
    await db.audit(
        actor_id,
        "auto_freeze_paused",
        None,
        f"{duration_minutes} دقيقة — {reason.strip()[:500]}",
    )
    return until


async def resume_auto_freeze(db, actor_id: Optional[int], reason: str = "") -> None:
    """Resume automatic freezes immediately and record the administrative action."""
    await db.set_state("auto_freeze_paused_until", None)
    await db.audit(actor_id, "auto_freeze_resumed", None, reason.strip()[:500])


async def _register_strike(db, user_id: int, reason: str) -> int:
    """يسجّل مخالفة ويزيد مدة التجميد تصاعديًا."""
    if await auto_freeze_pause_until(db):
        return int((await db.get_block(user_id)).get("strikes") or 0)
    block = await db.get_block(user_id)
    strikes = int(block.get("strikes") or 0) + 1
    if strikes < config.FREEZE_AFTER_STRIKES:
        await db.save_block(user_id, strikes, None, reason)
        await db.audit(None, "user_strike", str(user_id), reason)
        logger.info("تجاوز المستخدم %s الحدود (%d/%d)", user_id, strikes,
                    config.FREEZE_AFTER_STRIKES)
        return strikes
    freeze_level = strikes - config.FREEZE_AFTER_STRIKES + 1
    minutes = (
        config.FREEZE_MINUTES_FIRST
        if freeze_level == 1
        else config.FREEZE_MINUTES_SECOND
    )
    if freeze_level > 2:
        minutes = config.FREEZE_MINUTES_REPEAT
    from datetime import datetime, timedelta, timezone

    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    await db.save_block(user_id, strikes, until, reason)
    await db.audit(None, "user_frozen", str(user_id), f"{reason} — تجميد {minutes} دقيقة")
    logger.warning("تجميد المستخدم %s (%s) لمدة %d دقيقة", user_id, reason, minutes)
    return strikes


async def check_daily_limit(db, user_id: int) -> Verdict:
    """الحد اليومي لكل مبلّغ."""
    global_used = await db.count_since(utils.local_day_start_iso())
    if global_used >= config.MAX_REPORTS_PER_DAY_GLOBAL:
        await db.set_state("emergency", "1")
        await db.audit(
            None, "emergency_auto", "global",
            f"تجاوز الحد اليومي العام: {global_used} محاولة/يوم",
        )
        return Verdict(
            allowed=False,
            code="global_daily_flood",
            severity="block",
            message=await render_block_message(db, "emergency"),
        )
    if await user_limits_pause_until(db):
        return Verdict(allowed=True, code="user_limits_paused")

    used = await db.count_since(utils.local_day_start_iso(), user_id=user_id)
    if used >= config.MAX_REPORTS_PER_DAY:
        return Verdict(
            allowed=False,
            code="daily_limit",
            severity="warn",
            message=await render_block_message(
                db, "daily_limit", {"limit": config.MAX_REPORTS_PER_DAY}
            ),
        )
    return Verdict(allowed=True)


# ==================================================================
# 2) منع الاستقبال خارج المحادثات الخاصة
# ==================================================================
def is_private_chat(chat_type: Optional[str]) -> bool:
    """هل المحادثة خاصة (مستخدم ↔ بوت)؟"""
    return str(chat_type) == "private"


def is_public_command_allowed(user_id: int | None, command: str) -> bool:
    """Only /start is public; every other command is restricted to admins."""
    normalized = command.strip().lstrip("/").split("@", 1)[0].casefold()
    return normalized == "start" or (
        user_id is not None and user_id in config.ADMIN_IDS
    )


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


async def inspect_report_content(
    db, link: str, description: str
) -> tuple[Verdict, Optional[Verdict], Optional[Verdict]]:
    """Apply the same link, substance, and duplicate checks to every report flow."""
    link_verdict = check_link(link)
    if link_verdict.blocked:
        return link_verdict, None, None

    substance_verdict = check_substance(description)
    if substance_verdict.blocked:
        return link_verdict, substance_verdict, None

    duplicate_verdict = await check_duplicate(db, link, description)
    if link:
        scan = await virustotal.scan_url(link)
        link_verdict.extra["virustotal_status"] = scan.status
        link_verdict.extra["needs_review"] = scan.needs_review
        if scan.needs_review:
            link_verdict.extra["review_reason"] = "virustotal"
            link_verdict.message = texts.VIRUSTOTAL_REVIEW
            logger.info(
                "VirusTotal result requires manual review (status=%s, malicious=%d, suspicious=%d)",
                scan.status,
                scan.malicious,
                scan.suspicious,
            )
    return link_verdict, substance_verdict, duplicate_verdict


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
