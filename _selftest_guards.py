"""اختبار ذاتي لطبقة الحماية + التطبيق (جديد)."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_SELFTEST_ONLY")
os.environ.setdefault("ADMIN_IDS", "1557513643")
os.environ.setdefault("SAVE_MEDIA_LOCALLY", "0")

import config  # noqa: E402
import guards  # noqa: E402
from database import Database  # noqa: E402


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "OK" if condition else "FAIL"
    print(f"{mark}: {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        check.failures += 1


check.failures = 0


async def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    db = Database(tmp)

    print("\n--- فحص الروابط ---")
    v = guards.check_link("https://t.me/blgh_bot")
    check("رابط سليم يُقبل", v.allowed)
    v = guards.check_link("javascript:alert(1)")
    check("javascript يُرفض", not v.allowed, v.code)
    v = guards.check_link("http://127.0.0.1/admin")
    check("عنوان داخلي يُرفض", not v.allowed, v.code)
    v = guards.check_link("http://169.254.169.254/latest/meta-data")
    check("عنوان البيانات الوصفية يُرفض", not v.allowed, v.code)
    v = guards.check_link("ftp://example.com/x")
    check("مخطط غير مسموح يُرفض", not v.allowed, v.code)

    print("\n--- تطبيع الروابط وكشف التكرار ---")
    a = guards.normalize_link("https://www.example.com/post/1?utm_source=x&id=5")
    b = guards.normalize_link("https://example.com/post/1?id=5")
    check("الرابطان يطابقان بعد التطبيع", a == b, f"{a} == {b}")
    check("بصمة الرابط متساوية", guards.link_hash(a) == guards.link_hash(b))

    print("\n--- تطبيع النص العربي ---")
    n1 = guards.normalize_text("إعاقةُ الطريق!")
    n2 = guards.normalize_text("اعاقة الطريق")
    check("تطبيع النص العربي يوحّد الهمزات والتشكيل", n1 == n2, f"{n1} | {n2}")

    print("\n--- التشابه ---")
    s = guards.similarity("سيارة تتجاوز السرعة ليلاً أمام الدوار",
                          "سياره تتجاوز السرعة ليلا امام الدوار")
    check("تشابه النص المتشابه مرتفع", s >= 0.85, f"{s:.2f}")
    s2 = guards.similarity("مخالفة إشارة مرورية", "سائق يقود بسرعة عالية ليلاً")
    check("تشابه النصوص المختلفة منخفض", s2 < 0.5, f"{s2:.2f}")

    print("\n--- فحص المضمون ---")
    v = guards.check_substance("قصير")
    check("وصف قصير يُرفض", not v.allowed, v.code)
    v = guards.check_substance("سيارة سبورت تتجاوز السرعة ليلاً أمام دوار الحريص وتتجاوز الحافلة")
    check("وصف مفصّل يُقبل", v.allowed)

    print("\n--- المحادثات الخاصة ---")
    check("المحادثة الخاصة مسموحة", guards.is_private_chat("private"))
    check("المجموعة غير مسموحة", not guards.is_private_chat("group"))
    check("القناة غير مسموحة", not guards.is_private_chat("channel"))

    print("\n--- حد رسائل البوت الصادرة ---")
    limiter = guards.OutgoingLimiter(per_minute=3)
    allowed = [limiter.allow(7) for _ in range(5)]
    check("يسمح بعدد محدود فقط", allowed[:3] == [True, True, True] and allowed[3:] == [False, False])

    print("\n--- حدود المعدّل والتجميد ---")
    await db.log_event(1, "report")
    await db.log_event(1, "report")
    v = await guards.check_rate_limits(db, 1)
    check("تجاوز الحد الزمني في الدقيقة يُمنع", not v.allowed, v.code)
    block = await db.get_block(1)
    check("التجميد مسجّل", bool(block.get("frozen_until")), str(block.get("strikes")))
    v = await guards.check_rate_limits(db, 1)
    check("المستخدم المجمَّد يُمنع فورًا", not v.allowed, v.code)

    print("\n--- الإيقاف الطارئ ---")
    check("ليس في وضع طارئ", not await guards.is_emergency(db))
    await guards.set_emergency(db, True, 1557513643, "اختبار")
    check("تم تفعيل الإيقاف الطارئ", await guards.is_emergency(db))
    await guards.set_emergency(db, False, 1557513643, "اختبار")
    check("تم إيقاف وضع الإيقاف", not await guards.is_emergency(db))

    print("\n--- سجل التدقيق ---")
    rows = await db.recent_audit(5)
    check("سجل التدقيق يسجّل العمليات", len(rows) > 0, f"{len(rows)} عملية")

    print("\n--- قاعدة البيانات: أعمدة التطبيق ---")
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(reports)").fetchall()}
    for col in ("violation_type", "place", "latitude", "longitude", "source",
                "platform", "needs_review", "published"):
        check(f"العمود {col} موجود", col in cols)

    report = await db.add_report(
        user_id=5, username="u", full_name="م", photo_path="", photo_file_id="f",
        post_link="https://t.me/x", description="وصف كامل للمخالفة على الطريق السريع",
        violation_type="تجاوز السرعة", place="دوار الحريص",
        latitude=36.2, longitude=37.1, source="app", needs_review=1,
    )
    check("حفظ بلاغ من التطبيق", report.get("source") == "app", str(report.get("source")))
    check("نوع المخالفة محفوظ", report.get("violation_type") == "تجاوز السرعة")
    check("الإحداثيات محفوظة", report.get("latitude") == 36.2)

    await db.add_fingerprint(report["id"], guards.link_hash("https://t.me/x"),
                             guards.text_hash(report["description"]), None)
    v = await guards.check_duplicate(db, "https://t.me/x", report["description"])
    check("كشف البلاغ المكرر", v.code == "exact_duplicate", v.code)


    print("\n--- كشف منصة التواصل ---")
    check("فيسبوك", guards.detect_platform("https://www.facebook.com/abc") == "فيسبوك")
    check("تيك توك", guards.detect_platform("https://vm.tiktok.com/xyz") == "تيك توك")
    check("إنستغرام", guards.detect_platform("https://instagram.com/p/1") == "إنستغرام")
    check("إكس", guards.detect_platform("https://x.com/user/status/1") == "إكس (تويتر)")
    check("مصدر غير معروف", guards.detect_platform("https://example.org/x") == "")
    v = guards.check_link("https://example.org/x")
    check("رابط غير معروف يُقبل مع تنبيه", v.allowed and v.code == "unknown_platform", v.code)
    v = guards.check_link("https://www.facebook.com/abc")
    check("رابط معروف بلا تنبيه", v.allowed and v.code == "ok", v.code)
    check("المنصة مستخرجة من النتيجة", v.extra.get("platform") == "فيسبوك")

    db.close()
    print("\n" + ("=" * 50))
    if check.failures:
        print(f"❌ فشل {check.failures} اختبار")
        sys.exit(1)
    print("=== كل اختبارات الحماية والتطبيق ناجحة ✅ ===")


asyncio.run(main())
