"""
معالجة آخر ديلوغ معلّق (قناة "$") عبر متعدد المسارات الرسمية:
  1) مغادرة عادية LeaveChannelRequest
  2) حذف الديالوغ بدون حذف السجل delete_dialog(revoke=False)
  3) محاولة حذف القناة كاملة لو ثبتت الملكية DeleteChannelRequest
كل محاولة تسجل نتيجتها، والنهاية: جرد نهائي للعدد.
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, errors, functions

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id:
        print("❌ لا API_ID")
        return 1

    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    dialogs = await client.get_dialogs(limit=None)
    if not dialogs:
        print("✅ لا يوجد شيء متبقٍ أصلًا — الحساب نظيف تمامًا.")
        await client.disconnect()
        return 0

    d = dialogs[0]
    e = d.entity
    print(f"مستهدف: {d.name or 'بدون عنوان'} | id={e.id} | creator={getattr(e, 'creator', None)}")

    # مسار 1: مغادرة عادية
    try:
        await client(functions.channels.LeaveChannelRequest(channel=e))
        print("✅ المسار 1 نجح: مغادرة القناة (LeaveChannelRequest)")
    except errors.UserNotParticipantError:
        print("ℹ️ المسار 1: لست مشاركًا فيها أصلًا")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ المسار 1 فشل: {type(exc).__name__}: {exc}")

    # مسار 2: حذف الديالوغ من القائمة (بدون حذف سجل القناة نفسها)
    try:
        await client.delete_dialog(e, revoke=False)
        print("✅ المسار 2 نجح: حذف الديالوغ من قائمة المحادثات")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ المسار 2 فشل: {type(exc).__name__}: {exc}")

    # مسار 3: حذف القناة كاملة لو ثبت ملكية الحساب فيها
    if getattr(e, "creator", False):
        try:
            await client(functions.channels.DeleteChannelRequest(channel=e))
            print("✅ المسار 3 نجح: حذف القناة كاملة (لأنك مالكها)")
        except Exception as exc:  # noqa: BLE001
            print(f"❌ المسار 3 فشل: {type(exc).__name__}: {exc}")
    else:
        print("ℹ️ المسار 3 متخطى (لست المالك — وبطلنا إلزاميًا عدم حذف مملك الآخرين)")

    # الجرد النهائي
    dialogs = await client.get_dialogs(limit=None)
    print(f"\n📊 النتيجة: عدد الديالوغات الباقية الآن: {len(dialogs)}")
    for x in dialogs:
        print(f"   - {x.name or 'بدون عنوان'} | id={x.entity.id}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
