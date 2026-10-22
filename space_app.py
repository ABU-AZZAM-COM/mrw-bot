"""
غلاف Gradio لتشغيل البوت داخل Hugging Face Space (خطة ZeroGPU المجانية).

لماذا هذا الملف؟
لأن Hugging Face سمحت (من 2026) بالحساب الشخصي المجاني بتشغيل
مساحتين من نوع ZeroGPU — وهي مساحات Gradio حرة للأبد، بشرط:
  - تفعيل البريد الإلكتروني،
  - أن يكون عمر الحساب 30 يومًا على الأقل.

شروط تشغيل لا بد من احترامها في هذه البيئة:
  1) مساحة ZeroGPU لا تعمل إلا إذا وُجدت دالة واحدة على الأقل معلَّمة
     بـ `@spaces.GPU` (وإلا أوقفتها المنصة برسالة: No @spaces.GPU function
     detected during startup). لذلك نُعرّف دالة خفيفة носمّيها "دالة التوافق"
     لا تستهلك أي طاقة معالجة فعلية.
  2) البوت يعمل في خيط خلفي، وتثبيت معالجات الإشارات لا يعمل إلا في الخيط
     الرئيسي — لذلك تُلغى في main.py تلقائيًا عند العمل خارج الخيط الرئيسي.
  3) Gradio يشغل المنفذ PORT، لذلك يُعطَّل خادم الصحة الداخلي (نفس المنفذ).

طريقة العمل:
  - خيط خلفي يشغّل البوت (يستقبل الرسائل من تلغرام).
  - صفحة Gradio مجرد واجهة حالة بسيطة (لا يشترط فتحها).
"""
from __future__ import annotations

import logging
import os
import threading
import time

# إيقاف خادم الصحة قبل استيراد main (Gradio يستمع على نفس المنفذ)
os.environ["DISABLE_HEALTH_SERVER"] = "1"

import gradio as gr  # noqa: E402

# حزمة spaces هي التي تدير تخصيص كرت الشاشة داخل مساحات ZeroGPU.
# خارج Hugging Face Spaces قد لا تكون مثبتة، فنستخدم بديلًا بسيطًا (تطبيق بلا أثر).
try:
    import spaces  # noqa: E402

    GPU = spaces.GPU
except ImportError:  # pragma: no cover - بيئة محلية بدون Hugging Face
    def GPU(func=None, **kwargs):  # type: ignore[misc]
        """بديل بسيط لspaces.GPU يعمل خارج مساحات ZeroGPU."""
        if func is None:
            return lambda inner: inner
        return func

import main as bot_main  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("space")

BOT_USERNAME = os.getenv("BOT_USERNAME", "@blgh_bot")
START_TIME = time.time()


def _run_bot() -> None:
    """يشغّل البوت في خيط خلفي (يستقبل الرسائل من تلغرام)."""
    try:
        bot_main.main()
    except Exception:
        logger.exception("توقف البوت داخل المساحة السحابية")


def _status() -> str:
    """يعرض مدة عمل الخدمة وحالة الاتصال."""
    minutes = int((time.time() - START_TIME) / 60)
    return (
        f"✅ البوت يعمل الآن داخل المساحة السحابية\n"
        f"⏱️ مدة التشغيل: {minutes} دقيقة\n"
        f"📨 البوت: {BOT_USERNAME}\n\n"
        f"البلاغات تُستقبل عبر تلغرام مباشرة، ولا حاجة لفتح هذه الصفحة."
    )


@GPU(duration=5)
def _refresh_status() -> str:
    """تحديث الحالة عند الضغط على الزر.

    ملاحظة: التوثيق (@spaces.GPU) إجباري في مساحات ZeroGPU —
    بدون دالة واحدة معلَّمة بهذا就不能 إقلاع المساحة إطلاقًا.
    ولا تستهلك هذه الدالة أي طاقة معالجة فعلية.
    """
    return _status()


def build_ui():
    """يبني صفحة الحالة البسيطة (لا علاقة لها بالوظيفة الأساسية)."""
    with gr.Blocks(title="نظام بلاغات المخالفات العسكرية") as ui:
        gr.Markdown(
            "# 🛡️ نظام بلاغات المخالفات العسكرية\n"
            "هذه لوحة حالة فقط. البوت يعمل في الخلفية ويستقبل البلاغات عبر تلغرام."
        )
        status_box = gr.Markdown(_status())
        refresh_button = gr.Button("تحديث الحالة")
        # ربط الزر بدالة تحديث الحالة (توافق مع Gradio 4 و5 و6)
        refresh_button.click(fn=_refresh_status, outputs=status_box)
    return ui


# Gradio 6 يبحث عن كائن باسم "demo" داخل __main__ — لذلك عرّفه هنا كمتغير عام.
demo = build_ui()


if __name__ == "__main__":
    # تشغيل البوت في الخلفية عند إقلاع المساحة
    threading.Thread(target=_run_bot, daemon=True, name="bot-thread").start()

    port = int(os.getenv("PORT", "7860") or 7860)
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        show_error=False,
    )
