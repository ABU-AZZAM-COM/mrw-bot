"""حذف البوت القديم عبر /deletebot مع دعم أزرار لوحة الرد (KeyboardButton)."""
import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
OLD_USERNAME = "mil_violation_reports_zlifg_bot"


async def wait_reply(client, last_id, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = await client.get_messages("BotFather", limit=1)
        if msgs and msgs[0].id > last_id:
            return msgs[0]
        await asyncio.sleep(1.0)
    return None


def pick_old_button(msg):
    """استخراج نص الزر الذي يحمل اسم البوت القديم (يدعم Inline أو Keyboard)."""
    rm = msg.reply_markup
    if rm is None:
        return None
    rows = getattr(rm, "rows", None)
    if rows is None and hasattr(rm, "button_rows"):
        rows = rm.button_rows
    if rows is None:
        # fallback عام: محاولة الوصول للقائمة عبر الحقول المعروفة
        for attr in ("rows", "button_rows", "buttons"):
            if hasattr(rm, attr):
                rows = getattr(rm, attr)
                break
    if not rows:
        return None
    for row in rows:
        buttons = row if isinstance(row, list) else getattr(row, "buttons", [])
        for btn in buttons:
            text = getattr(btn, "text", None)
            if text and OLD_USERNAME.lower() in text.lower():
                return text
    return None


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    sent = await client.send_message("BotFather", "/deletebot")
    r = await wait_reply(client, sent.id)
    if not r:
        print("❌ لا رد على /deletebot")
        return 1

    btn_text = pick_old_button(r)
    if not btn_text:
        print(f"❌ لم أجد زر البوت القديم — آخر رد: {(r.message or '')[:150]}")
        return 1

    # أزرار لوحة الرد تُرسل كنص رسالة (لا نُقرّ فيها)
    sent2 = await client.send_message("BotFather", btn_text)
    r2 = await wait_reply(client, sent2.id)
    text2 = (r2.message or "") if r2 else ""
    print(f"BotFather: {text2[:160]}")

    if "confirm" in text2.lower():
        sent3 = await client.send_message("BotFather", "/confirm")
        r3 = await wait_reply(client, sent3.id, timeout=30)
        print("✅ تم إرسال التأكيد.")
        if r3:
            print(f"BotFather: {(r3.message or '')[:160]}")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
