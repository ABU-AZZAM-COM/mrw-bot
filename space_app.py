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

import logging
import os
import threading
import time
from pathlib import Path

# إيقاف خادم الصحة قبل استيراد main (نفس المنفذ سيخدم التطبيق)
os.environ["DISABLE_HEALTH_SERVER"] = "1"

from fastapi import FastAPI  # noqa: E402
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("space")

BOT_USERNAME = os.getenv("BOT_USERNAME", "@blgh_bot")
START_TIME = time.time()

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"


def _run_bot() -> None:
    """يشغّل البوت في خيط خلفي (يستقبل الرسائل من تلغرام)."""
    try:
        bot_main.main()
    except Exception:
        logger.exception("توقف البوت داخل المساحة السحابية")


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


def build_app() -> FastAPI:
    """يبني تطبيق الويب: فحص + صفحة حالة + ملفات التطبيق."""
    app = FastAPI(title="نظام بلاغات المخالفات العسكرية", docs_url=None, redoc_url=None)

    # 1) فحص سريع للمنصة
    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "uptime_seconds": int(time.time() - START_TIME),
            "bot": BOT_USERNAME,
        })

    # 2) صفحة حالة خفيفة (بديل Gradio — لا تحتاج Node proxy)
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

    # 3) تطبيق البلاغ (ملفات ثابتة) — يجب أن يكون آخر مسار montaged
    if WEBAPP_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
        logger.info("🧩 تطبيق البلاغ متاح على المسار /")
    else:
        logger.warning("لم يُعثر على مجلد webapp — التطبيق غير متاح")

    return app


if __name__ == "__main__":
    import uvicorn

    # تشغيل البوت في الخلفية عند إقلاع المساحة
    threading.Thread(target=_run_bot, daemon=True, name="bot-thread").start()

    port = int(os.getenv("PORT", "7860") or 7860)
    _zerogpu_startup_report()
    logger.info("🚀 تشغيل خادم الويب على المنفذ %d", port)
    uvicorn.run(build_app(), host="0.0.0.0", port=port, log_level="warning")
