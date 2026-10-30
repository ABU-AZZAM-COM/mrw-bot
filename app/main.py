"""
نقطة التشغيل — يجمع كل الأجزاء ويشغّل استقبال Telegram.

الترتيب العام:
  1) تهيئة الطرفية والسجل (Logging)
  2) فتح قاعدة البيانات
  3) بناء التطبيق وتسجيل المعالجات
  4) بدء Webhook في الإنتاج أو polling محليًا
"""
import asyncio
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ------------------------------------------------------------------
# ضمان دعم الطرفية للنص العربي على ويندوز (قبل أي طباعة)
# ------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app import config  # noqa: E402  (بعد تهيئة الطرفية)
from app import health_server  # noqa: E402
from bot import admin, handlers, tracking_bot
from services import archive, guards, hub_backup
from database import Database  # noqa: E402
from bot.tracking_bot import TrackedBot  # noqa: E402

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

TELEGRAM_WEBHOOK_URL = "https://mrw-bot.onrender.com/telegram/webhook"
HEARTBEAT_INTERVAL_SECONDS = 60

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
    app.bot_data["welcome_photo"] = await app.bot_data["db"].get_welcome_photo()

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

    app.create_task(
        heartbeat_loop(app.bot_data["db"]),
        name="bot-heartbeat",
    )
    app.create_task(
        followup_deadline_loop(app),
        name="report-followup-deadlines",
    )


async def followup_deadline_loop(app) -> None:
    db = app.bot_data["db"]
    while True:
        try:
            closed = await db.close_expired_followups()
            for report in closed:
                try:
                    await app.bot.send_message(
                        chat_id=int(report["user_id"]),
                        text=(
                            f"أُغلق البلاغ {report.get('seq') or report['id']} "
                            "تلقائيًا لانتهاء مهلة اليومين دون رد. "
                            "يمكنك التواصل مع الإدارة إذا أردت طلب مراجعة."
                        ),
                    )
                except Exception:
                    logger.warning(
                        "تعذر إشعار المبلّغ بإغلاق مهلة البلاغ %s",
                        report.get("id"),
                        exc_info=True,
                    )
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("تعذر معالجة المهل المنتهية لطلبات معلومات البلاغ")
            await asyncio.sleep(60)
    app.create_task(
        notifications_cleanup_loop(app.bot_data["db"]),
        name="notifications-cleanup",
    )

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
    if (
        config.DB_BACKEND == "sqlite"
        and config.ARCHIVE_CHAT_ID
        and config.BACKUP_INTERVAL_MIN > 0
    ):
        app.create_task(
            archive.backup_loop(app.bot, config.DB_PATH, config.BACKUP_INTERVAL_MIN)
        )
    if hub_backup.is_configured() and config.BACKUP_INTERVAL_MIN > 0:
        app.create_task(
            hub_backup.database_backup_loop(
                app.bot_data["db"], config.BACKUP_INTERVAL_MIN
            )
        )


# ==================================================================
# عند الإيقاف: إغلاق قاعدة البيانات بأمان
# ==================================================================
async def post_shutdown(app) -> None:
    db = app.bot_data.get("db")
    if db:
        db.close()
    logger.info("🛑 تم إيقاف البوت وإغلاق قاعدة البيانات.")


