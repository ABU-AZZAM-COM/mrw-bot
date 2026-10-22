"""
إنشاء بوت جديد آليًا عبر BotFather باستخدام الجلسة المحلية.

خطوات الحوار مع BotFather:
  /newbot  → اسم البوت → اسم مستخدم (يتطلب اختتامًا بـ bot) → التوكن

التعامل مع الرفض (اسم مستخدم محجوز) بإعادة المحاولة تلقائيًا.
النتيجة تُحفظ في: data/bot_credentials.txt  (ملف محلي سرّي على جهازك)
"""
import asyncio
import random
import string
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
import os

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
CRED_FILE = BASE_DIR / "data" / "bot_credentials.txt"

BOTFATHER = "BotFather"
BOT_NAME = "بلاغات المخالفات العسكرية"


def rand_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def candidate_usernames():
    base_choices = ["mil_violation_reports", "violations_report_mil", "military_reports_hotline"]
    out = []
    for base in base_choices:
        out.append(f"{base}_{rand_suffix(5)}_bot")
    return out


async def wait_reply(client, last_msg_id: int, timeout: float = 40.0):
    """انتظار رسالة جديدة من BotFather بعد last_msg_id."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages(BOTFATHER, limit=1)
        if msgs and msgs[0].id > last_msg_id:
            return msgs[0]
        await asyncio.sleep(1.0)
    return None


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    botfather = await client.get_entity(BOTFATHER)
    first = await client.send_message(botfather, "/newbot")
    reply = await wait_reply(client, first.id)

    if not reply:
        print("❌ BotFather لم يرد على /newbot")
        return 1
    print(f"BotFather: {reply.message[:120]}")

    # الاسم
    sent = await client.send_message(botfather, BOT_NAME)
    reply = await wait_reply(client, sent.id)
    if not reply:
        print("❌ BotFather لم يرد على الاسم")
        return 1
    print(f"BotFather: {reply.message[:120]}")

    TRIES = 6
    for i, username in enumerate(candidate_usernames() * 2, start=1):
        sent = await client.send_message(botfather, username)
        reply = await wait_reply(client, sent.id)
        if not reply:
            print(f"❌ No reply on username {username}")
            continue
        text = reply.message or ""
        lowered = text.lower()
        if "is already taken" in lowered or "username is not available" in lowered:
            print(f"⚠️ اسم مستخدم محجوز: {username} — أحاول التالِي…")
            continue
        if "token" in lowered:
            import re
            m = re.search(r"(\d+:[A-Za-z0-9_\-]+)", text)
            if not m:
                print("❌ التوكن ظهر بصيغة غير متوقعة — راجع رد BotFather:")
                print(text)
                return 1
            token = m.group(1)
            print(f"✅ تم إنشاء البوت! @{username}")
            print(f"🔐 التوكن: {token}")
            with open(CRED_FILE, "w", encoding="utf-8") as f:
                f.write(f"TOKEN={token}\nUSERNAME=@{username}\nADMIN_ID=1557513643\n")
            print(f"💾 حُفي في {CRED_FILE.name}")
            await client.disconnect()
            return 0
        else:
            print(f"BotFather: {text[:200]} (محاولة {i})")

    print("❌ استُنزفت محاولات أسماء المستخدمين.")
    await client.disconnect()
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
