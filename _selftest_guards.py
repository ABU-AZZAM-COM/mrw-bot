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
import image_moderation  # noqa: E402
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

    print("\n--- فحص الصور المحلي ---")
    old_enabled = os.environ.get("NSFW_SCREENING_ENABLED")
    old_threshold = os.environ.get("NSFW_SCREENING_THRESHOLD")
    old_review_threshold = os.environ.get("NSFW_REVIEW_THRESHOLD")
    import cv2
    import numpy as np

    test_image = tmp.parent / "test.jpg"
    check("إنشاء صورة اختبار للفحص المحلي", cv2.imwrite(
        str(test_image), np.full((320, 320, 3), 255, dtype=np.uint8)
    ))
    os.environ.pop("NSFW_SCREENING_ENABLED", None)
    check("الفحص الآلي مفعّل افتراضيًا", image_moderation.is_enabled())
    os.environ["NSFW_SCREENING_ENABLED"] = "0"
    check("يمكن تعطيل الفحص صراحةً", not image_moderation.is_enabled())
    old_detector = image_moderation._detector
    image_moderation._detector = type(
        "FakeDetector",
        (),
        {"detect": lambda self, _path: [
            {"class": "FEMALE_BREAST_EXPOSED", "score": 0.91},
            {"class": "FEMALE_GENITALIA_EXPOSED", "score": 0.91},
            {"class": "FEMALE_BREAST_COVERED", "score": 0.99},
        ]},
    )()
    os.environ["NSFW_SCREENING_ENABLED"] = "1"
    os.environ["NSFW_SCREENING_THRESHOLD"] = "0.85"
    os.environ["NSFW_REVIEW_THRESHOLD"] = "0.40"
    screen = image_moderation.screen_image(test_image)
    check(
        "كشف الأعضاء التناسلية عالي الثقة يميّز الحالة الصريحة",
        screen.flagged
        and screen.review_required
        and screen.labels == ("FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED"),
    )
    os.environ["NSFW_SCREENING_THRESHOLD"] = "0.95"
    os.environ["NSFW_REVIEW_THRESHOLD"] = "0.40"
    under_cutoff = image_moderation.screen_image(test_image)
    check(
        "الكشف غير المؤكد يُحال للمراجعة ولا يسبب تجميدًا",
        not under_cutoff.flagged
        and under_cutoff.review_required
        and "FEMALE_GENITALIA_EXPOSED" in under_cutoff.labels,
    )
    image_moderation._detector = type(
        "ReviewDetector",
        (),
        {"detect": lambda self, _path: [
            {"class": "FEMALE_BREAST_EXPOSED", "score": 0.96},
        ]},
    )()
    check(
        "مؤشر الثدي وحده يحتاج مراجعة ولا يسبب حظرًا دائمًا",
        not image_moderation.screen_image(test_image).flagged
        and image_moderation.screen_image(test_image).review_required,
    )
    tiled_image = tmp.parent / "multiscale-test.jpg"
    check("إنشاء صورة اختبار متعددة الدقة", cv2.imwrite(
        str(tiled_image), np.full((800, 800, 3), 255, dtype=np.uint8)
    ))

    class MultiScaleDetector:
        def __init__(self):
            self.calls = 0

        def detect(self, _path):
            self.calls += 1
            if self.calls == 3:
                return [{"class": "MALE_GENITALIA_EXPOSED", "score": 0.93}]
            return []

    multi_scale_detector = MultiScaleDetector()
    image_moderation._detector = multi_scale_detector
    os.environ["NSFW_SCREENING_THRESHOLD"] = "0.85"
    multi_scale_result = image_moderation.screen_image(tiled_image)
    check(
        "المسح المتعدد يكتشف المؤشر في قصاصة مكبرة",
        multi_scale_detector.calls == 5
        and multi_scale_result.flagged
        and multi_scale_result.max_score == 0.93,
    )
    image_moderation._detector = old_detector
    if old_enabled is None:
        os.environ.pop("NSFW_SCREENING_ENABLED", None)
    else:
        os.environ["NSFW_SCREENING_ENABLED"] = old_enabled
    if old_threshold is None:
        os.environ.pop("NSFW_SCREENING_THRESHOLD", None)
    else:
        os.environ["NSFW_SCREENING_THRESHOLD"] = old_threshold
    if old_review_threshold is None:
        os.environ.pop("NSFW_REVIEW_THRESHOLD", None)
    else:
        os.environ["NSFW_REVIEW_THRESHOLD"] = old_review_threshold

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

    print("\n--- إيقاف التجميد التلقائي مؤقتًا ---")
    pause_until = await guards.set_auto_freeze_pause(db, 5, 1557513643, "اختبار")
    check("الإيقاف المؤقت له موعد انتهاء", bool(pause_until))
    await db.log_event(2, "report")
    await db.log_event(2, "report")
    v = await guards.check_rate_limits(db, 2)
    check("حد المحاولات يبقى فعالًا أثناء الإيقاف", not v.allowed, v.code)
    block = await db.get_block(2)
    check("لا يُنشأ تجميد جديد أثناء الإيقاف", not block.get("frozen_until"))
    await guards.resume_auto_freeze(db, 1557513643, "اختبار")
    check("استئناف التجميد يزيل الإيقاف", not await guards.auto_freeze_pause_until(db))

    print("\n--- إيقاف حدود المستخدم مؤقتًا ---")
    await db.log_event(3, "report")
    await db.log_event(3, "report")
    until = await guards.set_user_limits_pause(db, 5, 1557513643, "اختبار")
    v = await guards.check_rate_limits(db, 3)
    check("حدود الدقيقة تتوقف ضمن المدة المحددة", v.allowed, v.code)
    v = await guards.check_daily_limit(db, 3)
    check("الحد اليومي يتوقف مؤقتًا", v.allowed, v.code)
    check("موعد انتهاء الإيقاف محفوظ", until == await guards.user_limits_pause_until(db))
    await guards.resume_user_limits(db, 1557513643, "اختبار")
    check("استئناف حدود المستخدم يمسح الإيقاف", not await guards.user_limits_pause_until(db))

    from datetime import datetime, timedelta, timezone

    await db.set_state(
        "user_limits_paused_until",
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    check("ينتهي الإيقاف تلقائيًا", not await guards.user_limits_pause_until(db))
    check("تُمسح الحالة المنتهية", await db.get_state("user_limits_paused_until") is None)

    old_freeze_after = config.FREEZE_AFTER_STRIKES
    config.FREEZE_AFTER_STRIKES = 2
    await db.log_event(4, "report")
    await db.log_event(4, "report")
    await guards.check_rate_limits(db, 4)
    block = await db.get_block(4)
    check("يمكن تأخير التجميد حتى عتبة المخالفات", block.get("strikes") == 1 and not block.get("frozen_until"))
    await guards._register_strike(db, 4, "اختبار العتبة")
    check("يُطبق التجميد عند بلوغ العتبة", bool((await db.get_block(4)).get("frozen_until")))
    config.FREEZE_AFTER_STRIKES = old_freeze_after
    await db.clear_block(4)

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
        post_link="https://t.me/x",
        description="سيارة سبورت تتجاوز السرعة ليلاً أمام دوار الحريص وتتجاوز الحافلة",
        violation_type="تجاوز السرعة", place="دوار الحريص",
        latitude=36.2, longitude=37.1, source="app", needs_review=1,
    )
    check("حفظ بلاغ من التطبيق", report.get("source") == "app", str(report.get("source")))
    check("نوع المخالفة محفوظ", report.get("violation_type") == "تجاوز السرعة")
    check("الإحداثيات محفوظة", report.get("latitude") == 36.2)

    await db.add_fingerprint(report["id"], guards.link_hash("https://t.me/x"),
                             guards.text_hash(report["description"]), None)
    link_v, substance_v, duplicate_v = await guards.inspect_report_content(
        db, "https://t.me/x", report["description"]
    )
    check(
        "توحيد فحوص الرابط والوصف والتكرار",
        link_v.allowed and substance_v is not None and substance_v.allowed
        and duplicate_v is not None and duplicate_v.code == "exact_duplicate",
        duplicate_v.code if duplicate_v else "no duplicate verdict",
    )


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

    old_global_day_limit = config.MAX_REPORTS_PER_DAY_GLOBAL
    config.MAX_REPORTS_PER_DAY_GLOBAL = 1
    daily_global = await guards.check_daily_limit(db, 10)
    check("حد الإغراق اليومي العام يوقف الاستقبال", daily_global.code == "global_daily_flood")
    await db.set_state("emergency", None)
    config.MAX_REPORTS_PER_DAY_GLOBAL = old_global_day_limit

    db.close()
    print("\n" + ("=" * 50))
    if check.failures:
        print(f"❌ فشل {check.failures} اختبار")
        sys.exit(1)
    print("=== كل اختبارات الحماية والتطبيق ناجحة ✅ ===")


asyncio.run(main())
