"""
تسجيل الدخول المحلي إلى الحساب (التجريبي) — بنسخة "زر ضغط" للمبتدئين تمامًا.

الطريقة:
  دبل-كليك على (1-تسجيل-الدخول.bat) وأعِد اتباع الأسئلة العربية داخل النافذة.

ملاحظات:
  • لا يحتاج ملف .env مسبقًا — سينشئه تلقائيًا ويحفظ فيه API_ID/API_HASH.
  • الكود الذي يصلك على الهاتف يُكتب داخل النافذة فقط، ولا يُشارك مع أحد.
  • الجلسة تُحفظ في: data/user_session.session
  • كل المحاولات والأخطاء تُسجَّل في: data/login_log.txt (لتشخيص أي فشل تلقائيًا)
"""
import asyncio
import getpass
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv, set_key
from telethon import TelegramClient, errors

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
LOG_FILE = BASE_DIR / "data" / "login_log.txt"

load_dotenv(ENV_FILE)


# ------------------------------------------------------------------
# أدوات مساعدة
# ------------------------------------------------------------------
def log(msg: str) -> None:
    """تسجيل أي حدث في ملف اللوج (بحيث يقرأه المساعد لاحقًا ويشخّص الفشل)."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def ask(text: str) -> str:
    return input(f"\n✏️  {text}\n> ").strip()


def norm_phone(raw: str) -> str:
    """تطبيع الرقم: إزالة المسافات وإضافة + إن لم توجد (يرفع فرص النجاح)."""
    p = raw.strip().replace(" ", "").replace("-", "")
    if p and not p.startswith("+"):
        p = "+" + p
    return p


def get_api_keys() -> tuple[int, str]:
    """قراءة API_ID/API_HASH من .env أو طلبها وإحفظهما تلقائيًا."""
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()

    if not api_id or not api_hash:
        api_id = ask("أدخل API_ID (الرقم الصغير من صفحة تطوير API)")
        api_hash = ask("أدخل API_HASH (النص الطويل من نفس الصفحة)")
        set_key(str(ENV_FILE), "API_ID", api_id)
        set_key(str(ENV_FILE), "API_HASH", api_hash)
        log("حفظت API_ID/API_HASH في .env (أول مرة)")
        print("\n✅ تم حفظهما في ملف .env ولن يطلبهما منك مرة أخرى.")
    else:
        print("🔑 مفاتيح API موجودة مسبقًا — نجاري مباشرة.")

    return int(api_id), api_hash


# ------------------------------------------------------------------
# المسار الرئيسي
# ------------------------------------------------------------------
async def main() -> int:
    print()
    print("=" * 60)
    print("  🔐 تسجيل الدخول المحلي — الكود يُكتب هنا فقط")
    print("=" * 60)

    api_id, api_hash = get_api_keys()
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        log(f"جلسة موجودة سلفًا وصالحة: {me.first_name if me else '?'} id={me.id if me else '?'}")
        print("\n✅ الجلسة محفوظة من قبل وصالحة — لا حاجة لإعادة الدخول.")
        _print_me(me)
        await client.disconnect()
        return 0

    # ---------- الخطوة 1: الرقم ----------
    print("\n🔵 الخطوة 1 من 2: رقم الحساب")
    raw = ask("اكتب رقم الحساب التجريبي بالمفتاح الدولي (مثال +9665XXXXXXX)")
    phone = norm_phone(raw)
    log(f"محاولة إرسال كود إلى: {phone}")

    try:
        await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        log(f"ERROR PhoneNumbersInvalidError | أدخل المستخدم: {raw}")
        print("\n❌ الرقم غير صحيح — يجب أن يبدأ بمفتاح الدولة مثل +9665XXXXXXX")
        await client.disconnect()
        return 1
    except errors.FloodWaitError as exc:
        log(f"ERROR FloodWaitError seconds={exc.seconds}")
        print(f"\n⏳ تلغرام يطلب انتظارًا: حاول بعد {exc.seconds} ثانية تقريبًا.")
        await client.disconnect()
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR send_code_request: {type(exc).__name__}: {exc}")
        print(f"\n❌ تعذر إرسال الكود ({type(exc).__name__}) — سأشخّص السجل تلقائيًا.")
        await client.disconnect()
        return 1

    print("\n📩 الكود أُرسل — افتح تلغرام (أو SMS) وابحث عن رسالة من «Telegram».")

    # ---------- الخطوة 2: الكود ----------
    print("🟠 الخطوة 2 من 2: كود الدخول (5 ارقام غالبًا)")
    code = ask("اكتب الكود الذي وصلك")

    try:
        await client.sign_in(phone, code)
    except errors.PhoneCodeInvalidError:
        log("ERROR PhoneCodeInvalidError (كود خاطئ)")
        print("\n❌ الكود غير صحيح — تأكد من الرسالة الجديدة وعد للنافذة وكرر خطواتها.")
        await client.disconnect()
        return 1
    except errors.PhoneCodeExpiredError:
        log("ERROR PhoneCodeExpiredError (كود منتهٍ)")
        print("\n❌ الكود انتهت صلاحيته — شغّل الملف من جديد وأدخل الكود الجديد فورًا.")
        await client.disconnect()
        return 1
    except errors.SessionPasswordNeededError:
        log("مطلوب 2FA (كلمة مرور الحماية)")
        print("\n🔒 الحساب محمي بكلمة تحقق (2FA).")
        password = getpass("🔑 اكتب كلمة 2FA (الكتابة مخفية): ").strip()
        try:
            await client.sign_in(password=password)
        except Exception as exc:  # noqa: BLE001
            log(f"ERROR 2FA wrong password: {type(exc).__name__}")
            print("\n❌ كلمة المرور غير صحيحة — عد وشغّل النافذة وتجرّب مسار جديد.")
            await client.disconnect()
            return 1
    except errors.FloodWaitError as exc:
        log(f"ERROR FloodWaitError seconds={exc.seconds}")
        print(f"\n⏳ انتظر {exc.seconds} ثانية ثم أعد المحاولة.")
        await client.disconnect()
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR sign_in: {type(exc).__name__}: {exc}")
        print(f"\n❌ خطأ غير متوقع ({type(exc).__name__}) — سأشخّصه من سجل المحاولات.")
        await client.disconnect()
        return 1

    me = await client.get_me()
    log(f"SUCCESS login: id={me.id} username={me.username}")
    print("\n✅ تم الدخول بنجاح وحُفظت الجلسة صالحة:")
    _print_me(me)
    print(f"   ملف الجلسة: {SESSION_FILE}")
    print("\n🎉 الآن قل للمساعد «الجلسة جاهزة» وابدأ استلام قائمة المهام.")
    await client.disconnect()
    return 0


def _print_me(me) -> None:
    print(f"   الاسم   : {me.first_name} {me.last_name or ''}")
    if me.username:
        print(f"   المعرف  : @{me.username}")
    print(f"   رقم ID  : {me.id}")


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n🚫 ألغيت العملية.")
        log("user إلغاء من المستخدم (Ctrl+C)")
