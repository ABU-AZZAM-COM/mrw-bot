#!/usr/bin/env python3
"""
نشاط خفيف مستمر، يمنع Oracle من استرداد النسخة المجانية الخاملة.

الآلية: Oracle قد تستعيد (reclaim) النسخ المجانية Always Free إذا انخفض
استخدام المعالج أقل من 10% واستخدام الشبكة أقل من 20% لمدة 7 أيام متصلة.
لذلك نُشغّل مهمة خفيفة كل 5 دقائق: عمل حسابي موزّع على كل الأنوية
مع تنزيل بيانات قليلة — لرفع متوسط الاستهلاك فوق الحد الآمن.

هذا لا يعرقل عمل البوت إطلاقًا، واستهلاكه في حدود بسيطة جدًا.
"""
import hashlib
import multiprocessing as mp
import os
import time
import urllib.request


def burn_cpu(seconds: float) -> None:
    """عمل حسابي متواصل (SHA-256) لمدة محددة."""
    end = time.time() + seconds
    block = os.urandom(64)
    digest = block
    while time.time() < end:
        for _ in range(2000):
            digest = hashlib.sha256(digest + block).digest()
    _ = digest


def net_activity() -> None:
    """تنزيل صغير عبر الشبكة لقياس النشاط (يُتجاهل الفشل)."""
    url = "https://speed.cloudflare.com/__down?bytes=1048576"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mrw-keepalive/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            total = 0
            while total < 1_048_576:
                chunk = resp.read(65_536)
                if not chunk:
                    break
                total += len(chunk)
    except Exception:
        pass  # الفشل في الشبكة لا يمنع اكتمال المهمة


def main() -> None:
    cores = os.cpu_count() or 1
    # نسبة عمل ~18% من الزمن لكل نواة في كل دورة (فتتجاوز حد الـ10%)
    work_seconds = 55.0
    print(f"[keepalive] الأنوية={cores} مدة العمل={work_seconds:.0f}ث", flush=True)

    procs = [mp.Process(target=burn_cpu, args=(work_seconds,)) for _ in range(cores)]
    for p in procs:
        p.start()
    net_activity()
    for p in procs:
        p.join()
    print("[keepalive] انتهت الدورة بنجاح", flush=True)


if __name__ == "__main__":
    main()
