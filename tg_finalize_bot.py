"""
التثبيت النهائي: بوت باليوزر blgh_bot + حذف البوت القديم mkhfb_bot.
يحفظ في data/bot_credentials.txt: التوكن الجديد + اليوزر + ADMIN_ID.
"""
import asyncio
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESS = BASE_DIR / "data" / "user_session.session"
CRED = BASE_DIR / "data" / "bot_credentials.txt"

BOTFATHER = "BotFather"
BOT_NAME = "بلاغات المخالفات العسكرية"
NEW_USERNAME = "blgh_bot"
OLD_USERNAME = "mkhfb_bot"


async def wait_reply(client, last_id, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages(BOTFATHER, limit=1)
        if msgs and msgs[0].id > last_id:
            return msgs[0]
        await asyncio.sleep(1.0)
    return None


def extract_token(text):
    m = re.search(r"(\d+:[A-Za-z0-9_\-]+)", text or "")
    return m.group(1) if m else None


def pick_button_text(msg, needle):
    rm = msg.reply_markup
    if rm is None:
        return None
    rows = getattr(rm, "rows", None) or getattr(rm, "button_rows", None)
    if not rows:
        return None
    for row in rows:
        buttons = row if isinstance(row, list) else getattr(row, "buttons", [])
        for btn in buttons:
            t = getattr(btn, "text", None)
            if t and needle.lower() in t.lower():
                return t
    return None


async def delete_bot(client, username: str) -> bool:
    sent = await client.send_message(BOTFATHER, "/deletebot")
    r = await wait_reply(client, sent.id)
    if not r:
        return False
    btn = pick_button_text(r, username)
    if not btn:
        print(f"    لم أجد زر {username} في القائمة")
        return False
    sent2 = await client.send_message(BOTFATHER, btn)
    r2 = await wait_reply(client, sent2.id)
    t2 = (r2.message or "") if r2 else ""
    if "sure" in t2.lower():
        sent3 = await client.send_message(BOTFATHER, "Yes, I am totally sure.")
        r3 = await wait_reply(client, sent3.id, timeout=30)
        ok = r3 is not None
        print(f"    BotFather: {(r3.message or '')[:80] if r3 else ''}")
        return ok
    return False


async def main() -> int:
    client = TelegramClient(str(SESS), int(os.getenv("API_ID")), os.getenv("API_HASH"))
    await client.connect()

    # 1) إنشاء البوت النهائي
    first = await client.send_message(BOTFATHER, "/newbot")
    r = await wait_reply(client, first.id)
    sent = await client.send_message(BOTFATHER, BOT_NAME)
    r = await wait_reply(client, sent.id)
    sent = await client.send_message(BOTFATHER, NEW_USERNAME)
    r = await wait_reply(client, sent.id)
    text = (r.message or "") if r else ""
    token = extract_token(text)
    if not token:
        print(f"❌ فشل إنشاء @{NEW_USERNAME} — رد BotFather: {text[:150]}")
        await client.disconnect()
        return 1
    print(f"✅ تم إنشاء البوت النهائي: @{NEW_USERNAME}")
    print(f"🔐 توكن: {token}")

    # 2) حذف القديم
    print("🗑 حذف البوت القديم @mkhfb_bot…")
    ok = await delete_bot(client, OLD_USERNAME)
    print("✅ حُذف القديم." if ok else "⚠️ حذف القديم تعثر (غير حرج — أداويه لاحقًا).")

    # 3) حفظ
    with open(CRED, "w", encoding="utf-8") as f:
        f.write(f"TOKEN={token}\nUSERNAME=@{NEW_USERNAME}\nADMIN_ID=1557513643\n")
    print("💾 بُثّت بيانات data/bot_credentials.txt")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
