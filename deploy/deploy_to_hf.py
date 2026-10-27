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
      --bot-token "<BOT_TOKEN>" `
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
    # Root compatibility entry points keep existing launch commands working.
    "space_app.py": "space_app.py",
    "main.py": "main.py",
    "config.py": "config.py",
    "texts.py": "texts.py",
    "handlers.py": "handlers.py",
    "admin.py": "admin.py",
    "keyboards.py": "keyboards.py",
    "utils.py": "utils.py",
    "exporters.py": "exporters.py",
    "archive.py": "archive.py",
    "guards.py": "guards.py",          # طبقة الحماية (إساءة/كيّد/تخريب)
    "image_moderation.py": "image_moderation.py",
    "integrations_monitor.py": "integrations_monitor.py",
    "merge_intervals.py": "merge_intervals.py",
    "sightengine_moderation.py": "sightengine_moderation.py",
    "tracking_bot.py": "tracking_bot.py",
    "virustotal.py": "virustotal.py",
    "admin_api.py": "admin_api.py",
    "admin_dashboard.py": "admin_dashboard.py",
    "health_server.py": "health_server.py",
    "app/__init__.py": "app/__init__.py",
    "app/config.py": "app/config.py",
    "app/main.py": "app/main.py",
    "app/space_app.py": "app/space_app.py",
    "app/health_server.py": "app/health_server.py",
    "api/__init__.py": "api/__init__.py",
    "api/admin_api.py": "api/admin_api.py",
    "api/admin_dashboard.py": "api/admin_dashboard.py",
    "bot/__init__.py": "bot/__init__.py",
    "bot/admin.py": "bot/admin.py",
    "bot/handlers.py": "bot/handlers.py",
    "bot/keyboards.py": "bot/keyboards.py",
    "bot/texts.py": "bot/texts.py",
    "bot/tracking_bot.py": "bot/tracking_bot.py",
    "services/__init__.py": "services/__init__.py",
    "services/archive.py": "services/archive.py",
    "services/exporters.py": "services/exporters.py",
    "services/guards.py": "services/guards.py",
    "services/hub_backup.py": "services/hub_backup.py",
    "services/image_moderation.py": "services/image_moderation.py",
    "services/integrations_monitor.py": "services/integrations_monitor.py",
    "services/merge_intervals.py": "services/merge_intervals.py",
    "services/sightengine_moderation.py": "services/sightengine_moderation.py",
    "services/utils.py": "services/utils.py",
    "services/virustotal.py": "services/virustotal.py",
    "database/__init__.py": "database/__init__.py",
    # Hugging Face يقرأ الاعتماديات من requirements.txt فقط
    "requirements-space.txt": "requirements.txt",
    "requirements-nsfw.txt": "requirements-nsfw.txt",
    # التطبيق (Mini App) — نسخة داخل مستودع المساحة أيضًا
    "webapp/index.html": "webapp/index.html",
    "webapp/app.js": "webapp/app.js",
    "webapp/styles.css": "webapp/styles.css",
    "webapp/manifest.webmanifest": "webapp/manifest.webmanifest",
    "webapp/icon.svg": "webapp/icon.svg",
    "webapp/icon.png": "webapp/icon.png",
    "webapp/icon-192.png": "webapp/icon-192.png",
    "webapp/icon-512.png": "webapp/icon-512.png",
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
    parser.add_argument("--mini-app-url", default=os.getenv("MINI_APP_URL", ""),
                        help="رابط تطبيق البلاغ (GitHub Pages) — يُفعّل زر التطبيق")
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
    # ملاحظة: نستخدم الدوال المخصّصة (add_space_secret / add_space_variable)
    # لأن تمريرها داخل create_repo لا يُطبَّق على المساحات المُنشأة مسبقًا.
    secrets: dict[str, str] = {}
    variables: dict[str, str] = {
        "SAVE_MEDIA_LOCALLY": "0",
        "BACKUP_INTERVAL_MIN": "30",
        "KEEPALIVE_INTERVAL_SEC": "300",
        "LOCAL_UTC_OFFSET": "3",
        "BOT_USERNAME": args.bot_username,
    }
    if args.bot_token:
        secrets["BOT_TOKEN"] = args.bot_token
    if args.admin_ids:
        secrets["ADMIN_IDS"] = args.admin_ids
    if args.archive_chat_id:
        secrets["ARCHIVE_CHAT_ID"] = args.archive_chat_id
    if args.keepalive_url:
        variables["KEEPALIVE_URL"] = args.keepalive_url
    if args.mini_app_url:
        variables["MINI_APP_URL"] = args.mini_app_url

    if secrets or variables:
        print("▶ ضبط الأسرار والمتغيرات …")
        for key, value in secrets.items():
            api.add_space_secret(repo_id=args.space, key=key, value=value)
            print(f"   🔒 سر: {key}")
        for key, value in variables.items():
            api.add_space_variable(repo_id=args.space, key=key, value=value)
            print(f"   ⚙️ متغير: {key}")
        # تأكيد ما استُقبل فعلًا
        stored_secrets = list(api.get_space_secrets(args.space).keys())
        stored_variables = list(api.get_space_variables(args.space).keys())
        print(f"   ✅ الأسرار المخزّنة: {stored_secrets}")
        print(f"   ✅ المتغيرات المخزّنة: {stored_variables}")

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
