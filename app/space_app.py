"""
غلاف المساحة السحابية: يشغّل البوت + يقدّم تطبيق البلاغ على نفس المنفذ.

لماذا هذا الملف؟
- مساحة Hugging Face المجانية (ZeroGPU) تشغّل ملفًا واحدًا على منفذ PORT واحد.
- نريد في الوقت نفسه:
    1) البوت يعمل 24/7 (خيط خلفي).
    2) تطبيق البلاغ (Mini App) متاح عبر HTTPS ليعمل داخل تيليغرام.
    3) صفحة حالة بسيطة لمتابعة التشغيل.

الطريقة:
- خادم FastAPI/uvicorn واحد على منفذ المنصة (PORT):
    • /        → تطبيق البلاغ (ملفات ثابتة)
    • /status  → صفحة حالة بسيطة (HTML خفيف)
    • /health  → فحص سريع للمنصة
- نُبقي Gradio خارج المعادلة عن قصد: وكيله (Node) يعترض كل المسارات
  ويخدم صفحته هو، فلا يبقى مكان لتطبيقنا على نفس المنفذ.

شروط لا بد من احترامها في هذه البيئة:
  1) مساحة ZeroGPU لا تعمل إلا إذا وُجدت دالة واحدة على الأقل معلَّمة بـ `@spaces.GPU`
     (وإلا أوقفتها المنصة برسالة: No @spaces.GPU function detected during startup).
     لذلك نُرسل تقرير الإقلاع يدويًا (نفس ما تفعله المكتبة داخليًا).
  2) البوت يعمل في خيط خلفي، وتثبيت معالجات الإشارات لا يعمل إلا في الخيط الرئيسي
     — لذلك تُلغى تلقائيًا في main.py.
  3) خادم الصحة الداخلي (health_server) يُعطَّل هنا لأن المنفذ محجوز للتطبيق.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# إيقاف خادم الصحة قبل استيراد main (نفس المنفذ سيخدم التطبيق)
os.environ["DISABLE_HEALTH_SERVER"] = "1"

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# حزمة spaces هي التي تدير تخصيص كرت الشاشة داخل مساحات ZeroGPU.
# خارج Hugging Face Spaces قد لا تكون مثبتة، فنستخدم بديلًا بسيطًا (تطبيق بلا أثر).
try:
    import spaces  # noqa: E402

    GPU = spaces.GPU
except ImportError:  # pragma: no cover - بيئة محلية بدون Hugging Face
    def GPU(func=None, **kwargs):  # type: ignore[misc]
        """بديل بسيط لـ spaces.GPU يعمل خارج مساحات ZeroGPU."""
        if func is None:
            return lambda inner: inner
        return func

from app import config, main as bot_main  # noqa: E402
from api import admin_api, admin_dashboard  # noqa: E402
from api.rate_limit import install_admin_auth_guard  # noqa: E402
from bot import texts  # noqa: E402
from services.chat_attachments import (  # noqa: E402
    MAX_CHAT_ATTACHMENT_BYTES,
    sanitize_attachment_filename,
    validate_chat_attachment,
)
from services.report_taxonomy import REPORT_CATEGORIES
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("space")

BOT_USERNAME = os.getenv("BOT_USERNAME", "@blgh_bot")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
START_TIME = time.time()

BASE_DIR = Path(__file__).resolve().parents[1]
WEBAPP_DIR = BASE_DIR / "webapp"

# مهلة قبول بيانات التطبيق (تيليغرام يوقّع initData بتاريخ إصدار)
INIT_DATA_MAX_AGE = 24 * 60 * 60
BOT_STARTUP_GRACE_SECONDS = 240
BOT_HEALTH_CHECK_INTERVAL_SECONDS = 30
BOT_HEALTH_FAILURE_SECONDS = 180
BOT_RESTART_INITIAL_DELAY_SECONDS = 5
BOT_RESTART_MAX_DELAY_SECONDS = 60
_BOT_STOP_EVENT = threading.Event()


def _run_bot() -> None:
    """Run the bot with bounded restart backoff if its main loop exits."""
    delay = BOT_RESTART_INITIAL_DELAY_SECONDS
    while not _BOT_STOP_EVENT.is_set():
        try:
            bot_main.main()
            logger.error("انتهى تشغيل البوت؛ ستتم محاولة إعادة تشغيله")
        except Exception as exc:
            logger.error(
                "توقف البوت داخل المساحة السحابية (%s)؛ ستتم إعادة المحاولة",
                type(exc).__name__,
            )
        if _BOT_STOP_EVENT.wait(delay):
            break
        delay = min(delay * 2, BOT_RESTART_MAX_DELAY_SECONDS)


@GPU(duration=5)
def _gpu_marker() -> str:
    """دالة توافق مع شرط ZeroGPU.

    التوثيق (@spaces.GPU) إلزامي في مساحات ZeroGPU: بدون دالة واحدة معلَّمة
    بهذا لا تُقلع المساحة إطلاقًا (رسالة: No @spaces.GPU function detected).
    وهي لا تستهلك أي طاقة معالجة فعلية.
    """
    return "ok"


def _zerogpu_startup_report() -> None:
    """يُرسل تقرير الإقلاع إلى خدمة ZeroGPU (شرط تشغيل المساحة).

    نكرّر هنا ما تفعله مكتبة spaces داخليًا عند إقلاع Gradio، لأننا نشغّل
    خادمنا بأنفسنا (بلا Gradio) ولا نستدعي مسار الإقلاع الرسمي.
    """
    try:
        from spaces.zero import client, config as zg_config, decorator, torch

        total_size = torch.pack()
        threshold = zg_config.get_config()["xlarge_threshold"]
        if threshold is not None and total_size > threshold:
            decorator._default_gpu_size = "xlarge"
        if len(decorator.decorated_cache) == 0:
            return
        client.startup_report()
        logger.info("✅ تم إرسال تقرير إقلاع ZeroGPU")
    except Exception as exc:  # noqa: BLE001
        logger.info("ℹ️ لم يُرسل تقرير ZeroGPU (%s) — طبيعي خارج مساحات ZeroGPU", exc)


def _verify_init_data(
    init_data: str,
    bot_token: str,
    max_age: int = INIT_DATA_MAX_AGE,
) -> dict | None:
    """يتحقق من توقيع تيليغرام (initData) ويعيد بيانات المستخدم.

    الخوارزمية الرسمية:
        secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
        hash       = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))

    حيث data_check_string هو كل الحقول مرتبة أبجديًا بصيغة key=value
    ومفصولة بـ "\\n" بعد استثناء حقل hash.

    بدون هذا التحقق يستطيع أي شخص إرسال بلاغ باسم أي مستخدم،
    لذلك لا يُقبل أي طلب لا يمر من هنا.
    """
    if not init_data or not bot_token:
        return None

    try:
        # parse_qsl هو ما تستخدمه أمثلة تيليغرام الرسمية: يفكّ %20 و + معًا
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        fields = {key: value for key, value in pairs}
    except Exception:  # noqa: BLE001
        return None

    received_hash = fields.pop("hash", "")
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        logger.warning("رفض بيانات تطبيق: توقيع غير صحيح")
        return None

    # منع إعادة استعمال توقيع قديم (تسريب رابط/بيانات جلسة)
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date <= 0 or (time.time() - auth_date) > max_age:
        logger.warning("رفض بيانات تطبيق: توقيع منتهي الصلاحية")
        return None

    try:
        user = json.loads(fields.get("user", "{}"))
    except Exception:  # noqa: BLE001
        return None
    return user if isinstance(user, dict) else None


def _webapp_user_id(request: Request) -> int | None:
    user = _verify_init_data(
        request.headers.get("X-Telegram-Init-Data", ""),
        BOT_TOKEN,
    )
    user_id = user.get("id") if user else None
    return user_id if isinstance(user_id, int) and user_id > 0 else None


def _webapp_database():
    application = getattr(bot_main, "app", None)
    if application is None:
        return None
    return application.bot_data.get("db")


def _webapp_denied(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": error},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _inject_app_update(user_id: int, payload: dict, tg_user: dict) -> bool:
    """يُدخل بيانات التطبيق إلى البوت كأنها وصلت من تيليغرام (web_app_data).

    لماذا الحقن بدل معالجة الطلب هنا؟
    لأن كل الحمايات (المعدّل، التجميد، التكرار، المضمون، الإيقاف الطارئ)
    وقاعدة البيانات تعمل داخل حلقة البوت وفي خيطها — فتُعاد استخدامها كما هي
    بلا تكرار منطق أمني ولا مخاطرة على قاعدة SQLite.
    """
    app = getattr(bot_main, "app", None)
    if app is None or app.bot is None:
        return False

    queue = getattr(app, "update_queue", None)
    loop = app.bot_data.get("loop")
    if queue is None or loop is None:
        logger.warning("البوت لم يُهيَّأ بعد — تعذر حقن بيانات التطبيق")
        return False

    try:
        from telegram import Chat, Message, Update, User, WebAppData

        message = Message(
            message_id=0,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type=Chat.PRIVATE),
            from_user=User(
                id=user_id,
                first_name=str(tg_user.get("first_name") or "مستخدم")[:64],
                is_bot=False,
                username=tg_user.get("username") or None,
            ),
            web_app_data=WebAppData(
                data=json.dumps(payload, ensure_ascii=False),
                button_text="تطبيق البلاغ",
            ),
        )
        message.set_bot(app.bot)
        update = Update(update_id=0, message=message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("تعذر بناء تحديث التطبيق: %s", exc)
        return False

    try:
        loop.call_soon_threadsafe(queue.put_nowait, update)
    except Exception as exc:  # noqa: BLE001
        logger.warning("تعذر حقن تحديث التطبيق: %s", exc)
        return False
    return True


async def _bot_health_snapshot() -> tuple[bool, bool, bool, bool]:
    application = getattr(bot_main, "app", None)
    database = (
        application.bot_data.get("db")
        if application is not None
        else None
    )
    bot_ready = bool(application is not None and application.bot is not None)
    if database is None:
        return False, bot_ready, False, False
    try:
        database_ready = bool(database.health_check())
    except Exception:
        logger.warning("فشل فحص قاعدة البيانات في نقطة الصحة", exc_info=True)
        return False, bot_ready, False, False
    if not database_ready:
        return False, bot_ready, False, False

    heartbeat = await database.get_state("bot_last_heartbeat")
    bot_alive = False
    if heartbeat:
        try:
            heartbeat_at = datetime.fromisoformat(
                heartbeat.replace("Z", "+00:00")
            )
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - heartbeat_at).total_seconds()
            bot_alive = 0 <= age < BOT_HEALTH_FAILURE_SECONDS
        except (AttributeError, TypeError, ValueError):
            logger.warning("قيمة نبضة الحياة في قاعدة البيانات غير صالحة")
    starting = (
        not heartbeat
        and time.time() - START_TIME < BOT_STARTUP_GRACE_SECONDS
    )
    return database_ready, bot_ready, bot_alive, starting


async def _monitor_bot_health() -> None:
    """Detect heartbeat loss independently of admin dashboard requests."""
    while True:
        await asyncio.sleep(BOT_HEALTH_CHECK_INTERVAL_SECONDS)
        application = getattr(bot_main, "app", None)
        database = (
            application.bot_data.get("db")
            if application is not None
            else None
        )
        if database is None:
            continue
        try:
            if not database.health_check():
                continue
            heartbeat = await database.get_state("bot_last_heartbeat")
            stale = False
            if heartbeat:
                try:
                    heartbeat_at = datetime.fromisoformat(
                        heartbeat.replace("Z", "+00:00")
                    )
                    if heartbeat_at.tzinfo is None:
                        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - heartbeat_at).total_seconds()
                    stale = age >= BOT_HEALTH_FAILURE_SECONDS
                except (AttributeError, TypeError, ValueError):
                    stale = True
            else:
                stale = time.time() - START_TIME >= BOT_STARTUP_GRACE_SECONDS

            failure_notified = await database.get_state(
                "bot_heartbeat_failure_notified"
            )
            if stale and not failure_notified:
                await database.create_notification(
                    "heartbeat_fail",
                    {"message": "لم تصل نبضة حياة البوت خلال 180 ثانية"},
                )
                await database.set_state("bot_heartbeat_failure_notified", "1")
            elif not stale and failure_notified:
                await database.create_notification(
                    "heartbeat_fail",
                    {"message": "عاد البوت لإرسال نبضات الحياة"},
                )
                await database.set_state(
                    "bot_heartbeat_failure_notified", None
                )
        except Exception:
            logger.exception("تعذر فحص نبضة حياة البوت في المراقب المستقل")


def build_app() -> FastAPI:
    """يبني تطبيق الويب: فحص + صفحة حالة + ملفات التطبيق."""
    app = FastAPI(title="نظام بلاغات المخالفات العسكرية", docs_url=None, redoc_url=None)
    _origins = [
        origin.strip()
        for origin in os.getenv(
            "ADMIN_CORS_ORIGINS",
            "http://localhost:9000,http://127.0.0.1:9000",
        ).split(",")
        if origin.strip()
    ]
    # الفشل يُغلق لا يُفتح: غياب النطاقات يعني «لا سماح عبر النطاقات»، لا «اسمح للجميع».
    # السماح الصريح بـ``*`` ممكن لكنه يُسجَّل تحذيرًا لأنه يفتح الـAPI لأي موقع.
    if "*" in _origins:
        logger.warning(
            "ADMIN_CORS_ORIGINS يتضمن * — الـAPI مفتوح لأي نطاق. حدّد النطاقات الفعلية في الإنتاج."
        )
    elif not _origins:
        logger.warning(
            "ADMIN_CORS_ORIGINS فارغ — لن يُسمح بأي طلب عبر النطاقات. اضبط النطاقات الفعلية."
        )

    # حماية محاولات المصادقة المتكررة (تُركَّب قبل CORS ليبقى CORS في الطبقة الخارجية).
    install_admin_auth_guard(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "x-admin-token",
            "x-setup-token",
            "x-telegram-init-data",
            "content-type",
        ],
    )

    @app.on_event("startup")
    async def start_bot_health_monitor() -> None:
        app.state.bot_health_monitor = asyncio.create_task(
            _monitor_bot_health(), name="bot-health-monitor"
        )

    @app.on_event("shutdown")
    async def stop_background_tasks() -> None:
        _BOT_STOP_EVENT.set()
        monitor = getattr(app.state, "bot_health_monitor", None)
        if monitor is not None:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

    @app.post("/telegram/webhook")
    async def telegram_webhook(request: Request) -> JSONResponse:
        expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        provided_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if not expected_secret:
            return JSONResponse(
                {"ok": False, "error": "webhook_secret_not_configured"},
                status_code=503,
            )
        if not provided_secret or not hmac.compare_digest(
            provided_secret.encode("utf-8"),
            expected_secret.encode("utf-8"),
        ):
            return JSONResponse(
                {"ok": False, "error": "unauthorized"}, status_code=403
            )

        application = getattr(bot_main, "app", None)
        loop = (
            application.bot_data.get("loop")
            if application is not None
            else None
        )
        if (
            application is None
            or application.bot is None
            or loop is None
            or not loop.is_running()
        ):
            return JSONResponse(
                {"ok": False, "error": "bot_not_ready"}, status_code=503
            )

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "error": "bad_json"}, status_code=400
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                {"ok": False, "error": "bad_update"}, status_code=400
            )

        from telegram import Update

        try:
            update = Update.de_json(payload, application.bot)
        except (TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "error": "bad_update"}, status_code=400
            )
        loop.call_soon_threadsafe(application.update_queue.put_nowait, update)
        return JSONResponse({"ok": True})

    # 1) فحص سريع للمنصة
    @app.get("/health")
    async def health() -> JSONResponse:
        database_ready, bot_ready, bot_alive, starting = await _bot_health_snapshot()
        healthy = database_ready and bot_ready and (bot_alive or starting)
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "uptime_seconds": int(time.time() - START_TIME),
                "bot": BOT_USERNAME,
                "bot_ready": bot_ready,
                "bot_alive": bot_alive,
                "database_ready": database_ready,
                "starting": starting,
            },
            status_code=200 if healthy else 503,
        )

    # 2) مسار استقبال بيانات التطبيق (يعمل من أي طريقة فتح: زر القائمة، زر لوحة الرد، رابط مباشر)
    @app.post("/api/app-data")
    async def app_data(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

        payload = body.get("payload") if isinstance(body, dict) else None
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "bad_payload"}, status_code=400)

        tg_user = _verify_init_data(str(body.get("init_data") or ""), BOT_TOKEN)
        if not tg_user:
            return JSONResponse({"ok": False, "error": "invalid_init_data"}, status_code=403)

        user_id = tg_user.get("id")
        if not isinstance(user_id, int) or user_id <= 0:
            return JSONResponse({"ok": False, "error": "no_user"}, status_code=403)

        if not _inject_app_update(user_id, payload, tg_user):
            return JSONResponse({"ok": False, "error": "bot_not_ready"}, status_code=503)
        return JSONResponse({"ok": True})

    @app.get("/api/public/vtypes")
    async def public_vtypes() -> JSONResponse:
        application = bot_main.app
        if application is None or "db" not in application.bot_data:
            return JSONResponse(
                {"ok": False, "error": "bot_not_ready"}, status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        try:
            items = await application.bot_data["db"].effective_vtypes()
        except Exception:
            logger.exception("تعذر تحميل تصنيفات التطبيق")
            return JSONResponse(
                {"ok": False, "error": "types_unavailable"}, status_code=500,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            {
                "ok": True,
                "items": items,
                "categories": REPORT_CATEGORIES,
                "groups": await application.bot_data["db"].effective_vtype_groups(),
                "max_desc_len": config.MAX_DESC_LEN,
                "min_desc_len": config.MIN_DESC_LEN,
                "min_desc_words": config.MIN_DESC_WORDS,
                "max_photo_mb": config.MAX_PHOTO_MB,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/user/settings")
    async def user_settings(request: Request) -> JSONResponse:
        user = _verify_init_data(
            request.headers.get("X-Telegram-Init-Data", ""),
            BOT_TOKEN,
        )
        user_id = user.get("id") if user else None
        if not isinstance(user_id, int) or user_id <= 0:
            return _webapp_denied(403, "invalid_init_data")
        db = _webapp_database()
        if db is None:
            return _webapp_denied(503, "bot_not_ready")

        global_raw = await db.get_state("contact_global_enabled")
        global_enabled = global_raw != "0"
        account_enabled = await db.user_contact_enabled(user_id)
        preferences = await db.user_contact_preferences(user_id)
        verification = await db.verification_status(user_id)
        verification_summary = None
        if verification:
            verification_summary = {
                "status": verification.get("status"),
                "review_state": verification.get("review_state"),
                "message": verification.get("supplement_message") or "",
                "reason": verification.get("rejection_reason") or "",
            }
        return JSONResponse(
            {
                "ok": True,
                "contact_global_enabled": global_enabled,
                "account_contact_enabled": account_enabled,
                "can_send_contact": global_enabled and account_enabled,
                "preferences": preferences,
                "verification": verification_summary,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.put("/api/user/settings")
    async def update_user_settings(request: Request) -> JSONResponse:
        user_id = _webapp_user_id(request)
        if user_id is None:
            return _webapp_denied(403, "invalid_init_data")
        db = _webapp_database()
        if db is None:
            return _webapp_denied(503, "bot_not_ready")
        try:
            body = await request.json()
        except Exception:
            return _webapp_denied(400, "bad_json")
        if not isinstance(body, dict):
            return _webapp_denied(400, "bad_payload")
        reply_alerts = body.get("telegram_reply_alerts")
        publication_alerts = body.get("publication_alerts")
        if type(reply_alerts) is not bool or type(publication_alerts) is not bool:
            return _webapp_denied(400, "bad_settings")
        await db.set_user_contact_preferences(
            user_id,
            reply_alerts,
            publication_alerts,
        )
        return JSONResponse(
            {"ok": True},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/user/messages")
    async def user_messages(request: Request) -> JSONResponse:
        user_id = _webapp_user_id(request)
        if user_id is None:
            return _webapp_denied(403, "invalid_init_data")
        db = _webapp_database()
        if db is None:
            return _webapp_denied(503, "bot_not_ready")
        try:
            after_id = max(0, int(request.query_params.get("after_id", "0") or 0))
        except ValueError:
            return _webapp_denied(400, "bad_query")
        raw_before_id = request.query_params.get("before_id")
        if raw_before_id is not None:
            try:
                before_id = max(0, int(raw_before_id or 0))
            except ValueError:
                return _webapp_denied(400, "bad_query")
            items = await db.list_user_messages_before(
                user_id, before_id, 100, conversation_only=True
            )
        else:
            items = await db.list_user_messages(
                user_id, after_id, 100, conversation_only=True
            )
        safe_items = []
        for item in items:
            safe_items.append({
                key: item.get(key)
                for key in (
                    "id",
                    "direction",
                    "text",
                    "created_at",
                    "delivery_status",
                    "media_type",
                    "filename",
                    "is_edited",
                    "is_deleted",
                )
            } | {
                "has_media": bool(
                    item.get("telegram_file_id") or item.get("has_attachment")
                ),
            })
        return JSONResponse(
            {"ok": True, "items": safe_items},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/user/messages/{message_id}/attachment")
    async def user_message_attachment(
        message_id: int, request: Request
    ) -> Response:
        user_id = _webapp_user_id(request)
        if user_id is None:
            return _webapp_denied(403, "invalid_init_data")
        db = _webapp_database()
        if db is None:
            return _webapp_denied(503, "bot_not_ready")
        rows = await db.list_user_messages(
            user_id,
            max(0, message_id - 1),
            1,
            conversation_only=True,
        )
        if not rows or int(rows[0]["id"]) != message_id:
            return _webapp_denied(404, "not_found")
        item = rows[0]
        if item.get("is_deleted"):
            return _webapp_denied(404, "attachment_not_found")
        attachment = await db.get_user_message_attachment(user_id, message_id)
        if attachment is not None:
            data = attachment["data"]
            content_type = attachment["content_type"]
            filename = attachment["filename"]
        else:
            file_id = str(item.get("telegram_file_id") or "")
            if not file_id:
                return _webapp_denied(404, "attachment_not_found")
            application = getattr(bot_main, "app", None)
            bot = getattr(application, "bot", None)
            if bot is None:
                return _webapp_denied(503, "bot_not_ready")

            async def _download():
                telegram_file = await bot.get_file(file_id)
                return await telegram_file.download_as_bytearray()

            try:
                data = await admin_api._on_bot_loop_value(_download)
            except Exception:
                logger.exception(
                    "تعذر تنزيل مرفق المحادثة %s للمستخدم %s",
                    message_id,
                    user_id,
                )
                return _webapp_denied(502, "attachment_download_failed")
            if data is None:
                return _webapp_denied(503, "bot_not_ready")
            filename = str(item.get("filename") or f"attachment-{message_id}")
            if str(item.get("media_type") or "") == "photo":
                filename = "attachment.jpg"
            detected = validate_chat_attachment(bytes(data), filename)
            if detected is None:
                return _webapp_denied(415, "unsupported_attachment")
            _media_kind, content_type = detected
        return Response(
            content=bytes(data),
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''"
                    + urllib.parse.quote(filename)
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/user/messages")
    async def create_user_message(request: Request) -> JSONResponse:
        user_id = _webapp_user_id(request)
        if user_id is None:
            return _webapp_denied(403, "invalid_init_data")
        db = _webapp_database()
        if db is None:
            return _webapp_denied(503, "bot_not_ready")
        global_enabled = await db.get_state("contact_global_enabled") != "0"
        if not global_enabled or not await db.user_contact_enabled(user_id):
            return _webapp_denied(403, "contact_unavailable")
        try:
            body = await request.json()
        except Exception:
            return _webapp_denied(400, "bad_json")
        text = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
        if not 1 <= len(text) <= 2000:
            return _webapp_denied(400, "text_length")
        await db.open_user_conversation(user_id)
        message_id = await db.add_user_message(
            user_id,
            "in",
            text,
            delivery_status="received",
        )
        return JSONResponse(
            {"ok": True, "message_id": message_id},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/user/messages/attachment")
    async def create_user_message_attachment(request: Request) -> JSONResponse:
        user_id = _webapp_user_id(request)
        if user_id is None:
            return _webapp_denied(403, "invalid_init_data")
        db = _webapp_database()
        if db is None:
            return _webapp_denied(503, "bot_not_ready")
        global_enabled = await db.get_state("contact_global_enabled") != "0"
        if not global_enabled or not await db.user_contact_enabled(user_id):
            return _webapp_denied(403, "contact_unavailable")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_CHAT_ATTACHMENT_BYTES + 512 * 1024:
                    return _webapp_denied(413, "attachment_too_large")
            except ValueError:
                return _webapp_denied(400, "bad_content_length")
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة مرفق محادثة المستخدم")
            return _webapp_denied(400, "invalid_multipart")
        upload = form.get("file")
        if not hasattr(upload, "read"):
            return _webapp_denied(400, "attachment_required")
        filename = sanitize_attachment_filename(getattr(upload, "filename", None))
        try:
            data = await upload.read(MAX_CHAT_ATTACHMENT_BYTES + 1)
        finally:
            await upload.close()
        if len(data) > MAX_CHAT_ATTACHMENT_BYTES:
            return _webapp_denied(413, "attachment_too_large")
        detected = validate_chat_attachment(data, filename)
        if detected is None:
            return _webapp_denied(415, "unsupported_attachment")
        media_type, content_type = detected
        caption = str(form.get("text") or "").strip()
        if len(caption) > 1000:
            return _webapp_denied(400, "caption_too_long")
        await db.open_user_conversation(user_id)
        message_id = await db.add_user_message(
            user_id,
            "in",
            caption or f"[مرفق: {filename}]",
            delivery_status="received",
            media_type=media_type,
            filename=filename,
            attachment_data=data,
            attachment_content_type=content_type,
        )
        return JSONResponse(
            {"ok": True, "message_id": message_id},
            headers={"Cache-Control": "no-store"},
        )

    # 3) مسار الإعداد لمرة واحدة: تطبيق بايو البوت من السبيس مباشرة.
    #    • محمي بمفتاح SETUP_TOKEN من أسرار السبيس — إن غاب فالمسار مغلق.
    #    • النصان يُقرآن من الكود المعتمد (texts.py)؛ المسار لا يقبل أي نص خارجي.
    #    • آمن للتكرار، ويعيد قراءة النتيجة من تلغرام نفسها للتأكد.
    @app.post("/api/set-bio")
    async def api_set_bio(request: Request) -> JSONResponse:
        expected = os.getenv("SETUP_TOKEN", "").strip()
        if not expected:
            return JSONResponse(
                {"ok": False, "error": "setup_token_not_configured"}, status_code=503
            )
        got = (request.headers.get("X-Setup-Token") or "").strip()
        if not hmac.compare_digest(got, expected):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

        token = BOT_TOKEN.strip()
        if not token:
            return JSONResponse({"ok": False, "error": "no_bot_token"}, status_code=503)

        full, short = texts.BOT_DESCRIPTION, texts.BOT_SHORT_DESCRIPTION
        if len(full) > 512 or len(short) > 120:
            return JSONResponse({"ok": False, "error": "text_too_long"}, status_code=500)

        try:
            from telegram import Bot

            tg = Bot(token=token)
            await tg.set_my_description(description=full)
            await tg.set_my_short_description(short_description=short)
            got_full = (await tg.get_my_description()).description or ""
            got_short = (await tg.get_my_short_description()).short_description or ""
        except Exception as exc:  # noqa: BLE001
            logger.exception("فشل تطبيق البايو من السبيس")
            return JSONResponse(
                {"ok": False, "error": "telegram_error"},
                status_code=502,
            )

        return JSONResponse({
            "ok": (got_full.strip() == full and got_short.strip() == short),
            "description_now": got_full,
            "short_description_now": got_short,
        })

    # 4) صفحة حالة خفيفة (بديل Gradio — لا تحتاج Node proxy)
    @app.get("/status", response_class=HTMLResponse)
    async def status() -> HTMLResponse:
        minutes = int((time.time() - START_TIME) / 60)
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>حالة النظام</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1b16;
 color:#eef6f1;font-family:"Segoe UI",Tahoma,sans-serif;line-height:1.8}}
 .card{{background:#163026;border:1px solid #1f4436;border-radius:18px;padding:32px 36px;
 max-width:420px;text-align:center;box-shadow:0 10px 40px rgba(0,0,0,.45)}}
 h1{{color:#f0c674;font-size:20px;margin:0 0 14px}}
 p{{color:#9ab8aa;margin:6px 0}}
 .ok{{color:#3fa66b;font-weight:700}}
 a{{color:#f0c674}}
</style></head><body>
<div class="card">
  <h1>🛡️ نظام بلاغات المخالفات العسكرية</h1>
  <p class="ok">✅ البوت يعمل الآن داخل المساحة السحابية</p>
  <p>⏱️ مدة التشغيل: {minutes} دقيقة</p>
  <p>📨 البوت: {BOT_USERNAME}</p>
  <p><a href="/app/">فتح تطبيق إرسال البلاغ</a></p>
</div></body></html>""")

    # 5) واجهة التحكم الإدارية (Admin API) — نقاط محمية للمشرف، تُربط قبل
    #    ربط الملفات الثابتة حتى لا تحجبها.
    admin_enabled = os.getenv("ADMIN_WEB_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    if admin_enabled:
        admin_api.register(app)
        admin_dashboard.register_dashboard(app)
        logger.info("🛡️ واجهة التحكم الإدارية جاهزة (+ADMIN_API_TOKEN)")

        # 6) لوحة الإدارة (Flutter وب) على مسار فرعي خاص — قبل جذر التطبيق
        ADMIN_WEB_DIR = BASE_DIR / "admin_web" / "dist"
        if ADMIN_WEB_DIR.is_dir():
            app.mount(
                "/admin",
                StaticFiles(directory=str(ADMIN_WEB_DIR), html=True),
                name="adminweb",
            )
            logger.info("🖥️ لوحة الإدارة (Flutter) متاحة على /admin")
        else:
            logger.warning("⚠️ لم يُعثر على بناء لوحة الإدارة (admin_web/dist)")
    else:
        logger.info("لوحة الإدارة معطّلة (ADMIN_WEB_ENABLED=0)")

    # 7) تطبيق البلاغ (ملفات ثابتة) — يُربط أخيرًا حتى لا يحجب المسارات الأخرى
    if WEBAPP_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
        logger.info("🧩 تطبيق البلاغ متاح على المسار /")
    else:
        logger.warning("لم يُعثر على مجلد webapp — التطبيق غير متاح")

    return app


def _wait_for_bot(seconds: float = 60.0) -> bool:
    """ينتظر جهوزية تطبيق البوت (وقاعدة البيانات) قبل فتح خادم الويب.

    بدون هذا الانتظار قد تصل أول طلبات لوحة الإدارة قبل أن يضبط البوت
    `main.app` و `bot_data["db"]`، فترد 500 رغم أن كل شيء سليم.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        application = getattr(bot_main, "app", None)
        if application is not None and application.bot_data.get("db") is not None:
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    import uvicorn

    # تشغيل البوت في الخلفية عند إقلاع المساحة
    _BOT_STOP_EVENT.clear()
    threading.Thread(target=_run_bot, daemon=True, name="bot-thread").start()
    if _wait_for_bot():
        logger.info("✅ البوت وقاعدة البيانات جاهزان — فتح لوحة الإدارة الآن")
    else:
        logger.warning("⚠️ البوت لم يجهز خلال 60 ثانية — سنفتح الخادم على أي حال")

    port = int(os.getenv("PORT", "7860") or 7860)
    _zerogpu_startup_report()
    logger.info("🚀 تشغيل خادم الويب على المنفذ %d", port)
    uvicorn.run(build_app(), host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
