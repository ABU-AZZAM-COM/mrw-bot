"""
إعادة تسمية البوت لاسم مختصر (لا تُدعم إعادة تسمية اليوزر رسميًا):
  1) إنشاء بوت جديد بالاسم المختصر المتاح
  2) حذف البوت القديم عبر BotFather /deletebot (باختياره من الأزرار ثم /confirm)
  3) حفظ بيانات الوثيقة الجديدة في data/bot_credentials.txt
"""
import asyncio
import os
import random
import re
import string
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
CRED_FILE = BASE_DIR / "data" / "bot_credentials.txt"

BOTFATHER = "BotFather"
BOT_NAME = "بلاغات المخالفات العسكرية"
OLD_USERNAME = "mil_violation_reports_zlifg_bot"

# مرتبة من الأقصر والأوضح حتى الأطول
CANDIDATES = [
    "mkhf_bot",
    "mkhfb_bot",
    "mkhfat_bot",
    "mklfat_bot",
    "mkhfbot_bot",
    "t_balagh_bot",
]


def rand_suffix(n=5):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


async def wait_reply(client, last_id, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages(BOTFATHER, limit=1)
        if msgs and msgs[0].id > last_id:
            return msgs[0]
        await asyncio.sleep(1.0)
    return None


def extract_token(text: str):
    m = re.search(r"(\d+:[A-Za-z0-9_\-]+)", text or "")
    return m.group(1) if m else None


async def create_bot(client) -> tuple[str, str] | None:
    first = await client.send_message(BOTFATHER, "/newbot")
    r = await wait_reply(client, first.id)
    if not r:
        print("❌ لا رد على /newbot")
        return None

    sent = await client.send_message(BOTFATHER, BOT_NAME)
    r = await wait_reply(client, sent.id)
    if not r:
        print("❌ لا رد على الاسم")
        return None

    for username in CANDIDATES + [f"mkhf_{rand_suffix()}_bot" for _ in range(4)]:
        sent = await client.send_message(BOTFATHER, username)
        r = await wait_reply(client, sent.id)
        if not r:
            continue
        text = r.message or ""
        if "already taken" in text.lower() or "not available" in text.lower():
            print(f"  ⚠️ محجوز: {username}")
            continue
        token = extract_token(text)
        if token:
            print(f"  ✅ الاسم المختصر المتبنّى: @{username}")
            print(f"  🔐 التوكن الجديد: {token}")
            return username, token
        print(f"  BotFather: {text[:120]}")
    return None


async def delete_old_bot(client) -> None:
    sent = await client.send_message(BOTFATHER, "/deletebot")
    r = await wait_reply(client, sent.id)
    if not r or not r.reply_markup:
        print("⚠️ DeleteBot لم يعرض أزرارًا — سأبقى البوت القديم (غير ضار).")
        return

    # اختيار البوت القديم من الأزرار
    clicked = False
    for row in r.reply_markup.rows:
        for btn in row.buttons:
            if OLD_USERNAME.lower() in (btn.text or "").lower():
                await btn.click()
                clicked = True
                break
        if clicked:
            break
    if not clicked:
        for row in r.reply_markup.rows:
            for btn in row.buttons:
                if "bot" in (btn.text or "").lower():
                    await btn.click()
                    clicked = True
                    break
            if clicked:
                break

    if not clicked:
        print("⚠️ لم أعثر على زر البوت القديم.")
        return

    time.sleep(1.2)
    msgs = await client.get_messages(BOTFATHER, limit=2)
    latest = msgs[0].message or ""
    if "confirm" in latest.lower():
        sent2 = await client.send_message(BOTFATHER, "/confirm")
        r2 = await wait_reply(client, sent2.id, timeout=30)
        print("✅ تأكيد الحذف أُرسل.")
        if r2:
            print(f"   BotFather: {(r2.message or '')[:140]}")
    else:
        print(f"   BotFather بعد الاختيار: {latest[:140]}")


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    print("🚫 الخطوة 1: إنشاء بوت جديد باسم مختصر…")
    made = await create_bot(client)
    if not made:
        print("❌ فشل إنشاء البوت الجديد — لن أحذف القديم.")
        return 1

    new_username, new_token = made

    print("\n🗑 الخطوة 2: حذف البوت القديم…")
    try:
        await delete_old_bot(client)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ حذف القديم تعثر: {type(exc).__name__}: {exc} (غير خطير)")

    # حفظ البيانات
    with open(CRED_FILE, "w", encoding="utf-8") as f:
        f.write(f"TOKEN={new_token}\nUSERNAME=@{new_username}\nADMIN_ID=1557513643\n")

    print("\n💾 حفظت بيانات البوت الجديد في data/bot_credentials.txt")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
