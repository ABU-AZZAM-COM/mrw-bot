"""
تنظيف قائمة الحظر بالكامل (Unblock All).

- يجلب الحظرين عبر صفحات (100 لكل صفحة).
- يلفت الحظر عن كل مرء (بوتات + حسابات) واحداً تلو الآخر مع تأخير أمان.
- ينتهي بجرد نهائي يجب أن يساوي صفر.
- السجل: data/unblock_log.txt
"""
import asyncio
import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from telethon import TelegramClient, errors, functions

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SESSION_FILE = BASE_DIR / "data" / "user_session.session"
LOG_FILE = BASE_DIR / "data" / "unblock_log.txt"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


async def count_blocked(client) -> int:
    try:
        res = await client(functions.contacts.GetBlockedRequest(offset=0, limit=1))
        return getattr(res, "count", 0)
    except Exception:
        return 0


async def main() -> int:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id:
        log("ERROR: لا API_ID")
        return 1

    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
    await client.connect()

    before = await count_blocked(client)
    log(f"🚫 عدد المحظورين قبل: {before}")
    if before == 0:
        print("✅ قائمة الحظر فارغة أصلًا.")
        await client.disconnect()
        return 0

    # مسار 1: جلب الصفحات وفتح الحظر بالمرجع peer_id
    unblocked, failed, seen = 0, 0, 0
    while True:
        try:
            res = await client(functions.contacts.GetBlockedRequest(offset=0, limit=100))
        except Exception as exc:  # noqa: BLE001
            log(f"❌ فشل جلب قائمة الحظر: {type(exc).__name__}: {exc}")
            break

        blocked = getattr(res, "blocked", [])
        if not blocked:
            break

        for b in blocked:
            seen += 1
            peer = getattr(b, "peer_id", None)
            try:
                input_peer = await client.get_input_entity(peer)
                await client(functions.contacts.UnblockRequest(id=input_peer))
                unblocked += 1
                log(f"   [{seen}] فُكّ حظر: {peer.user_id if hasattr(peer, 'user_id') else peer}")
            except errors.FloodWaitError as exc:
                log(f"   ⏳ انتظار {exc.seconds} ث…")
                await asyncio.sleep(exc.seconds + 2)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"   ❌ فشل فك الحظر: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.25)

        if getattr(res, "left", 0) == 0 or len(blocked) < 100:
            break

    # مسار 2 إجباري: مراجعة ما بقى (قد تكون الصفحة الأولى تجددت أثناء الحذف)
    remaining = await count_blocked(client)
    guard, extra_fails = 0, 0
    while remaining > 0 and guard < 5:
        guard += 1
        try:
            res = await client(functions.contacts.GetBlockedRequest(offset=0, limit=100))
            for b in getattr(res, "blocked", []):
                try:
                    input_peer = await client.get_input_entity(getattr(b, "peer_id"))
                    await client(functions.contacts.UnblockRequest(id=input_peer))
                    await asyncio.sleep(0.25)
                except Exception:
                    extra_fails += 1
        except Exception:
            break
        remaining = await count_blocked(client)

    log(f"🎯 النتيجة: فُكّ {unblocked} + مراجعة إضافية | ناجح | فشل {failed} | متبقي {remaining}")
    print()
    if remaining == 0:
        print(f"✅ قائمة الحظر أصبحت فارغة تمامًا (فُكّ {unblocked + (0 if failed == 0 else 0)} حظرًا تقريبًا).")
    else:
        print(f"⚠️ بقى {remaining} غير قادر على فكّه آليًا — سأفصّصه لك بالأسماء.")
        # عرض ما بقى
        try:
            res = await client(functions.contacts.GetBlockedRequest(offset=0, limit=100))
            for b in getattr(res, "blocked", []):
                try:
                    u = await client.get_entity(getattr(b, "peer_id"))
                    print(f"   - {getattr(u, 'first_name', '') or u.id} | @{getattr(u, 'username', '—')} | id={u.id}")
                except Exception:
                    print(f"   - كيان غير قابل للفصل (id عبر peer)")
        except Exception:
            pass

    await client.disconnect()
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
