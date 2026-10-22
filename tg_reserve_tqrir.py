"""
حجز @tqrir_bot كاسم احتياطي (بوت الثاني).
- يتعامل مع حدود BotFather (too many attempts) بالانتظار وإعادة المحاولة تلقائيًا.
- يُحفظ توكنه في data/reserved_credentials.txt (أيقونتك لا تنقص من أي أحقيجة).
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
OUT = BASE_DIR / "data" / "reserved_credentials.txt"

BOTFATHER = "BotFather"
NAME = "تقارير المشروع (احتياطي)"
USERNAME = "tqrir_bot"


async def wait_reply(client, last_id, timeout=60.0):
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


def flood_seconds(text):
    m = re.search(r"try again in (\d+) seconds", text or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


async def attempt(client) -> tuple[str, str] | None:
    """يرجع (username, token) عند النجاح أو None بعد الرد غير المتوقع."""
    first = await client.send_message(BOTFATHER, "/cancel")
    await wait_reply(client, first.id, timeout=15)
    await asyncio.sleep(2.0)

    first = await client.send_message(BOTFATHER, "/newbot")
    r = await wait_reply(client, first.id)
    if not r or not r.message:
        return None
    text = r.message
    wait_s = flood_seconds(text)
    if text.lower().startswith("sorry, too many attempts") and wait_s:
        print(f"⏳ BotFather يطلب انتظار {wait_s} ث…")
        return ("WAIT", wait_s)
    if "name" not in text.lower():
        print(f"⚠️ رد غير متوقع: {text[:100]}")
        return None

    sent = await client.send_message(BOTFATHER, NAME)
    r = await wait_reply(client, sent.id)
    text = (r.message or "") if r else ""
    wait_s = flood_seconds(text)
    if wait_s:
        return ("WAIT", wait_s)
    if "username" not in text.lower():
        print(f"⚠️ رد الاسم غير متوقع: {text[:100]}")
        return None

    sent = await client.send_message(BOTFATHER, USERNAME)
    r = await wait_reply(client, sent.id)
    text = (r.message or "") if r else ""
    wait_s = flood_seconds(text)
    if wait_s:
        return ("WAIT", wait_s)
    taken = "already taken" in text.lower() or "not available" in text.lower()
    if taken:
        print("❌ الاسم صار محجوزًا الآن (تم إختراقه من غيرك أثناء تأخيرنا)!")
        return None
    token = extract_token(text)
    if token:
        return (USERNAME, token)
    print(f"⚠️ رد اليوزر غير متوقع: {text[:140]}")
    return None


async def main() -> int:
    client = TelegramClient(str(SESS), int(os.getenv("API_ID")), os.getenv("API_HASH"))
    await client.connect()

    for i in range(4):
        made = await attempt(client)
        if not made:
            break
        if made[0] == "WAIT":
            await asyncio.sleep(made[1] + 6)
            continue
        username, token = made
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(f"TOKEN={token}\nUSERNAME=@{username}\nSEHAT=true\nملاحظة=بوت احتياطي (محمول اسمه)\n")
        print(f"✅ تم الحجز: @{username}")
        print(f"🔐 توكن الحجز: {token}")
        print(f"💾 حُفظ في {OUT.name}")
        await client.disconnect()
        return 0

    print("❌ لم يُحجز بعد المحاولات — نعيد التخطيط لاحقًا.")
    await client.disconnect()
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
