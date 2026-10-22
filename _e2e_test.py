"""
اختبار حي من طرف إلى طرف: يرسل أوامر حقيقية إلى البوت من حساب المستخدم
ويطبع ردود البوت (تأكد من عمل الأوامر الجديدة فعليًا).

ملاحظة: لا يُنهي الجلسة — يتصل بها فقط (قاعدة المشروع).
"""

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient, events

BASE = Path(__file__).resolve().parent
SESS = BASE / "data" / "user_session.session"
API_ID = os.getenv("API_ID", "36082659")
API_HASH = os.getenv("API_HASH", "4d1de251c4f1fbfc093c10d168f2c42c")
BOT = os.getenv("BOT_USERNAME", "@blgh_bot")

COMMANDS = ["/start", "/app", "/panic", "/audit", "/resume"]


async def main() -> int:
    client = TelegramClient(str(SESS), int(API_ID), API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("❌ الجلسة غير مصرّح بها")
        return 1

    me = await client.get_me()
    print(f"👤 الحساب: {me.first_name} (ID {me.id})")
    target = await client.get_entity(BOT)
    print(f"🎯 البوت: {target.username}\n" + "=" * 55)

    for command in COMMANDS:
        # تنظيف الرسائل القديمة لهذا الأمر
        try:
            async for message in client.iter_messages(target, limit=0):
                await message.delete()
        except Exception:
            pass

        await client.send_message(target, command)
        await asyncio.sleep(2.5)

        reply_text = ""
        try:
            async for message in client.iter_messages(target, limit=3):
                if message.text and message.text.strip() != command:
                    reply_text = message.text
                    break
        except Exception as exc:
            reply_text = f"(تعذّر القراءة: {exc})"

        print(f"\n▶ أرسلت: {command}")
        print(f"◀ رد البوت: {reply_text[:700]}")

    await client.disconnect()
    print("\n=== انتهى الاختبار الحي ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
