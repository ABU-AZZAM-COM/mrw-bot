"""
غلاف Gradio لتشغيل البوت داخل Hugging Face Space (خطة ZeroGPU المجانية).

لماذا هذا الملف؟
لأن Hugging Face سمحت (من 2026) بالحساب الشخصي المجاني بتشغيل
مساحتين من نوع ZeroGPU — وهي مساحات Gradio حرة للأبد، بشرط:
  - تفعيل البريد الإلكتروني،
  - أن يكون عمر الحساب 30 يومًا على الأقل.
والمساحة الحرة لا تنام: نبضة الإبقاء الذاتي (KEEPALIVE_URL) تبقيها نشطة.

طريقة العمل:
  1) هذا الملف يشغّل البوت في خيط خلفي (الخيط يقرأ الرسائل من تلغرام).
  2) صفحة Gradio مجرد واجهة بسيطة تُظهر حالة النظام (لا يشترط فتحها).
  3) المنصة تفحص الخدمة عبر المنفذ PORT — وGradio هو من يستمع عليه.

ملاحظة: خادم الصحة الداخلي (health_server) يُعطَّل هنا لأن Gradio يشغل
المنفذ نفسه، فتحميله الثاني على نفس المنفذ سيفشل.
"""
from __future__ import annotations

import logging
import os
import threading
import time

# إيقاف خادم الصحة قبل استيراد main (Gradio يستمع على نفس المنفذ)
os.environ["DISABLE_HEALTH_SERVER"] = "1"

import gradio as gr  # noqa: E402

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


def build_ui():
    """يبني صفحة الحالة البسيطة (لا علاقة لها بالوظيفة الأساسية)."""
    with gr.Blocks(title="نظام بلاغات المخالفات العسكرية") as demo:
        gr.Markdown(
            "# 🛡️ نظام بلاغات المخالفات العسكرية\n"
            "هذه لوحة حالة فقط. البوت يعمل في الخلفية ويستقبل البلاغات عبر تلغرام."
        )
        status_box = gr.Markdown(_status())
        refresh_button = gr.Button("تحديث الحالة")
        # ربط الزر بدالة تحديث الحالة (توافق مع Gradio 4 و5)
        refresh_button.click(fn=_status, outputs=status_box)
    return demo


# تشغيل البوت في الخلفية عند إقلاع المساحة
threading.Thread(target=_run_bot, daemon=True, name="bot-thread").start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860") or 7860)
    build_ui().launch(
        server_name="0.0.0.0",
        server_port=port,
        show_error=False,
    )
