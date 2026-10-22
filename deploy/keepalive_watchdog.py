r"""
المراقب الخارجي: يمنع توقّف الخدمة السحابية ويوقظها عند الحاجة.

لماذا هذا الملف؟
المنصات المجانية (Hugging Face Spaces) تُوقف الحاوية بعد خمول طويل، والنبضة
الداخلية داخل البوت لا تنفع عندها لأن البوت نفسه يكون متوقفًا.
لذلك نحتاج مراقبًا خارجيًا (GitHub Actions) يعمل كل 10 دقائق ويقوم بـ:
  1) فحص حالة المساحة عبر واجهة Hugging Face الرسمية.
  2) إعادة تشغيل المساحة إذا كانت نائمة أو موقوفة.
  3) فحص رابط الخدمة (اختياري) وإرسال تنبيه للمشرف عند تعطّل الخدمة.

المتغيرات المطلوبة (أسرار GitHub Actions):
  HF_TOKEN   = رمز وصول (Write) من https://huggingface.co/settings/tokens
  HF_SPACE   = اسم-المستخدم/اسم-المساحة
  SERVICE_URL = رابط المساحة (اختياري — يفيد إذا كانت المساحة عامة)
  BOT_TOKEN  = توكن البوت (اختياري — لإرسال التنبيه)
  ADMIN_IDS  = معرّف المشرف (اختياري)

التشغيل:
  python deploy/keepalive_watchdog.py
"""

from __future__ import annotations

import os
import sys

from huggingface_hub import HfApi

# الحالات التي تعني أن المساحة متوقفة ويجب إيقاظها
SLEEPING_STAGES = {"SLEEPING", "PAUSED", "STOPPED", "BUILD_ERROR", "RUNTIME_ERROR"}


def notify_admin(message: str) -> None:
    """يرسل تنبيهًا للمشرف عبر بوت تلغرام (إن كانت البيانات متاحة)."""
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    admin_ids = os.getenv("ADMIN_IDS", "").strip()
    if not bot_token or not admin_ids:
        return
    first_admin = admin_ids.split(",")[0].strip()
    try:
        import httpx

        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": first_admin, "text": message},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"تعذر إرسال التنبيه: {exc}")


def main() -> int:
    token = os.getenv("HF_TOKEN", "").strip()
    space = os.getenv("HF_SPACE", "").strip()

    if not token or not space:
        print("⚠️ لم تُضبط HF_TOKEN أو HF_SPACE — لا حاجة لأي إجراء.")
        return 0

    api = HfApi(token=token)
    runtime = api.get_space_runtime(space)
    stage = runtime.stage
    print(f"حالة المساحة: {stage}")

    # 1) إيقاظ المساحة إن كانت متوقفة
    if stage in SLEEPING_STAGES:
        if stage in {"BUILD_ERROR", "RUNTIME_ERROR"}:
            print("⚠️ المساحة في حالة خطأ — إعادة التشغيل قد تفيد (تُصلَح بعد رفع الكود).")
        try:
            api.restart_space(space)
            print(f"▶ أُرسل طلب إعادة تشغيل المساحة (الحالة السابقة: {stage})")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ تعذّرت إعادة التشغيل: {exc}")
            notify_admin(f"⚠️ تعذّر تشغيل المساحة السحابية ({space}). الحالة: {stage}")
            return 1
    else:
        print("✅ المساحة نشطة — لا حاجة لأي إجراء.")

    # 2) فحص رابط الخدمة — مع رمز Hugging Face لأن المساحة خاصة
    #    (المساحة الخاصة تردّ 404 على الزائر غير المسجّل، و200 مع المصادقة).
    service_url = os.getenv("SERVICE_URL", "").strip()
    if service_url:
        try:
            import httpx

            request_headers = {"Authorization": f"Bearer {token}"}
            response = httpx.get(
                service_url,
                timeout=30,
                follow_redirects=True,
                headers=request_headers,
            )
            print(f"استجابة رابط الخدمة: {response.status_code}")
            if response.status_code >= 500:
                notify_admin(f"⚠️ الخدمة السحابية تردّ بالرمز {response.status_code}.")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ تعذّر الوصول إلى رابط الخدمة: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
