"""إعادة محاولة إنشاء blgh_bot مع تنظيف حالة BotFather وطباعة كل ردود الحوار."""
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
        print(f"    الزر غير ظاهر. آخر رد: {(r.message or '')[:120]}")
        return False
    sent2 = await client.send_message(BOTFATHER, btn)
    r2 = await wait_reply(client, sent2.id)
    t2 = (r2.message or "") if r2 else ""
    print(f"    BotFather: {t2[:120]}")
    if "sure" in t2.lower():
        sent3 = await client.send_message(BOTFATHER, "Yes, I am totally sure.")
        r3 = await wait_reply(client, sent3.id, timeout=30)
        print(f"    BotFather: {(r3.message or '')[:80] if r3 else ''}")
        return r3 is not None
    return False


async def create_bot(client) -> tuple[str, str] | None:
    # تنظيف حالة الحوار (في أي مرحلة كانت)
    cancel = await client.send_message(BOTFATHER, "/cancel")
    await wait_reply(client, cancel.id, timeout=15)
    await asyncio.sleep(2.0)

    first = await client.send_message(BOTFATHER, "/newbot")
    r = await wait_reply(client, first.id)
    print(f"[1] {((r.message or '')[:100]).replace(chr(10), ' ')}")
    if not r or "name" not in (r.message or "").lower():
        print("⚠️ الرد على /newbot غير متوقع — أطبعه كاملًا أسفل.")
        print(r.message if r else "(لا رد)")

    sent = await client.send_message(BOTFATHER, BOT_NAME)
    r = await wait_reply(client, sent.id)
    print(f"[2] {((r.message or '')[:100]).replace(chr(10), ' ')}")

    sent = await client.send_message(BOTFATHER, NEW_USERNAME)
    r = await wait_reply(client, sent.id)
    text = (r.message or "") if r else ""
    print(f"[3] {text[:160].replace(chr(10), ' ')}")
    token = extract_token(text)
    if token:
        return NEW_USERNAME, token
    return None


async def main() -> int:
    client = TelegramClient(str(SESS), int(os.getenv("API_ID")), os.getenv("API_HASH"))
    await client.connect()

    print("🚀 إنشاء @blgh_bot …")
    made = await create_bot(client)
    if not made:
        print("❌ فشل التاسيس في هذه المحاولة — أبلغني لاعيد التخطيط.")
        await client.disconnect()
        return 1

    username, token = made
    print("🗑 حذف القديم @mkhfb_bot…")
    try:
        ok = await delete_bot(client, OLD_USERNAME)
        print("✅ حُذف القديم." if ok else "⚠️ حذف القديم تعثر (يدويًا ممكنٌ لاحقًا).")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ تعثر الحذف: {exc}")

    with open(CRED, "w", encoding="utf-8") as f:
        f.write(f"TOKEN={token}\nUSERNAME=@{username}\nADMIN_ID=1557513643\n")
    print("💾 حُفظت البيانات.")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
