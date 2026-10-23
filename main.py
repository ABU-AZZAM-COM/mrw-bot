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
import handlers  # noqa: E402
import health_server  # noqa: E402
from database import Database  # noqa: E402

from telegram import BotCommand, BotCommandScopeChat, MenuButtonDefault, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# كائن تطبيق البوت (يُضبط في main) — يستخدمه مسار التطبيق في space_app
app = None


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

    base_commands = [
        BotCommand("start", "بدء إرسال بلاغ جديد"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
        BotCommand("chatid", "معرّف هذه المحادثة (للإعداد)"),
    ]
    await app.bot.set_my_commands(base_commands)

    # للمشرفين فقط: إضافة أمر اللوحة
    admin_commands = [*base_commands, BotCommand("admin", "لوحة إدارة البلاغات")]
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
    # الاستعادة من القناة الرسمية قبل فتح قاعدة البيانات:
    # في الاستضافة السحابية المجانية يفقد الخادم القرص عند كل إعادة تشغيل،
    # فنستعيد آخر نسخة محفوظة داخل القناة (مخزن استمرارية مجاني على تلغرام).
    # ------------------------------------------------------------------
    if config.ARCHIVE_CHAT_ID:
        try:
            restored = asyncio.run(
                archive.restore_database_if_needed(config.BOT_TOKEN, config.DB_PATH)
            )
            logger.info("♻️ استعادة القاعدة من القناة: %s", "تم" if restored else "لا حاجة")
        except Exception:
            logger.exception("خطأ أثناء محاولة استعادة قاعدة البيانات من القناة")

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

    # 1) بيانات التطبيق (Mini App) ثم الصورة — تُسجَّل قبل المحادثة حتى لا تبتلعها
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handlers.app_data_received)
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & handlers.AppPhotoPending()
            & (filters.PHOTO | filters.Document.ALL),
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

    # 2-ب) أوامر المشرف: الإيقاف الطارئ وسجل التدقيق
    app.add_handler(CommandHandler("panic", admin.cmd_panic))
    app.add_handler(CommandHandler("resume", admin.cmd_resume))
    app.add_handler(CommandHandler("audit", admin.cmd_audit))

    # 3) أي رسالة خاصة خارج محادثة نشطة → تلميح ترحيبي
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handlers.welcome_hint)
    )

    # 4) أداة الإعداد: /chatid (تعمل في أي محادثة — خاصة أو قناة)
    app.add_handler(CommandHandler("chatid", handlers.cmd_chatid))

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
    main()
