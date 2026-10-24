"""اختبار حيّ لمسار التطبيق على المساحة (توقيع حقيقي بمفتاح البوت)."""

import hashlib
import hmac
import json
import os
import time
import urllib.parse

import httpx

BASE = "https://azzam1992-mrw-bot.hf.space"
TOKEN = os.environ.get("BOT_TOKEN", "")

PAYLOAD = {
    "v": 1,
    "type": "تصوير مواقع ومنشآت عسكرية",
    "place": "اختبار آلي — بلا صورة",
    "desc": "اختبار مسار الخادم للتطبيق dontsend",
    "link": "https://x.com/example/status/123456789",
    "platform": "X",
    "lat": None,
    "lon": None,
    "photo": 0,
    "ts": int(time.time()),
}


def sign(fields: dict) -> str:
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields) + "&hash=" + digest


def init_data(user_id: int) -> str:
    return sign({
        "auth_date": str(int(time.time())),
        "query_id": "AAHlive",
        "user": json.dumps({"id": user_id, "first_name": "اختبار", "username": "live_test"}, ensure_ascii=False),
    })


for attempt in range(20):
    try:
        r = httpx.get(BASE + "/health", timeout=20)
        if r.status_code == 200:
            print("الصحة:", r.text)
            break
    except Exception as exc:  # noqa: BLE001
        print("انتظار…", type(exc).__name__)
    time.sleep(15)

# 1) مفتاح مزوّر → يجب الرفض
bad = init_data(700000001).replace("&hash=", "&hash=0")
r1 = httpx.post(BASE + "/api/app-data", json={"init_data": bad, "payload": PAYLOAD}, timeout=30)
print("توقيع مزوّر :", r1.status_code, r1.text)

# 2) توقيع صحيح لمستخدم غير موجود → يقبل الحقن (البوت سيفشل في الرد: chat not found)
r2 = httpx.post(BASE + "/api/app-data", json={"init_data": init_data(700000001), "payload": PAYLOAD}, timeout=30)
print("توقيع صحيح :", r2.status_code, r2.text)

# 3) بلا توقيع
r3 = httpx.post(BASE + "/api/app-data", json={"init_data": "", "payload": PAYLOAD}, timeout=30)
print("بلا توقيع  :", r3.status_code, r3.text)
