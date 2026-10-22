"""
مسبار أسماء البوتات: يفحص كل مرشح فعليًا عن طريق BotFather (حقيقي 100%).

آلية الفحص الآمنة:
  - لكل اسم مرشح: يبدأ /newbot ويدخل الاسم يوزر المرشح.
  - إن رد "taken/not available" → محجوز (لم يُنشأ شيء).
  - إن قبل الاسم وتوليد توكن → متاح! ثم يُحذف فورًا (لأن الاختيار النهائي مقبلةلك)
    حتى لا يتراكم بوتات عرضية.
  - لا يمس @mkhfb_bot الحالي إطلاقًا (اختيار الترقيم مطابق فقط بالاسم الملف%).

النتائج تُطبع كجدول وتُحفظ في data/name_probe.txt
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
PROBE_OUT = BASE_DIR / "data" / "name_probe.txt"

BOTFATHER = "BotFather"
BOT_NAME = "بلاغات المخالفات العسكرية"

CANDIDATES = [
    "blgh_bot",       # بلاغ
    "blghat_bot",     # بلاغات
    "tblgh_bot",      # تبليغ
    "tanbih_bot",     # تنبيه
    "tqrir_bot",      # تقرير
    "mkhf_bot",       # مخالفات (أعيد فحصه، ربما تحرر)
]


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


def pick_button_text(msg, needle: str):
    """استخراج نص الزر المطابق (يدعم Keyboard و Inline)."""
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
        return False
    sent2 = await client.send_message(BOTFATHER, btn)
    r2 = await wait_reply(client, sent2.id)
    text2 = (r2.message or "") if r2 else ""
    if "sure" in text2.lower() or "confirm" in text2.lower():
        sent3 = await client.send_message(BOTFATHER, "Yes, I am totally sure.")
        await wait_reply(client, sent3.id, timeout=30)
        return True
    return False


async def probe_name(client, username: str) -> str:
    """يرجع: متاح / محجوز / خطأ: سبب"""
    first = await client.send_message(BOTFATHER, "/newbot")
    r = await wait_reply(client, first.id)
    if not r:
        return "خطأ: BotFather لا يرد على /newbot"
    sent = await client.send_message(BOTFATHER, BOT_NAME)
    r = await wait_reply(client, sent.id)
    if not r:
        return "خطأ: لا رد على الاسم"
    sent = await client.send_message(BOTFATHER, username)
    r = await wait_reply(client, sent.id)
    if not r:
        return "خطأ: لا رد على اليوزر"
    text = r.message or ""
    low = text.lower()
    if "already taken" in low or "not available" in low:
        return "❌ محجوز"
    token = extract_token(text)
    if token:
        ok = await delete_bot(client, username)
        return "✅ متاح = فُحص وحُذف مؤقتًا" if ok else "✅ متاح (حذفه اللاحق تعثر — سأُداويه)"
    return f"خطأ: {text[:80]}"


async def main() -> int:
    client = TelegramClient(str(SESS), int(os.getenv("API_ID")), os.getenv("API_HASH"))
    await client.connect()
    results = []
    for name in CANDIDATES:
        status = await probe_name(client, name)
        results.append((name, status))
        print(f"{name:14} → {status}")
        await asyncio.sleep(2.0)

    with open(PROBE_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{n} | {s}" for n, s in results))
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
