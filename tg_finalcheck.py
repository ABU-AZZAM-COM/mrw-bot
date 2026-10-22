"""فحص نهائي للبوت الجديد المختصر: /start ثم /admin."""
import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESS = BASE_DIR / "data" / "user_session.session"
BOT_USERNAME = "@mkhfb_bot"


async def wait_for_new(client, bot_chat, last_id, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages(bot_chat, limit=2)
        if msgs and max(m.id for m in msgs) > last_id:
            return msgs[0]
        await asyncio.sleep(0.8)
    return None


async def main() -> int:
    client = TelegramClient(str(SESS), int(os.getenv("API_ID")), os.getenv("API_HASH"))
    await client.connect()

    bot = await client.get_entity(BOT_USERNAME)

    sent = await client.send_message(bot, "/start")
    r = await wait_for_new(client, bot, sent.id)
    print("1) الترحيب من البوت الجديد:", (r.message[:70].replace("\n", " ") if r else "❌ لا رد"))

    sent = await client.send_message(bot, "/admin")
    r = await wait_for_new(client, bot, sent.id)
    ok_admin = r and "لوحة إدارة البلاغات" in (r.message or "")
    print("2) لوحة المشرف:", ("✅ فُتحت" if ok_admin else (f"❌ الرد: {(r.message or '')[:70]}" if r else "❌ لا رد")))

    sent = await client.send_message(bot, "/cancel")
    r = await wait_for_new(client, bot, sent.id)
    print("3) /cancel:", (r.message[:60].replace("\n", " ") if r else "❌ لا رد"))

    await client.disconnect()
    return 0 if ok_admin else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
