"""تأكيد حذف البوت القديم بنص التأكيد الحرفي الذي اشترطه BotFather."""
import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
CONFIRM_TEXT = "Yes, I am totally sure."


async def wait_reply(client, last_id, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages("BotFather", limit=1)
        if msgs and msgs[0].id > last_id:
            return msgs[0]
        await asyncio.sleep(1.0)
    return None


async def main() -> int:
    client = TelegramClient(str(SESSION_FILE), int(os.getenv("API_ID")), os.getenv("API_HASH"))
    await client.connect()
    sent = await client.send_message("BotFather", CONFIRM_TEXT)
    r = await wait_reply(client, sent.id)
    print(f"BotFather: {(r.message or '') if r else 'لا رد'}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
