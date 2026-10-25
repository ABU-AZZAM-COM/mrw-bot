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
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
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

import main as bot_main  # noqa: E402
import admin_api  # noqa: E402
import texts  # noqa: E402
import config  # noqa: E402
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("space")

BOT_USERNAME = os.getenv("BOT_USERNAME", "@blgh_bot")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
START_TIME = time.time()

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"

# مهلة قبول بيانات التطبيق (تيليغرام يوقّع initData بتاريخ إصدار)
INIT_DATA_MAX_AGE = 24 * 60 * 60


def _run_bot() -> None:
    """يشغّل البوت في خيط خلفي (يستقبل الرسائل من تلغرام)."""
    try:
        bot_main.main()
    except Exception as exc:
        logger.error("توقف البوت داخل المساحة السحابية (%s)", type(exc).__name__)


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


def _verify_init_data(init_data: str, bot_token: str) -> dict | None:
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
    if auth_date <= 0 or (time.time() - auth_date) > INIT_DATA_MAX_AGE:
        logger.warning("رفض بيانات تطبيق: توقيع منتهي الصلاحية")
        return None

    try:
        user = json.loads(fields.get("user", "{}"))
    except Exception:  # noqa: BLE001
        return None
    return user if isinstance(user, dict) else None


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["x-admin-token", "content-type"],
    )

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
        return JSONResponse({
            "status": "ok",
            "uptime_seconds": int(time.time() - START_TIME),
            "bot": BOT_USERNAME,
        })

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
                "max_desc_len": config.MAX_DESC_LEN,
                "min_desc_len": config.MIN_DESC_LEN,
                "min_desc_words": config.MIN_DESC_WORDS,
                "max_photo_mb": config.MAX_PHOTO_MB,
            },
            headers={"Cache-Control": "no-store"},
        )

    # 3) مسار الإعداد لمرة واحدة: تطبيق بايو البوت من السبيس مباشرة.
    #    • محمي بمفتاح SETUP_TOKEN من أسرار السبيس — إن غاب فالمسار مغلق.
    #    • النصان يُقرآن من الكود المعتمد (texts.py)؛ المسار لا يقبل أي نص خارجي.
    #    • آمن للتكرار، ويعيد قراءة النتيجة من تلغرام نفسها للتأكد.
    @app.get("/api/set-bio")
    async def api_set_bio(request: Request) -> JSONResponse:
        expected = os.getenv("SETUP_TOKEN", "").strip()
        if not expected:
            return JSONResponse(
                {"ok": False, "error": "setup_token_not_configured"}, status_code=503
            )
        got = (request.query_params.get("key") or "").strip()
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
                {"ok": False, "error": "telegram_error", "detail": str(exc)},
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
