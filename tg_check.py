"""
فحص الجلسة المحلية: يتحقق أن الجلسة صالحة ويعرض معلومات الحساب بشكل مختصر.
(يعمل دون أي إدخال — آمن للتشغيل في أي وقت)
"""
from pathlib import Path

from telethon.sync import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SESSION_FILE = BASE_DIR / "data" / "user_session.session"


def main() -> None:
    import os

    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()

    if not api_id or not api_hash or not SESSION_FILE.exists():
        print("❌ لا توجد جلسة محفوظة بعد — شغّل (1-تسجيل-الدخول.bat) أولًا")
        return

    with TelegramClient(str(SESSION_FILE), int(api_id), api_hash) as client:
        me = client.get_me()
        dialogs_count = len(client.get_dialogs())

        print("✅ الجلسة صالحة وتعمل")
        print(f"   الاسم   : {me.first_name} {me.last_name or ''}")
        print(f"   المعرف  : @{me.username}" if me.username else "   المعرف  : (بدون اسم مستخدم)")
        print(f"   رقم ID  : {me.id}")
        print(f"   المحادثات: {dialogs_count} محادثة")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ فشل الفحص: {exc}")
