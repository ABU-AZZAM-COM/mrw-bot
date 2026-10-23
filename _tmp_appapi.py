"""اختبار مسار بيانات التطبيق الخادمي (توقيع initData + الحقن)."""

import hashlib
import hmac
import json
import time
import urllib.parse

import httpx

BASE = "http://127.0.0.1:7879"
TOKEN = "123456:TEST"


def sign(fields: dict, token: str) -> str:
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields) + "&hash=" + digest


def make_init(user_id: int = 111, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAHtest",
        "user": json.dumps({"id": user_id, "first_name": "تجربة", "username": "test"}, ensure_ascii=False),
    }
    return sign(fields, TOKEN)


PAYLOAD = {"v": 1, "type": "تصوير مواقع", "desc": "اختبار", "link": "https://x.com/a/status/1"}


def post(body: dict) -> tuple[int, str]:
    r = httpx.post(BASE + "/api/app-data", json=body, timeout=20)
    return r.status_code, r.text


print("1) بلا بيانات      :", post({}))
print("2) توقيع خاطئ      :", post({"init_data": make_init() + "bad", "payload": PAYLOAD}))
print("3) توقيع منتهٍ      :", post({"init_data": make_init(auth_date=int(time.time()) - 200000), "payload": PAYLOAD}))
print("4) توقيع صحيح      :", post({"init_data": make_init(), "payload": PAYLOAD}))
print("5) payload غير صالح:", post({"init_data": make_init(), "payload": "x"}))
