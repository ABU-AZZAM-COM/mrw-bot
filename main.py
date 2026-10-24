"""
نقطة التشغيل — يجمع كل الأجزاء ويشغّل البوت (polling).

الترتيب العام:
  1) تهيئة الطرفية والسجل (Logging)
  2) فتح قاعدة البيانات
  3) بناء التطبيق وتسجيل المعالجات
  4) بدء الاستقبال من تلغرام
"""
import asyncio
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

# ------------------------------------------------------------------
# ضمان دعم الطرفية للنص العربي على ويندوز (قبل أي طباعة)
# ------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config  # noqa: E402  (بعد تهيئة الطرفية)
import admin   # noqa: E402
import archive  # noqa: E402
import guards  # noqa: E402
import handlers
import health_server  # noqa: E402
import hub_backup  # noqa: E402
from database import Database  # noqa: E402

from telegram import BotCommand, BotCommandScopeChat, MenuButtonDefault, Update
from telegram.ext import (
    ApplicationHandlerStop,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# عند التشغيل المباشر `python main.py` يكون اسم الوحدة __main__، بينما
# space_app / admin_api يستوردان `main`. نوحّد الاسمين حتى يقرأ الجميع
# نفس الكائن app ونفس قاعدة البيانات bot_data["db"].
if __name__ == "__main__":
    sys.modules.setdefault("main", sys.modules[__name__])

# كائن تطبيق البوت (يُضبط في main) — يستخدمه مسار التطبيق في space_app
app = None


async def command_access_control(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Allow only /start to non-admins and stop every other command early."""
    message = update.effective_message
    user = update.effective_user
    command_text = (message.text or message.caption or "") if message else ""
    if message is None or user is None or not command_text.startswith("/"):
        return
    command = command_text.split(maxsplit=1)[0]
    if guards.is_public_command_allowed(user.id, command):
        return
    await message.reply_text(
        "عذرًا، الأوامر المباشرة للمشرفين فقط. استخدم أزرار البوت التفاعلية."
    )
    raise ApplicationHandlerStop


# ==================================================================
# إعداد السجلات: شاشة + ملف دوّار logs/bot.log
# ==================================================================
def setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                config.LOGS_DIR / "bot.log",
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            ),
        ],
    )
    # تقليل ضجيج سجل مكتبة HTTP (وقد يظهر فيه التوكن أثناء التصحيح)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ==================================================================
# ما بعد التهيئة: ضبط قائمة أوامر البوت في تلغرام
# ==================================================================
async def post_init(app) -> None:
    # نحتفظ بحلقة البوت الحالية: مسار التطبيق الخادمي (/api/app-data) يحتاجها
    # لحقن التحديث في قائمة المعالجة من خيط خادم الويب (خيط آخر).
    try:
        app.bot_data["loop"] = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover
        pass

    runtime_settings = await app.bot_data["db"].get_runtime_settings()
    config.apply_runtime_settings(runtime_settings)

    base_commands = [BotCommand("start", "فتح الواجهة الرئيسية")]
    await app.bot.set_my_commands(base_commands)

    # للمشرفين فقط: أمر اللوحة + أمر النسخة الفورية
    admin_commands = [
        *base_commands,
        BotCommand("cancel", "إلغاء العملية الحالية"),
        BotCommand("reply", "إرسال معلومات إضافية عن بلاغ"),
        BotCommand("status", "عرض بلاغاتي"),
        BotCommand("chatid", "معرّف هذه المحادثة (للإعداد)"),
        BotCommand("admin", "لوحة إدارة البلاغات"),
        BotCommand("reports", "عرض لوحة البلاغات"),
        BotCommand("report", "بدء بلاغ من المحادثة"),
        BotCommand("stats", "إحصائيات المستخدمين والبلاغات"),
        BotCommand("backup", "رفع نسخة احتياطية فورية"),
        BotCommand("panic", "إيقاف استقبال البلاغات طارئًا"),
        BotCommand("resume", "استئناف استقبال البلاغات"),
        BotCommand("audit", "عرض سجل التدقيق"),
        BotCommand("app", "فتح تطبيق البلاغ"),
        BotCommand("help", "عرض التعليمات"),
    ]
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as exc:
            logger.warning("تعذر ضبط أوامر المشرف %s: %s", admin_id, exc)

    logger.info("✅ البوت جاهز ويستقبل الرسائل …")

    # ------------------------------------------------------------------
    # التطبيق (Mini App): زر القائمة الدائم + أمر فتح التطبيق
    # الزر يظهر بجانب مربع الكتابة لكل مستخدم → سهولة الوصول (المهم: أن يعرف
    # الجميع وجود التطبيق دون شرح).
    # ------------------------------------------------------------------
    # زر القائمة الجانبي: عودة إلى الأوامر الافتراضية (☰) — بلا زر تطبيق.
    # ------------------------------------------------------------------
    try:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception as exc:
        logger.warning("تعذر ضبط زر القائمة الافتراضي: %s", exc)

    # ------------------------------------------------------------------
    # القناة الرسمية: التحقق من الصلاحيات فقط (لا تُرسل أي رسالة)
    # ------------------------------------------------------------------
    if config.ARCHIVE_CHAT_ID:
        try:
            info = await app.bot.get_chat(config.ARCHIVE_CHAT_ID)
            logger.info("📁 القناة الرسمية جاهزة: %s", getattr(info, "title", config.ARCHIVE_CHAT_ID))
        except Exception as exc:
            logger.warning(
                "تعذّر الوصول إلى القناة الرسمية (%s) — تأكد أنك أضفت البوت مشرفًا فيها: %s",
                config.ARCHIVE_CHAT_ID, exc,
            )

    # ------------------------------------------------------------------
    # المهام الخلفية: إبقاء الاستضافة نشطة + النسخ الاحتياطي الدوري
    # ------------------------------------------------------------------
    if config.KEEPALIVE_URL:
        app.create_task(archive.keepalive_loop(config.KEEPALIVE_URL))
    if config.ARCHIVE_CHAT_ID and config.BACKUP_INTERVAL_MIN > 0:
        app.create_task(
            archive.backup_loop(app.bot, config.DB_PATH, config.BACKUP_INTERVAL_MIN)
        )
    if hub_backup.is_configured() and config.BACKUP_INTERVAL_MIN > 0:
        app.create_task(hub_backup.backup_loop(config.DB_PATH, config.BACKUP_INTERVAL_MIN))


# ==================================================================
# عند الإيقاف: إغلاق قاعدة البيانات بأمان
# ==================================================================
async def post_shutdown(app) -> None:
    db = app.bot_data.get("db")
    if db:
        db.close()
    logger.info("🛑 تم إيقاف البوت وإغلاق قاعدة البيانات.")


# ==================================================================
# التشغيل
# ==================================================================
def main() -> None:
    global app  # يُتاح لخادم التطبيق (space_app) لحقن بيانات الـ Mini App

    setup_logging()
    logger.info("🚀 تشغيل نظام استقبال بلاغات المخالفات العسكرية …")

    # ------------------------------------------------------------------
    # الاستعادة من مستودع النسخ الاحتياطي (Hugging Face) قبل فتح قاعدة البيانات:
    # في الاستضافة السحابية المجانية يفقد الخادم القرص عند كل إعادة تشغيل،
    # فنستعيد آخر نسخة من المستودع الخاص — فقط إذا كانت القاعدة المحلية فارغة.
    # (دالة متزامنة وآمنة: داخليًا تفحص وتقرر، وبلا توكن لا تفعل شيئًا.)
    # ------------------------------------------------------------------
    restored = hub_backup.restore_latest_if_needed(config.DB_PATH)
    if restored:
        logger.info("♻️ استعادة القاعدة من مستودع النسخ الاحتياطي: تم")
    else:
        logger.info("لا حاجة لاستعادة القاعدة (لدينا نسخة محلية أو لا نسخة سحابية)")

    db = Database(config.DB_PATH)

    # ------------------------------------------------------------------
    # خادم الصحة: مطلوب في منصات الاستضافة السحابية (مثل Hugging Face Spaces)
    # حتى تعتبر المنصة أن الخدمة حيّة فلا توقفها تلقائيًا.
    # ------------------------------------------------------------------
    if health_server.is_space_environment():
        health_server.start_health_server()

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # قاعدة البيانات متاحة لكل المعالجات عبر context.bot_data["db"]
    app.bot_data["db"] = db

    # Run before every command-specific handler so unlisted commands are gated too.
    app.add_handler(
        MessageHandler(filters.Regex(r"^/"), command_access_control),
        group=-2,
    )

    # 1) بيانات التطبيق (Mini App) ثم الصورة — تُسجَّل قبل المحادثة حتى لا تبتلعها
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handlers.app_data_received)
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & handlers.AppPhotoPending()
            & (filters.PHOTO | filters.Document.IMAGE),
            handlers.app_photo_received,
        )
    )
    # 1-ب) محادثة إدخال البلاغ العادية
    app.add_handler(handlers.build_conversation())

    # 1-ج) أمر /app لفتح التطبيق
    app.add_handler(CommandHandler("app", handlers.cmd_app))

    # 2) لوحة المشرف
    app.add_handler(CommandHandler(["admin", "reports"], admin.cmd_admin))
    app.add_handler(CallbackQueryHandler(admin.on_callback, pattern=r"^adm:"))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            admin.receive_reject_reason,
        ),
        group=-1,
    )

    # 2-ب) أوامر المشرف: الإيقاف الطارئ وسجل التدقيق ونسخة فورية
    app.add_handler(CommandHandler("panic", admin.cmd_panic))
    app.add_handler(CommandHandler("resume", admin.cmd_resume))
    app.add_handler(CommandHandler("audit", admin.cmd_audit))
    app.add_handler(CommandHandler("backup", admin.cmd_backup))
    app.add_handler(CommandHandler("stats", admin.cmd_stats))

    # 3) أي رسالة خاصة خارج محادثة نشطة → تلميح ترحيبي
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handlers.welcome_hint)
    )

    # 4) أداة الإعداد + دليل الاستخدام + حالة بلاغاتي
    app.add_handler(CommandHandler("chatid", handlers.cmd_chatid))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    app.add_handler(CommandHandler("reply", handlers.cmd_reply))

    # 5) معالج الأخطاء العام
    app.add_error_handler(handlers.error_handler)

    # بدء الاستقبال.
    # drop_pending_updates=False مقصود: أي بلاغ يصل البوت أثناء انقطاعه
    # يبقى في طابور تلغرام (حتى 24 ساعة) ويُسلَّم فور عودة التشغيل — فلا تضيع بلاغات.
    #
    # ملاحظة مهمة: تثبيت معالجات الإشارات (للإيقاف النظيف) يعمل في الخيط الرئيسي
    # فقط. لذلك نلغيها عند التشغيل داخل خيط خلفي (مثل غلاف Gradio في
    # Hugging Face Space)، ونترك السلوك الافتراضي كما هو في التشغيل العادي.
    in_main_thread = threading.current_thread() is threading.main_thread()

    if in_main_thread:
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    else:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            stop_signals=(),
        )


if __name__ == "__main__":
    # Render's native Python service commonly starts `python main.py`.
    # Use the web entrypoint when the platform provides its listening port;
    # keep the regular local bot-only workflow unchanged.
    if os.getenv("PORT"):
        from space_app import main as run_web_service

        run_web_service()
    else:
        main()
