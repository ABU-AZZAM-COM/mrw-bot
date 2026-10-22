"""
اختبار حي متكامل لنظام البلاغات من الطرف إلى الطرف عبر الجلسة:
  /start → صورة → رابط → وصف → تأكيد → حفظ + إشعار المشرف.

يقرأ رد كل خطوة من كيان البوت ويطبعها.
"""
import asyncio
import os
import time
from pathlib import Path

from PIL import Image
from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
BOT_USERNAME = "@mil_violation_reports_zlifg_bot"

FAKE_IMG = BASE_DIR / "data" / "_test_screenshot.png"


def sleep_until(deadline, step=0.7):
    while time.time() < deadline:
        time.sleep(step)


async def wait_for_new(client, bot_chat, last_id, timeout=25):
    """انتظار رسالة جديدة من البوت بعد last_id."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages(bot_chat, limit=2)
        if msgs and max(m.id for m in msgs) > last_id:
            return msgs[0]
        await asyncio.sleep(0.8)
    return None


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    bot = await client.get_entity(BOT_USERNAME)

    # 0) تجهيز صورة توثيقية وهمية (PNG)
    Image.new("RGB", (600, 420), (28, 92, 160)).save(str(FAKE_IMG))

    # 1) /start
    sent = await client.send_message(bot, "/start")
    r = await wait_for_new(client, bot, sent.id)
    print("1) البوت رد بأول ترحيب:", (r.message[:80] if r else "❌ لا رد"))

    # 2) الصورة
    sent = await client.send_file(bot, str(FAKE_IMG))
    r = await wait_for_new(client, bot, sent.id)
    print("2) بعد الصورة:", (r.message[:80] if r else "❌ لا رد"))

    # 3) الرابط
    sent = await client.send_message(bot, "https://t.me/armed_forces_guideline/2024")
    r = await wait_for_new(client, bot, sent.id)
    print("3) بعد الرابط:", (r.message[:80] if r else "❌ لا رد"))

    # 4) الوصف
    sent = await client.send_message(bot, "منشور يروّج لجمع أموال بظل الجهات الأمنية — خطأ عاجل ومطلوب ملازمته.")
    r = await wait_for_new(client, bot, sent.id)
    print("4) بعد الوصف (ملخص):", (r.message[:80].replace("\n", " ") if r else "❌ لا رد"))

    # 5) تأكيد
    sent = await client.send_message(bot, "✅ إرسال البلاغ")
    r1 = await wait_for_new(client, bot, sent.id)
    print("5) بعد التأكيد — الرد الأول:", (r1.message[:90].replace("\n", " ") if r1 else "❌ لا رد"))

    # 6) إشعار المشرف (يصل بنفس الحساب)
    await asyncio.sleep(2.5)
    msgs = await client.get_messages(bot, limit=3)
    print("6) آخر 3 رسائل من البوت الآن:")
    for m in msgs:
        kind = "🖼 صورة+تعليق" if m.photo else ("📝 نص" if m.message else "؟")
        text = (m.message or "")[:60].replace("\n", " ")
        print(f"   - [{kind}] {text}")

    # 7) التحقق من قاعدة البيانات مباشرة
    import sqlite3
    conn = sqlite3.connect(str(BASE_DIR / "data" / "reports.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT seq, status, user_id, post_link FROM reports ORDER BY id DESC LIMIT 1").fetchall()
    print("7) السجل في قاعدة البيانات:")
    for row in rows:
        print(f"   #{row['seq']} | حالة: {row['status']} | سرى مرسل {row['user_id']} | رابط: {row['post_link']}")
    conn.close()

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
