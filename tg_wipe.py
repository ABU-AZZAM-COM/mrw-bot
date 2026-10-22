"""
تنظيف شامل (Full Wipe) للحساب — حالة "مولود جديد".

مراحل التنفيذ (كل واحدة مُسجّلة بالتفصيل في data/wipe_log.txt):
  1) مصفوفة جرد قبل التنظيف → data/inventory_before.txt
  2) حذف/مغادرة كل الديالوغات (مجموعات + قنوات + محادثات خاصة) مع حذف السجل (revoke)
  3) حذف جميع جهات الاتصال المحفوظة
  4) حذف كل صور الحساب + تصفير السيرة الذاتية (bio)
  5) تسجيل الجرد بعد التنظيف → data/inventory_after.txt
  6) إنهاء كل الجلسات النشطة على الأجهزة الأخرى (اختيارية متقدمة)

التراجع غير ممكن بعد التنفيذ — تم تفويض هذا السيناريو من صاحب الحساب.
"""
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, errors, functions

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
LOG_FILE = BASE_DIR / "data" / "wipe_log.txt"

load_dotenv(ENV_FILE)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def classify(dialog) -> str:
    if dialog.is_user:
        return "محادثة خاصة"
    if dialog.is_group or (dialog.is_channel and getattr(dialog.entity, "megagroup", False)):
        return "مجموعة"
    if dialog.is_channel:
        return "قناة"
    return "أخرى"


def dialog_title(dialog) -> str:
    return dialog.name or getattr(dialog.entity, "title", "") or "بدون عنوان"


async def inventory(client, label_path: Path) -> int:
    rows = []
    async for d in client.iter_dialogs(limit=None):
        kind = classify(d)
        title = d.name or getattr(d.entity, "title", "") or "بدون عنوان"
        users = getattr(d.entity, "participants_count", "-")
        rows.append(f"{kind} | {title} | id={d.id} | أعضاء={users}")
    with open(label_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(rows) or "(فارغ)")
    return len(rows)


async def wipe_dialogs(client) -> tuple[int, int, int]:
    dialogs = await client.get_dialogs(limit=None)
    total = len(dialogs)
    log(f"• المرحلة 2: حذف/مغادرة {total} ديلوغًا…")

    done, failed, saved_delete = 0, 0, 0
    for i, d in enumerate(dialogs, start=1):
        kind = classify(d)
        title = dialog_title(d)
        try:
            await d.delete(revoke=True)
            done += 1
            log(f"   [{i}/{total}] حُذف {kind}: {title}")
        except errors.FloodWaitError as exc:
            log(f"   ⏳ فلد سينتظر {exc.seconds} ث — أطوَل مرضية أُنام")
            await asyncio.sleep(exc.seconds + 2)
            try:
                await d.delete(revoke=True)
                done += 1
                log(f"   [{i}/{total}] حُذف بعد الانتظار: {title}")
            except Exception as exc2:  # noqa: BLE001
                failed += 1
                log(f"   ❌ فشل نهائي: {title} ({type(exc2).__name__})")
        except errors.ChannelPrivateError:
            failed += 1
            log(f"   ⚠️ قناة خاصة لا يمكن الخروج منها من السكربت (id={d.id})")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log(f"   ❌ {title}: {type(exc).__name__}: {exc}")
        await asyncio.sleep(0.35)

    log(f"• نتيجة المرحلة 2: نجح {done} | فشل {failed}")
    return done, failed, total


async def wipe_contacts(client) -> int:
    log("• المرحلة 3: حذف جهات الاتصال…")
    try:
        res = await client(functions.contacts.GetContactsRequest(hash=0))
        contacts = getattr(res, "contacts", [])
        if not contacts:
            log("   لا توجد جهات اتصال.")
            return 0
        inputs = []
        for c in contacts:
            try:
                inputs.append(await client.get_input_entity(c.user_id))
            except Exception:
                continue
        await client(functions.contacts.DeleteContactsRequest(id=inputs))
        log(f"   حُذفت {len(inputs)} جهة اتصال.")
        return len(inputs)
    except Exception as exc:  # noqa: BLE001
        log(f"   ❌ حذف جهات الاتصال فشل: {type(exc).__name__}: {exc}")
        return 0


