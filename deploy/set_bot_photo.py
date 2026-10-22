r"""
ضع صورة البوت (الشعار) على حساب البوت في تلغرام — بأمر واحد.

ما يفعله:
  1) يتحقق أن الصورة مربّعة ويحوّلها إلى 1024×1024 (قصّ مركزي) إن لزم.
  2) يضغطها إلى PNG تحت 1 ميجابايت (شرط تلغرام لصورة البوت).
  3) يرفعها عبر setMyProfilePhoto.
  4) يحفظ نسخة 512×512 للاستخدام ك-anyقة التطبيق (webapp/icon.png).

الاستخدام:
  # ضع الصورة في أي مكان ثم:
  $env:BOT_TOKEN = "..."
  .\.venv\Scripts\python.exe deploy\set_bot_photo.py --path "D:\Downloads\emblem.png"

ملاحظة: لا يُرفع أي شيء إلى Git (الصورة في data/ مستثناة من .gitignore).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from PIL import Image

from telegram import Bot, InputProfilePhotoStatic

MAX_BYTES = 1024 * 1024        # حد تلغرام لصورة البوت (1 ميجابايت)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="تعيين صورة البوت")
    parser.add_argument("--path", required=True, help="مسار ملف الصورة (PNG/JPG)")
    parser.add_argument("--size", type=int, default=1024, help="مقاس الحافة (1024 افتراضيًا)")
    parser.add_argument("--icon", default="webapp/icon.png", help="مسار حفظ أيقونة التطبيق")
    parser.add_argument("--no-icon", action="store_true", help="عدم توليد أيقونة التطبيق")
    return parser.parse_args()


def prepare(source: Path, size: int) -> tuple[bytes, Path]:
    """يحوّل الصورة إلى PNG مربّع مضغوط تحت الحد، ويعيد (البايتات، المسار)."""
    image = Image.open(source).convert("RGB")

    # قصّ مركزي للوصول إلى مربّع
    width, height = image.size
    if width != height:
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        image = image.crop((left, top, left + side, top + side))
        print(f"✂️  تم القصّ المركزي: {width}×{height} → {side}×{side}")

    image = image.resize((size, size), Image.LANCZOS)

    out = Path("data") / f"bot_photo_{size}.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    # ضغط تدريجي حتىduc الوصول تحت 1 ميجابايت
    quality = 95
    while True:
        image.save(out, format="PNG", optimize=True)
        data = out.read_bytes()
        if len(data) <= MAX_BYTES or quality <= 40:
            break
        quality -= 10
        image.save(out, format="PNG", optimize=True, compress_level=9)
        data = out.read_bytes()

    print(f"📐 المقاس: {size}×{size} | الحجم: {len(data) / 1024:.0f} كيلوبايت | المسار: {out}")
    if len(data) > MAX_BYTES:
        print("⚠️ الصورة ما زالت أكبر من 1 ميجابايت — جرّب مقاسًا أصغر (--size 512).")
    return data, out


async def main() -> int:
    args = parse_args()
    source = Path(args.path)
    if not source.exists():
        print(f"❌ الملف غير موجود: {source}")
        return 1

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        print("❌ BOT_TOKEN غير مضبوط")
        return 1

    data, out_path = prepare(source, args.size)

    # نسخة أصغر لأيقونة التطبيق
    if not args.no_icon:
        try:
            icon = Image.open(source).convert("RGBA")
            width, height = icon.size
            if width != height:
                side = min(width, height)
                icon = icon.crop(
                    ((width - side) // 2, (height - side) // 2,
                     (width - side) // 2 + side, (height - side) // 2 + side)
                )
            icon = icon.resize((512, 512), Image.LANCZOS)
            icon_path = Path(args.icon)
            icon_path.parent.mkdir(parents=True, exist_ok=True)
            icon.save(icon_path, format="PNG", optimize=True)
            print(f"🖼️  حُفظت أيقونة التطبيق: {icon_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ تعذّر توليد أيقونة التطبيق: {exc}")

    bot = Bot(token)
    try:
        with open(out_path, "rb") as handle:
            ok = await bot.set_my_profile_photo(
                photo=InputProfilePhotoStatic(photo=handle)
            )
    except Exception as exc:  # noqa: BLE001
        print(f"❌ فشل تعيين الصورة: {exc}")
        return 1

    if ok:
        print("✅ تم تعيين صورة البوت بنجاح")
        me = await bot.get_me()
        print(f"   البوت: @{me.username} — {me.first_name}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
