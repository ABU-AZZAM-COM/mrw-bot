r"""
تطبيق بايو البوت (الوصف والوصف المختصر) على حساب البوت في تلغرام — بأمر واحد.

هذه أداة لمرة واحدة تشغّلها يدويًا متى شئت تحديث البايو؛
البوت نفسه لا يضبط أي وصف عند التشغيل (قرار مطور المشروع).

ما يفعله:
  1) يقرأ التوكن من ملف .env بمجلد المشروع (لا يطبع التوكن أبدًا).
  2) يقرأ النص المعتمد من texts.py:
       BOT_SHORT_DESCRIPTION → البايو القصير (About، الحد 120 حرفًا)
       BOT_DESCRIPTION       → الوصف الكامل (Description، الحد 512 حرفًا)
  3) يرسلهما إلى تلغرام عبر setMyShortDescription و setMyDescription في معرفة
     اللغة الافتراضية (بدون لغة محددة).
  4) يعيد قراءتهما من تلغرام ويعرضهما نصًا للتأكد من نجاح التطبيق.

الأوضاع:
  (بدون وسائط)   تطبيق النصين ثم التحقق منهما بالقراءة من تلغرام.
  --check        فحص فقط: يقرأ الوصفَين الحاليَين من تلغرام دون أي تغيير.

الاستخدام:
  .\.venv\Scripts\python.exe deploy\set_bot_bio.py            # تطبيق + تحقق
  .\.venv\Scripts\python.exe deploy\set_bot_bio.py --check    # قراءة فقط دون تغيير

ملاحظة: في @BotFather يكافئ هذا:
  /setabouttext   → البايو القصير
  /setdescription → الوصف الكامل
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import texts  # noqa: E402

SHORT_LIMIT = 120   # حد تلغرام للوصف المختصر (البايو)
FULL_LIMIT = 512    # حد تلغرام للوصف الكامل


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="تطبيق بايو البوت على تلغرام")
    parser.add_argument(
        "--check", action="store_true",
        help="قراءة الوصفين الحاليَين من تلغرام فقط، دون أي تغيير",
    )
    return parser.parse_args()


def load_token() -> str:
    """يقرأ BOT_TOKEN من .env بمجلد المشروع، أو من متغير البيئة احتياطًا."""
    env_path = BASE_DIR / ".env"
    if env_path.is_file():
        # utf-8-sig لاستيعاب BOM الذي تضيفه أدوات ويندوز أحيانًا
        for raw in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key == "BOT_TOKEN" and value and not value.startswith("ضع_هنا"):
                return value
    return os.getenv("BOT_TOKEN", "").strip()


async def read_current(bot) -> tuple[str, str]:
    """يقرأ الوصفَين المعتمَدَين حاليًا من تلغرام."""
    full = await bot.get_my_description()
    short = await bot.get_my_short_description()
    return (full.description or "").strip(), (short.short_description or "").strip()


async def main() -> int:
    args = parse_args()
    token = load_token()
    if not token:
        print("✖ لا يوجد BOT_TOKEN صالح في ملف .env بمجلد المشروع.")
        print("  انسخ .env.example إلى .env وضع التوكن من @BotFather ثم أعد المحاولة.")
        return 1

    from telegram import Bot  # استيراد متأخر: أسرع عند فشل التوكن/الفحص
    bot = Bot(token=token)

    if args.check:
        full, short = await read_current(bot)
        print("📄 الوصف الكامل الحالي في تلغرام (")
        print(full or "—")
        print("📄 البايو القصير الحالي (")
        print(short or "—")
        return 0

    full, short = texts.BOT_DESCRIPTION, texts.BOT_SHORT_DESCRIPTION
    if len(full) > FULL_LIMIT:
        print(f"✖ الوصف الكامل أطول من حد تلغرام ({len(full)}/{FULL_LIMIT}).")
        return 1
    if len(short) > SHORT_LIMIT:
        print(f"✖ البايو القصير أطول من حد تلغرام ({len(short)}/{SHORT_LIMIT}).")
        return 1

    await bot.set_my_description(description=full)
    await bot.set_my_short_description(description=short)
    print("✔ أُرسلت النصوص إلى تلغرام، الآن تحقق بالقراءة من المصدر…")

    got_full, got_short = await read_current(bot)
    ok = (got_full == full.strip()) and (got_short == short.strip())
    print("📄 الوصف الكامل الآن:")
    print(got_full or "—")
    print("📄 البايو القصير الآن:")
    print(got_short or "—")
    if ok:
        print("✅ التطابق تام — البايو موسوم بنجاح. افتح بروفايل البوت لتراه.")
        return 0
    print("⚠️ ما وُجد قد يختلف قليلًا عن المرسل (تأخر مزامنة تلغرام) — راجع النص أعلاه.")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nمتوقف باليد.")
        sys.exit(130)