async def wipe_profile(client) -> None:
    log("• المرحلة 4: صور الحساب والسيرة…")
    try:
        photos = await client.get_profile_photos("me", limit=None)
        if photos:
            for i, p in enumerate(photos, start=1):
                try:
                    await client(functions.photos.DeletePhotosRequest(id=[p]))
                    log(f"   حُذفت الصورة {i}/{len(photos)}")
                    await asyncio.sleep(0.3)
                except Exception as exc:  # noqa: BLE001
                    log(f"   ❌ فشل حذف صورة: {type(exc).__name__}")
        else:
            log("   لا توجد صور.")
    except Exception as exc:  # noqa: BLE001
        log(f"   ❌ قراءة/حذف الصور فشل: {type(exc).__name__}")

    try:
        await client(functions.account.UpdateProfileRequest(about=""))
        log("   ✅ السيرة (bio) صُفّرت.")
    except Exception as exc:  # noqa: BLE001
        log(f"   ⚠️ تصفير السيرة لم ينجح: {type(exc).__name__}")


async def reset_other_sessions(client) -> None:
    """⚠️ مُستثناة — صاحب الحساب منع صراحةً إنهاء أي جلسة (تُنفَّذ فقط إذا
    تمرر متغير بيئة WIPE_RESET_SESSIONS=1 صراحةً)."""
    if os.getenv("WIPE_RESET_SESSIONS", "0") != "1":
        log("   ⏭️ مرحلة الجلسات مُستثناة بالكامل (الجلسات كما هي بطلب صاحب الحساب).")
        return
    log("• المرحلة 5: إنهاء جلسات الأجهزة الأخرى…")
    try:
        await client(functions.account.ResetAuthorizationsRequest())
        log("   ✅ كل الأجهزة الأخرى خرجت (تبقى هذه الجلسة فقط).")
    except Exception as exc:  # noqa: BLE001
        log(f"   ❌ إعادة الجلسات فشلت: {type(exc).__name__}: {exc}")


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id or not api_hash:
        log("ERROR: API_ID/API_HASH غير موجودين")
        return 1

    started = time.time()
    log("🚀 بدأ التنظيف الشامل…")

    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        log("ERROR: الجلسة غير مصرح بها — أعد الدخول أولًا")
        print("❌ الجلسة غير صالحة — أعد الدخول.")
        return 1

    me = await client.get_me()
    log(f"الحساب: {me.first_name} (@{me.username}) id={me.id}")

    before_path = BASE_DIR / "data" / "inventory_before.txt"
    if before_path.exists():
        log("📋 جرد قبل موجود سلفًا من التشغيل الأول — لن يُستبدل (حفظ للمراجعة).")
        before_count_line = before_path.read_text(encoding="utf-8-sig").splitlines()
        before_count = len([l for l in before_count_line if l.strip()])
    else:
        before_count = await inventory(client, before_path)
        log(f"📋 جرد قبل: {before_count} ديلوغًا → inventory_before.txt")

    done, failed, total = await wipe_dialogs(client)
    contacts_deleted = await wipe_contacts(client)
    await wipe_profile(client)
    await reset_other_sessions(client)

    after_count = await inventory(client, BASE_DIR / "data" / "inventory_after.txt")

    minutes = round((time.time() - started) / 60, 1)
    log("🎯 النتيجة النهائية:")
    log(f"   ديلوغات: قبل {before_count} | حُذف {done}/{total} | فشل {failed} | بعد {after_count}")
    log(f"   جهات اتصال محذوفة: {contacts_deleted}")
    log(f"   الزمن: {minutes} دقيقة")

    print("\n✅ اكتمل التنظيف الشامل.")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
