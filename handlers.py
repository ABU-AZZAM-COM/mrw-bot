"""
معالجات المستخدم العادي: ترحيب، استقبال عناصر البلاغ (صورة/رابط/وصف)، حفظ، وإشعار المشرفين.

مسار المحادثة (ConversationHandler)، أربع حالات:
  1) ASK_PHOTO : استقبال الصورة التوثيقية
  2) ASK_LINK  : استقبال رابط المنشور
  3) ASK_DESC  : استقبال وصف المخالفة
  4) CONFIRM   : تأكيد الحفظ النهائي
"""
from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import admin
import archive
import config
import guards
import texts
import utils
from database import Database
from keyboards import cancel_kb, confirm_kb

logger = logging.getLogger(__name__)

# مراحل المحادثة
ASK_PHOTO, ASK_LINK, ASK_DESC, CONFIRM = range(4)

# نصوص الأزرار (أنماط regex) لإلغاء العملية وتأكيد الحفظ
CANCEL_RE = r"^\s*❌\s*إلغاء\s*$"
CONFIRM_RE = r"^\s*✅\s*إرسال\s+البلاغ\s*$"

# ==================================================================
# طلبات التطبيق (Mini App): المستخدم الذي أرسل بياناته وينتظر الصورة
# ==================================================================
_pending_app_users: set[int] = set()


class AppPhotoPending(filters.MessageFilter):
    """يسمّي الرسائل التي تخصّ طلبًا مفتوحًا من التطبيق."""

    def filter(self, message) -> bool:
        user = message.from_user
        return bool(user and user.id in _pending_app_users)


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


