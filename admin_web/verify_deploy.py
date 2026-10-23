#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تحقق مضمونة — لا تستخدم /health أبدًا. فقط /api/admin/vtypes."""
import urllib.request, json, sys

URL = "https://azzam1992-mrw-bot.hf.space/api/admin/vtypes"
TOKEN = "jrPIesNFa7iAgQbO2cWXZT5A054SHzn0"

def check():
    try:
        req = urllib.request.Request(URL, headers={"X-Admin-Token": TOKEN})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        print(f"STATUS: {resp.status} | ITEMS: {len(data)}")
        for it in data[:4]:
            name = (it.get("name") or it.get("label") or it.get("type") or "—")
            print(f"  - {it.get('type')} | {name}")
        return resp.status == 200 and len(data) >= 8
    except Exception as e:
        print(f"FAIL: {e}")
        return False

if __name__ == "__main__":
    ok = check()
    sys.exit(0 if ok else 1)
