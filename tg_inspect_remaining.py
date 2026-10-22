"""
فحص ما تبقى من الديالوغات بعد التنظيف + تشخيص تفاصيله (نوع/ملكية/أعضاء).
"""
import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"


async def main() -> None:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()

    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ الجلسة غير صالحة")
        return

    dialogs = await client.get_dialogs(limit=None)
    print(f"عدد الديالوغات الباقية: {len(dialogs)}\n")
    for d in dialogs:
        e = d.entity
        kind = "خاص" if d.is_user and not getattr(e, "bot", False) else (
            "بوت" if d.is_user else ("مجموعة" if getattr(e, "megagroup", False) else "قناة")
        )
        print(f"• النوع: {kind}")
        print(f"  العنوان: {d.name or getattr(e, 'title', '') or 'بدون عنوان'}")
        print(f"  id: {e.id} | username: @{getattr(e, 'username', None) or '—'}")
        print(f"  رقم الدا: {d.id}")
        print(f"  أعضاء: {getattr(e, 'participants_count', '—')}")
        creator = getattr(e, "creator", None)
        admin = getattr(e, "admin_rights", None)
        print(f"  هل أنت المالك (creator): {creator}")
        print(f"  هل لديك صلاحيات مشرف: {bool(admin)}")
        print()

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
