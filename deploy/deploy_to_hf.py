r"""
نشر المشروع على Hugging Face Space (خطة ZeroGPU المجانية) — آليًا بالكامل.

ما يفعله هذا السكربت:
  1) ينشئ المساحة (Private) بنوع Gradio على عتاد ZeroGPU إن لم تكن موجودة.
  2) يرفع كل ملفات البوت إلى المساحة (مع تحويل requirements-space.txt إلى requirements.txt
     لأن Hugging Face يقرأ الاعتماديات من ملف requirements.txt فقط).
  3) يرفع بطاقة المساحة (README.md) الجاهزة من deploy/huggingface/README.md.
  4) يضبط الأسرار (Secrets) والمتغيرات (Variables) داخل المساحة من سطر الأوامر.

طريقة الاستخدام (PowerShell):
  # 1) أنشئ رمز وصول (Write token) من:
  #    https://huggingface.co/settings/tokens  ثم ضعه في متغير البيئة:
  $env:HF_TOKEN = "hf_xxxxxxxxxxxx"
  $env:HF_SPACE = "اسم-المستخدم/mrw-bot"

  # 2) شغّل السكربت (يمكن تمرير الأسرار اختياريًا):
  .\.venv\Scripts\python.exe deploy\deploy_to_hf.py `
      --archive-chat-id "-1001234567890" `
      --bot-token "8860507439:AAFvla8GegpsJl5ZNbdhIop5k0GmJyeApYE" `
      --admin-ids "1557513643"

ملاحظة مهمة:
  - خيار `--archive-chat-id` اختياري؛ إن لم يتوفر المعرّف بعد، اضبطه لاحقًا من
    صفحة المساحة ← Settings ← Variables and secrets.
  - لا تُرفع قاعدة البيانات ولا الصور ولا التوكنات إلى Git — الأسرار تُضبط كـ Secrets داخل المساحة.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub._space_api import SpaceHardware

# جذر المشروع (المجلد الذي يحوي هذا الملف و main.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# الملفات المطلوبة داخل المساحة (المصدر: الوجهة)
UPLOAD_MAP: dict[str, str] = {
    "space_app.py": "space_app.py",
    "main.py": "main.py",
    "config.py": "config.py",
    "texts.py": "texts.py",
    "handlers.py": "handlers.py",
    "admin.py": "admin.py",
    "database.py": "database.py",
    "keyboards.py": "keyboards.py",
    "utils.py": "utils.py",
    "exporters.py": "exporters.py",
    "archive.py": "archive.py",
    "health_server.py": "health_server.py",
    # Hugging Face يقرأ الاعتماديات من requirements.txt فقط
    "requirements-space.txt": "requirements.txt",
}

# بطاقة المساحة (ترويسة YAML التي تعرّف المساحة)
SPACE_CARD = Path(__file__).resolve().parent / "huggingface" / "README.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="نشر البوت على Hugging Face Space")
    parser.add_argument("--space", default=os.getenv("HF_SPACE", ""), help="معرّف المساحة: اسم-المستخدم/اسم-المساحة")
    parser.add_argument("--token", default=os.getenv("HF_TOKEN", ""), help="رمز الوصول (Write) من Hugging Face")
    parser.add_argument("--bot-token", default=os.getenv("BOT_TOKEN", ""), help="توكن بوت تلغرام (سرّي)")
    parser.add_argument("--admin-ids", default=os.getenv("ADMIN_IDS", ""), help="معرّفات المشرفين")
    parser.add_argument("--archive-chat-id", default=os.getenv("ARCHIVE_CHAT_ID", ""), help="معرّف القناة الرسمية")
    parser.add_argument("--bot-username", default=os.getenv("BOT_USERNAME", "@blgh_bot"), help="اسم البوت للعرض")
    parser.add_argument("--keepalive-url", default=os.getenv("KEEPALIVE_URL", ""), help="رابط المساحة العامة")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.space or "/" not in args.space:
        print("❌ يجب تمرير --space بالشكل: اسم-المستخدم/اسم-المساحة")
        return 1
    if not args.token:
        print("❌ يجب تمرير --token (أو ضبط HF_TOKEN) — أنشئه من https://huggingface.co/settings/tokens")
        return 1

    api = HfApi(token=args.token)

    # 1) إنشاء المساحة (إن لم تكن موجودة)
    print(f"▶ إنشاء/فحص المساحة: {args.space}")
    try:
        api.create_repo(
            repo_id=args.space,
            repo_type="space",
            space_sdk="gradio",
            space_hardware=SpaceHardware.ZERO_A10G,
            private=True,
            exist_ok=True,
        )
        print("✅ المساحة جاهزة (Gradio + ZeroGPU + Private)")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ تعذّر إنشاء المساحة تلقائيًا: {exc}")
        print("   → افتح https://huggingface.co/spaces وأنشئها يدويًا (Gradio + Private)، ثم أعد تشغيل السكربت.")

    # 2) رفع ملفات البوت
    print("▶ رفع ملفات البوت …")
    for source_name, target_name in UPLOAD_MAP.items():
        source_path = PROJECT_ROOT / source_name
        if not source_path.exists():
            print(f"   ⚠️ ملف مفقود: {source_name} — تم تجاوزه")
            continue
        api.upload_file(
            path_or_fileobj=str(source_path),
            path_in_repo=target_name,
            repo_id=args.space,
            repo_type="space",
            commit_message="رفع ملفات النظام",
        )
        print(f"   ✅ {source_name} → {target_name}")

    # 3) رفع بطاقة المساحة
    if SPACE_CARD.exists():
        api.upload_file(
            path_or_fileobj=str(SPACE_CARD),
            path_in_repo="README.md",
            repo_id=args.space,
            repo_type="space",
            commit_message="بطاقة المساحة",
        )
        print("   ✅ بطاقة المساحة (README.md)")

    # 4) ضبط الأسرار والمتغيرات
    secrets: list[dict[str, str]] = []
    variables: list[dict[str, str]] = [
        {"key": "SAVE_MEDIA_LOCALLY", "value": "0"},
        {"key": "BACKUP_INTERVAL_MIN", "value": "30"},
        {"key": "KEEPALIVE_INTERVAL_SEC", "value": "300"},
        {"key": "LOCAL_UTC_OFFSET", "value": "3"},
        {"key": "BOT_USERNAME", "value": args.bot_username},
    ]
    if args.bot_token:
        secrets.append({"key": "BOT_TOKEN", "value": args.bot_token})
    if args.admin_ids:
        secrets.append({"key": "ADMIN_IDS", "value": args.admin_ids})
    if args.archive_chat_id:
        secrets.append({"key": "ARCHIVE_CHAT_ID", "value": args.archive_chat_id})
    if args.keepalive_url:
        variables.append({"key": "KEEPALIVE_URL", "value": args.keepalive_url})

    if secrets or variables:
        print("▶ ضبط الأسرار والمتغيرات …")
        api.create_repo(
            repo_id=args.space,
            repo_type="space",
            space_sdk="gradio",
            space_hardware=SpaceHardware.ZERO_A10G,
            private=True,
            exist_ok=True,
            space_secrets=secrets,
            space_variables=variables,
        )
        print(f"   ✅ أسرار: {[s['key'] for s in secrets]}")
        print(f"   ✅ متغيرات: {[v['key'] for v in variables]}")

    url = f"https://{args.space.replace('/', '-')}.hf.space"
    print("\n🎉 اكتمل النشر بنجاح")
    print(f"   رابط المساحة: {url}")
    print("   الخطوة التالية:")
    print("   1) افتح تبويب Logs وتأكد من ظهور: ✅ البوت جاهز")
    print("   2) أرسل /start للبوت وجرّب بلاغًا تجريبيًا")
    print("   3) أضف المراقب الخارجي: .github/workflows/keepalive.yml مع سر SERVICE_URL")
    print(f"      (القيمة: {url})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