async def write_heartbeat(db) -> None:
    await db.set_state(
        "bot_last_heartbeat",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


async def heartbeat_loop(db) -> None:
    """Persist a database-backed liveness marker independently of user traffic."""
    write_failed = False
    while True:
        try:
            await write_heartbeat(db)
            if write_failed:
                if not await db.get_state("bot_heartbeat_failure_notified"):
                    await db.create_notification(
                        "heartbeat_fail",
                        {"message": "تعافى اتصال قاعدة البيانات بعد تعذر نبضة حياة"},
                    )
                await db.set_state("bot_heartbeat_failure_notified", None)
                write_failed = False
        except Exception as exc:
            write_failed = True
            logger.error(
                "تعذرت كتابة نبضة حياة البوت (%s)", type(exc).__name__
            )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def notifications_cleanup_loop(db) -> None:
    """Prune old notifications every six hours without blocking the bot loop."""
    while True:
        await asyncio.sleep(6 * 60 * 60)
        try:
            deleted = await db.cleanup_notifications()
            if deleted:
                logger.info("تم تنظيف %s إشعار قديم", deleted)
            private_deleted = await db.cleanup_private_data()
            media_deleted = await db.cleanup_orphaned_media()
            if any(private_deleted.values()) or media_deleted:
                logger.info(
                    "تنظيف البيانات: تدقيق=%s، رسائل=%s، وسائط يتيمة=%s",
                    private_deleted["audit_deleted"],
                    private_deleted["messages_deleted"],
                    media_deleted,
                )
        except Exception:
            logger.exception("تعذر تنظيف البيانات القديمة")


async def run_webhook(application, secret: str) -> None:
    """Run PTB on its own loop while FastAPI receives Telegram webhook requests."""
    initialized = False
    started = False
    try:
        await application.initialize()
        initialized = True
        if application.post_init:
            await application.post_init(application)

        webhook_ready = False
        try:
            await application.bot.delete_webhook(drop_pending_updates=False)
            await application.bot.set_webhook(
                url=TELEGRAM_WEBHOOK_URL,
                secret_token=secret,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )
            webhook_ready = True
        except Exception as exc:
            logger.error(
                "فشل إعداد Telegram Webhook (%s)؛ سيُستخدم polling مؤقتًا",
                type(exc).__name__,
            )

        application.bot_data["webhook_ready"] = webhook_ready
        await application.start()
        started = True
        if webhook_ready:
            logger.info("✅ Telegram Webhook مضبوط على %s", TELEGRAM_WEBHOOK_URL)
        else:
            if application.updater is None:
                raise RuntimeError("لا يتوفر Updater للعودة إلى polling")
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )
            logger.critical(
                "⚠️ يعمل البوت مؤقتًا عبر polling بعد فشل إعداد Webhook"
            )

        await asyncio.Event().wait()
    finally:
        if application.updater and application.updater.running:
            await application.updater.stop()
        if started:
            await application.stop()
        if initialized:
            if application.post_shutdown:
                await application.post_shutdown(application)
            await application.shutdown()


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
    if config.DB_BACKEND == "sqlite":
        restored = hub_backup.restore_latest_if_needed(config.DB_PATH)
        if restored:
            logger.info("♻️ استعادة القاعدة من مستودع النسخ الاحتياطي: تم")
        else:
            logger.info("لا حاجة لاستعادة القاعدة (لدينا نسخة محلية أو لا نسخة سحابية)")
    else:
        logger.info("قاعدة البيانات الأساسية هي Turso؛ تم تجاوز استعادة SQLite المحلية")

    db = Database(config.DB_PATH, backend=config.DB_BACKEND)

    # ------------------------------------------------------------------
    # خادم الصحة: مطلوب في منصات الاستضافة السحابية (مثل Hugging Face Spaces)
    # حتى تعتبر المنصة أن الخدمة حيّة فلا توقفها تلقائيًا.
    # ------------------------------------------------------------------
    if health_server.is_space_environment():
        health_server.start_health_server()

    app = (
        ApplicationBuilder()
        .bot(TrackedBot(config.BOT_TOKEN, config.ADMIN_IDS))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # قاعدة البيانات متاحة لكل المعالجات عبر context.bot_data["db"]
    app.bot_data["db"] = db
    app.bot.set_tracking_database(db)

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

    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    use_webhook = bool(os.getenv("PORT")) and bool(webhook_secret)
    if os.getenv("PORT") and not webhook_secret:
        logger.error(
            "TELEGRAM_WEBHOOK_SECRET غير مضبوط؛ سيُستخدم polling مؤقتًا"
        )
    if use_webhook:
        asyncio.run(run_webhook(app, webhook_secret))
    elif in_main_thread:
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    else:
        if os.getenv("PORT"):
            logger.error(
                "تعذر تشغيل Webhook؛ سيُستخدم polling مؤقتًا"
            )
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
        from app.space_app import main as run_web_service

        run_web_service()
    else:
        main()
