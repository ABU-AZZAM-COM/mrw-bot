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


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


# ==================================================================
# نقطة الدخول: /start أو /report
# ==================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الترحيب وبدء جمع عناصر البلاغ — مع تطبيق الحد اليومي لكل مبلّغ."""
    user = update.effective_user
    db = _db(context)

    # حساب بداية اليوم المحلي وتحويله إلى UTC لمقارنته بوقت التسجيل
    if await db.count_since(utils.local_day_start_iso(), user_id=user.id) >= config.MAX_REPORTS_PER_DAY:
        await update.effective_message.reply_text(
            texts.LIMIT_REACHED.format(limit=config.MAX_REPORTS_PER_DAY),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

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
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الصورة (مضغوطة أو كملف) وحفظها في مجلد media/."""
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
            return ASK_PHOTO
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
            return ASK_PHOTO

        if not path.exists() or path.stat().st_size == 0:
            await msg.reply_text(texts.PHOTO_FAILED, parse_mode=ParseMode.HTML)
            return ASK_PHOTO

        photo_path = str(path)
        photo_name = path.name
    else:
        # لا نسخة محلية — نعتمد على file_id الموجود في تلغرام
        photo_path = ""
        photo_name = media.file_id

    context.user_data.update(
        photo_path=photo_path,
        photo_file_id=media.file_id,
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

    context.user_data["post_link"] = text
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
    # الخطوة الثانية اختيارية وتعمل فقط عند ضبط ARCHIVE_CHAT_ID.
    # ------------------------------------------------------------------
    await archive.post_report(context.bot, report)
    await archive.backup_database(context.bot, config.DB_PATH)

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return ConversationHandler.END


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
    """يردّ بمعرّف المحادثة الحالية — ضروري لمعرفة ARCHIVE_CHAT_ID."""
    chat = update.effective_chat
    user = update.effective_user
    await update.effective_message.reply_text(
        texts.CHAT_ID.format(
            chat_id=chat.id,
            title=utils.esc(chat.title or chat.full_name or "-"),
            chat_type=chat.type,
            user_id=user.id,
        ),
        parse_mode=ParseMode.HTML,
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
            MessageHandler(filters.ALL, unmatched),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )
