"""
دخول بالاستجابة السريعة (QR) — بدون أي كتابة من المستخدم إطلاقًا.

آلية العمل:
  1) يفتح جلسة ويولّد مربع QR في: data/qr_login.png (+ الرابط في data/qr_link.txt)
  2) المستخدم يمسح المربع من تلغرام على الهاتف:
       الإعدادات ⚙️ → الأجهزة (Devices) → Link Desktop Device
  3) فور اعتماده القدوم تصبح الجلسة محفوظة في: data/user_session.session
  4) يتيح أيضًا الرابط tg://link لمن لديه تلغرام Desktop مسجَّل بنفس الحساب
     (فتح الرابط على الكمبيوتر = زر اعتماد داخل تلغرام Desktop)

كل المراحل تسجَّل في data/login_log.txt للتشخيص التلقائي.
"""
import asyncio
import time
from PIL import Image
import qrcode

from telethon import TelegramClient, errors
from dotenv import load_dotenv
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
QR_PNG = BASE_DIR / "data" / "qr_login.png"
QR_LINK = BASE_DIR / "data" / "qr_link.txt"
LOG_FILE = BASE_DIR / "data" / "login_log.txt"

TOTAL_MINUTES = 5          # إجمالي مهلة انتظار المسح
load_dotenv(ENV_FILE)


def log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] QRLOGIN {msg}\n")
    except Exception:
        pass


def render_qr(url: str) -> None:
    """إعادة رسم مربع QR في الملف نفسه (يُحدّث تلقائيًا عند تغير الرمز كل 30 ث)."""
    img = qrcode.make(url)
    img = img.resize((480, 480))
    img.save(str(QR_PNG))
    with open(QR_LINK, "w", encoding="utf-8") as f:
        f.write(url)


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id or not api_hash:
        log("ERROR: API_ID/API_HASH غير موجودين في .env")
        print("❌ API_ID/API_HASH غير موجودين في .env")
        return 1

    log(f"ابدأ QR دخول (مهلة {TOTAL_MINUTES} دقائق)")
    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        log(f"الموجود سلفًا مُصرَّح: id={me.id}")
        print(f"✅ الجلسة موجودة مسبقًا وصالحة — بدون أي إجراء: id={me.id}")
        await client.disconnect()
        return 0

    deadline = time.time() + TOTAL_MINUTES * 60
    attempt = 0
    need_password = False

    while time.time() < deadline:
        attempt += 1
        qr = await client.qr_login()
        render_qr(qr.url)
        log(f"QR جاهز للقراءة (محاولة {attempt}) — بانتظار المسح")
        print(f"🟢 مربع QR جاهز في: {QR_PNG.name} (محاولة {attempt}) — انتظر المسح…")

        try:
            await qr.wait()          # ينتظر جلول المستخدم (حتى انتهاء صلاحية الرمز)
        except asyncio.TimeoutError:
            continue                 # رمز منتهي ← سيُعاد توليد مربع جديد تلقائيًا
        except errors.SessionPasswordNeededError:
            log("ستُطلب كلمة 2FA بعد المسح — أعد الدخول عبر 1-تسجيل-الدخول.bat لهذه الحالة")
            need_password = True
            break
        except Exception as exc:  # noqa: BLE001
            log(f"ERROR qr.wait: {type(exc).__name__}: {exc}")
            break

        if await client.is_user_authorized():
            me = await client.get_me()
            log(f"SUCCESS عبر QR: id={me.id} username={me.username}")
            print(f"✅ تم اعتماد الدخول! حساب: {me.first_name} | id={me.id}")
            await client.disconnect()
            return 0

    log(f"FAILURE: انتهت مهلة {TOTAL_MINUTES} دقائق بلا مسح (need_password={need_password})")
    print("⏳ انتهى وقت الانتظار بلا مسح — شغّل الملف من جديد لمربع QR جديد.")
    await client.disconnect()
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