# ==================================================================
# نقطة الدخول: /start أو /report
# ==================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الترحيب وبدء جمع عناصر البلاغ — مع تطبيق الحد اليومي لكل مبلّغ."""
    user = update.effective_user
    chat = update.effective_chat
    db = _db(context)

    # ------------------------------------------------------------------
    # الحماية ③: الاستقبال في المحادثات الخاصة فقط
    # (يمنع استعمال البوت داخل المجموعات، وهو أقوى وسيلة ضد الإبلاغ الجماعي)
    # ------------------------------------------------------------------
    if not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME), parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # ------------------------------------------------------------------
    # الحماية: حدود المعدّل (دقيقة/ساعة) + الإيقاف الطارئ + الحد اليومي
    # ------------------------------------------------------------------
    verdict = await guards.check_rate_limits(db, user.id)
    if verdict.blocked:
        await update.effective_message.reply_text(
            verdict.message, parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    daily = await guards.check_daily_limit(db, user.id)
    if daily.blocked:
        await update.effective_message.reply_text(
            daily.message, parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await db.log_event(user.id, "report")

    # بداية نظيفة لكل بلاغ جديد
    context.user_data.clear()
    name = utils.esc(user.first_name or "زائر")
    await update.effective_message.reply_text(
        texts.WELCOME.format(name=name),
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return ASK_PHOTO


# ==================================================================
# الحالة 1: الصورة التوثيقية
# ==================================================================
async def _save_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ الصورة ويعيد (المسار المحلي، file_id، الاسم) — مشترك بين المسارين."""
    msg = update.message
    user = update.effective_user

    if msg.photo:
        media = msg.photo[-1]          # أعلى جودة متاحة
        ext = ".jpg"
    else:
        doc = msg.document
        if doc.file_size and doc.file_size > config.MAX_PHOTO_MB * 1024 * 1024:
            await msg.reply_text(
                texts.PHOTO_TOO_BIG.format(mb=config.MAX_PHOTO_MB),
                parse_mode=ParseMode.HTML,
            )
            return "", None, ""
        ext = mimetypes.guess_extension(doc.mime_type or "") or ".jpg"
        media = doc

    # ------------------------------------------------------------------
    # حفظ الصورة على القرص: اختياري.
    # في الاستضافة السحابية المجانية قد لا يوجد قرص دائم، ونستطيع الاكتفاء
    # بـ file_id لأن تلغرام يحفظ الصورة في خوادمه إلى الأبد — وهي الطريقة
    # التي تجعل الدليل يبقى متاحًا بعد إعادة تشغيل الخادم.
    # ------------------------------------------------------------------
    if config.SAVE_MEDIA_LOCALLY:
        stamp = _stamp_name()
        path = config.MEDIA_DIR / f"{stamp}_{user.id}{ext}"
        try:
            tg_file = await media.get_file()
            await tg_file.download_to_drive(custom_path=str(path))
        except Exception:
            logger.exception("فشل تحميل الصورة للمستخدم %s", user.id)
            await msg.reply_text(texts.PHOTO_FAILED, parse_mode=ParseMode.HTML)
            return "", None, ""

        if not path.exists() or path.stat().st_size == 0:
            await msg.reply_text(texts.PHOTO_FAILED, parse_mode=ParseMode.HTML)
            return "", None, ""

        return str(path), media.file_id, path.name

    # لا نسخة محلية — نعتمد على file_id الموجود في تلغرام
    return "", media.file_id, media.file_id


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الصورة (مضغوطة أو كملف) وحفظها في مجلد media/."""
    msg = update.message

    photo_path, photo_file_id, photo_name = await _save_photo(update, context)
    if not photo_file_id:
        return ASK_PHOTO

    context.user_data.update(
        photo_path=photo_path,
        photo_file_id=photo_file_id,
        photo_name=photo_name,
    )
    await msg.reply_text(texts.ASK_LINK, parse_mode=ParseMode.HTML, reply_markup=cancel_kb())
    return ASK_LINK


async def remind_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عندما يرسل المستخدم شيئًا غير صورة في خطوة الصورة."""
    await update.effective_message.reply_text(texts.PHOTO_REMINDER, parse_mode=ParseMode.HTML)
    return ASK_PHOTO


# ==================================================================
# الحالة 2: رابط المنشور
# ==================================================================
async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not utils.is_valid_url(text):
        await update.message.reply_text(texts.LINK_INVALID, parse_mode=ParseMode.HTML)
        return ASK_LINK

    # تحديد منصة التواصل + تنبيه إن كان المصدر غير معروف
    verdict = guards.check_link(text)
    if verdict.blocked:
        await update.message.reply_text(verdict.message, parse_mode=ParseMode.HTML)
        return ASK_LINK

    platform = verdict.extra.get("platform") or ""
    context.user_data["post_link"] = text
    context.user_data["platform"] = platform

    if verdict.code == "unknown_platform":
        await update.message.reply_text(
            verdict.message, parse_mode=ParseMode.HTML
        )
    elif platform:
        await update.message.reply_text(
            texts.PLATFORM_DETECTED.format(platform=utils.esc(platform)),
            parse_mode=ParseMode.HTML,
        )

    await update.message.reply_text(
        texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return ASK_DESC


async def remind_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(texts.ASK_LINK, parse_mode=ParseMode.HTML)
    return ASK_LINK


# ==================================================================
# الحالة 3: وصف المخالفة
# ==================================================================
async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if len(text) > config.MAX_DESC_LEN:
        await update.message.reply_text(
            texts.DESC_TOO_LONG.format(length=len(text), max=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
        )
        return ASK_DESC

    context.user_data["description"] = text

    anchor = utils.link_anchor(context.user_data["post_link"])
    await update.message.reply_text(
        texts.SUMMARY.format(link=anchor, desc=utils.esc(text)),
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb(),
    )
    return CONFIRM


async def remind_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN), parse_mode=ParseMode.HTML
    )
    return ASK_DESC


# ==================================================================
# الحالة 4: التأكيد والحفظ
# ==================================================================
async def confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ البلاغ نهائيًا وإشعار المشرفين بوجوده."""
    user = update.effective_user
    ud = context.user_data
    db = _db(context)

    try:
        report = await db.add_report(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            photo_path=ud["photo_path"],
            photo_file_id=ud.get("photo_file_id"),
            post_link=ud["post_link"],
            description=ud["description"],
        )
    except Exception:
        logger.exception("فشل حفظ البلاغ للمستخدم %s", user.id)
        await update.effective_message.reply_text(
            texts.GENERAL_ERROR, parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    logger.info("بلاغ جديد %s من المستخدم %s", report["seq"], user.id)
    await update.effective_message.reply_text(
        texts.SAVED.format(
            seq=report["seq"],
            time=utils.fmt_local(report["created_at"]),
            status=texts.badge(report["status"]),
        ),
        parse_mode=ParseMode.HTML,
    )

    await _notify_admins(context, report)

    # ------------------------------------------------------------------
    # الأرشفة الرسمية:
    #   1) نشر البلاغ في القناة الرسمية الموحّدة (سجل زمني واحد لكل البلاغات)
    #   2) رفع نسخة من قاعدة البيانات إلى القناة (حماية البيانات من فقدان القرص)
    # الحماية ⑤: النشر لا يتم تلقائيًا — يُنتظر رأي المشرف
    # (يمنع وصول بلاغ كيدي أو مخالف إلى القناة الرسمية).
    # ------------------------------------------------------------------
    if config.AUTO_PUBLISH_TO_ARCHIVE:
        await archive.post_report(context.bot, report)
    await archive.backup_database(context.bot, config.DB_PATH)

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return ConversationHandler.END


# ==================================================================
# أمر /app — فتح التطبيق
# ==================================================================
async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل زر فتح التطبيق (Mini App) للمستخدم."""
    keyboard = keyboards_app_kb()
    if keyboard is None:
        await update.effective_message.reply_text(
            texts.APP_NOT_READY, parse_mode=ParseMode.HTML
        )
        return

    await update.effective_message.reply_text(
        texts.APP_INTRO, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


def keyboards_app_kb():
    """اختصار آمن لاستدعاء keyboards.app_kb (يتعامل مع غياب المكتبة)."""
    from keyboards import app_kb

    return app_kb()


# ==================================================================
# استقبال بيانات التطبيق (Mini App) ثم الصورة وإتمام البلاغ
# ==================================================================
async def app_data_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل ما ملأه المستخدم في التطبيق (نوع المخالفة، الموقع، الوصف، الرابط).

    ملاحظة أمنية: البيانات تصل عبر تلغرام نفسها (web_app_data) فهي موثّقة
    النسبة للمستخدم، وكل الفحوص الأمنية (المعدّل، التكرار، المضمون) تُطبَّق هنا
    تمامًا كما في مسار المحادثة العادي.
    """
    user = update.effective_user
    chat = update.effective_chat
    db = _db(context)

    # الحماية ③: التطبيق يعمل من المحادثة الخاصة فقط
    if not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME), parse_mode=ParseMode.HTML
        )
        return None

    # الحماية: الإيقاف الطارئ + حدود المعدّل
    if await guards.is_emergency(db):
        await update.effective_message.reply_text(
            texts.EMERGENCY_ON, parse_mode=ParseMode.HTML
        )
        return None

    verdict = await guards.check_rate_limits(db, user.id)
    if verdict.blocked:
        await update.effective_message.reply_text(verdict.message, parse_mode=ParseMode.HTML)
        return None

    daily = await guards.check_daily_limit(db, user.id)
    if daily.blocked:
        await update.effective_message.reply_text(daily.message, parse_mode=ParseMode.HTML)
        return None

    # قراءة البيانات وتحويلها إلى قاموس آمن
    raw = update.effective_message.web_app_data.data or "{}"
    try:
        import json

        data = json.loads(raw)
    except Exception:
        logger.warning("بيانات تطبيق غير صالحة من المستخدم %s", user.id)
        await update.effective_message.reply_text(
            texts.APP_BAD_DATA, parse_mode=ParseMode.HTML
        )
        return None

    violation_type = str(data.get("type") or "")[:60]
    place = str(data.get("place") or "")[:120]
    description = str(data.get("desc") or "")[: config.MAX_DESC_LEN]
    link = str(data.get("link") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")

    # فحص الرابط (إلزامي في نظامنا: المنشور على وسائل التواصل هو الدليل)
    link_verdict = guards.check_link(link) if link else guards.Verdict(
        allowed=False, code="missing_link", severity="block",
        message=(
            "🔗 <b>رابط المنشور إلزامي.</b>\n\n"
            "هذا النظام يستقبل مخالفات <b>منشورة على مواقع التواصل الاجتماعي</b>، "
            "والرابط هو الدليل الأساسي الذي يُبنى عليه التحقق.\n"
            "أرسل رابط المنشور الأصلي من فضلك."
        ),
    )
    if link_verdict.blocked:
        await update.effective_message.reply_text(
            link_verdict.message, parse_mode=ParseMode.HTML
        )
        return None

    platform = link_verdict.extra.get("platform") or ""
    if link_verdict.code == "unknown_platform":
        await update.effective_message.reply_text(
            link_verdict.message, parse_mode=ParseMode.HTML
        )

    # فحص مضمون الوصف
    substance = guards.check_substance(description)
    if substance.blocked:
        await update.effective_message.reply_text(
            substance.message, parse_mode=ParseMode.HTML
        )
        return None

    # كشف التكرار
    duplicate = await guards.check_duplicate(db, link, description)
    await db.log_event(user.id, "report")

    context.user_data.clear()
    context.user_data["app_draft"] = {
        "violation_type": violation_type,
        "place": place,
        "description": description,
        "post_link": link,
        "platform": platform,
        "lat": float(lat) if isinstance(lat, (int, float)) else None,
        "lon": float(lon) if isinstance(lon, (int, float)) else None,
        "suspect_duplicate": duplicate.code in {"exact_duplicate", "similar_duplicate"},
        "duplicate_of": duplicate.extra.get("duplicate_of"),
    }
    _pending_app_users.add(user.id)

    await update.effective_message.reply_text(
        texts.APP_DATA_OK.format(
            vtype=utils.esc(violation_type or "غير محدد"),
            place=utils.esc(place or "غير محدد"),
            dup="⚠️ " if duplicate.code in {"exact_duplicate", "similar_duplicate"} else "",
        ),
        parse_mode=ParseMode.HTML,
    )
    await update.effective_message.reply_text(
        texts.APP_SEND_PHOTO, parse_mode=ParseMode.HTML
    )
    return None


async def app_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الصورة بعد إرسال بيانات التطبيق ثم يُنشئ البلاغ كاملًا."""
    user = update.effective_user
    db = _db(context)
    ud = context.user_data
    draft = ud.pop("app_draft", None)
    _pending_app_users.discard(user.id)

    if not draft:
        return None

    # حفظ الصورة بنفس المنطق المستخدم في المحادثة العادية
    photo_path, photo_file_id, photo_name = await _save_photo(update, context)
    if not photo_file_id:
        await update.effective_message.reply_text(
            texts.PHOTO_FAILED, parse_mode=ParseMode.HTML
        )
        return None

    try:
        report = await db.add_report(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            photo_path=photo_path,
            photo_file_id=photo_file_id,
            post_link=draft["post_link"],
            description=draft["description"],
            violation_type=draft["violation_type"],
            place=draft["place"],
            latitude=draft["lat"],
            longitude=draft["lon"],
            source="app",
            platform=draft.get("platform") or "",
            needs_review=1 if draft["suspect_duplicate"] else 0,
        )
    except Exception:
        logger.exception("فشل حفظ بلاغ التطبيق للمستخدم %s", user.id)
        await update.effective_message.reply_text(
            texts.GENERAL_ERROR, parse_mode=ParseMode.HTML
        )
        return None

    logger.info("بلاغ جديد (تطبيق) %s من المستخدم %s", report["seq"], user.id)
    await update.effective_message.reply_text(
        texts.SAVED.format(
            seq=report["seq"],
            time=utils.fmt_local(report["created_at"]),
            status=texts.badge(report["status"]),
        ),
        parse_mode=ParseMode.HTML,
    )
    await _notify_admins(context, report)

    # البصمات لكشف التكرار مستقبلًا
    await db.add_fingerprint(
        report["id"],
        guards.link_hash(draft["post_link"]) if draft["post_link"] else None,
        guards.text_hash(draft["description"]),
        None,
    )

    if config.AUTO_PUBLISH_TO_ARCHIVE:
        await archive.post_report(context.bot, report)
    await archive.backup_database(context.bot, config.DB_PATH)

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return None


# ==================================================================
# إشعار المشرفين ببلاغ جديد
# ==================================================================
async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, report: dict) -> None:
    """إرسال صورة البلاغ مع ملخصها إلى كل مشرف معد في ADMIN_IDS."""
    caption = texts.ADMIN_NOTIFY.format(
        seq=report["seq"],
        name=utils.esc(report["full_name"] or "-"),
        username=texts.username_display(report["username"]),
        uid=report["user_id"],
        time=utils.fmt_local(report["created_at"]),
        anchor=utils.link_anchor(report["post_link"]),
        desc=utils.esc(utils.trunc(report["description"], 300)),
    )
    photo = Path(report["photo_path"]) if report["photo_path"] else None
    file_id = report.get("photo_file_id")

    for admin_id in config.ADMIN_IDS:
        try:
            if photo is not None and photo.exists():
                with open(photo, "rb") as fp:
                    await context.bot.send_photo(
                        chat_id=admin_id, photo=fp, caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
            elif file_id:
                # لا نسخة محلية على القرص — نرسل الصورة من تخزين تلغرام مباشرة
                await context.bot.send_photo(
                    chat_id=admin_id, photo=file_id, caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id, text=caption, parse_mode=ParseMode.HTML,
                )
        except Exception:
            logger.warning("تعذر إشعار المشرف %s ببلاغ %s", admin_id, report["seq"])


# ==================================================================
# أداة الإعداد: معرفة معرّف المحادثة (يُستخدم لضبط القناة الرسمية)
# ==================================================================
async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يردّ بمعرّف المحادثة الحالية — ضروري لمعرفة ARCHIVE_CHAT_ID.

    وعند تنفيذ الأمر داخل قناة، يُرسَل المعرّف أيضًا إلى أول مشرف في محادثته
    الخاصة، فيصله مباشرة دون حاجة لنسخه يدويًا.
    """
    chat = update.effective_chat
    user = update.effective_user
    message = texts.CHAT_ID.format(
        chat_id=chat.id,
        title=utils.esc(chat.title or chat.full_name or "-"),
        chat_type=chat.type,
        user_id=user.id,
    )
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)

    # إرسال المعرّف إلى المشرف في محادثة خاصة (تسهيل خطوة الإعداد)
    if chat.type != "private" and config.ADMIN_IDS:
        note = message + texts.CHAT_ID_ADMIN_NOTE.format(chat_id=chat.id)
        for admin_id in config.ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=note,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("تعذر إرسال معرّف المحادثة إلى المشرف %s: %s", admin_id, exc)
            break

    # تسجيل المعرّف في السجل ليكون بالإمكان قراءته آليًا أثناء الإعداد
    logger.info(
        "📌 معرّف المحادثة الحالي (للإعداد): %s | النوع: %s | الاسم: %s",
        chat.id,
        chat.type,
        chat.title or chat.full_name or "-",
    )


# ==================================================================
# إلغاء العملية (أمر أو زر)
# ==================================================================
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(texts.CANCELLED, parse_mode=ParseMode.HTML)
    context.user_data.clear()
    return ConversationHandler.END


# ==================================================================
# رسائل خارج المحادثة / خارج السياق المتوقع
# ==================================================================
async def welcome_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أي رسالة خاصة عندما لا تكون المحادثة نشطة (لا يوجد /start)."""
    await update.effective_message.reply_text(texts.WELCOME_HINT, parse_mode=ParseMode.HTML)


async def unmatched(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة غير مفهومة داخل المحادثة — تبقى المحادثة في حالتها الحالية."""
    await update.effective_message.reply_text(texts.FALLBACK_HINT, parse_mode=ParseMode.HTML)
    return None


async def exit_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فتح لوحة المشرف حتى أثناء محادثة البلاغ (تُنهي المحادثة)."""
    await admin.cmd_admin(update, context)
    return ConversationHandler.END


# ==================================================================
# معالج الأخطاء العام
# ==================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("خطأ أثناء معالجة تحديث: %s", context.error, exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                texts.GENERAL_ERROR, parse_mode=ParseMode.HTML
            )
    except Exception:
        pass


# ==================================================================
# بناء ConversationHandler وتسليمه إلى main.py
# ==================================================================
# ==================================================================
# أدوات مساعدة
# ==================================================================
def _stamp_name() -> str:
    """اسم فريد لملف الصورة: سنة-شهر-يوم_ساعة-دقيقة-ثانية"""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler(["start", "report"], cmd_start)],
        states={
            ASK_PHOTO: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_photo),
                MessageHandler(
                    ~filters.PHOTO & ~filters.Document.IMAGE & ~filters.COMMAND,
                    remind_photo,
                ),
            ],
            ASK_LINK: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, remind_link),
            ],
            ASK_DESC: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, remind_desc),
            ],
            CONFIRM: [
                MessageHandler(filters.Regex(CONFIRM_RE), confirm_save),
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(~filters.Regex(CONFIRM_RE), unmatched),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler(["start", "report"], cmd_start),
            CommandHandler(["admin", "reports"], exit_to_admin),
            CommandHandler("app", cmd_app),
            # ملاحظة: نستثني الأوامر من المصيدة الشاملة حتى لا تبتلع
            # أوامر المشرف (/panic /resume /audit) والأوامر الأخرى.
            MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )
