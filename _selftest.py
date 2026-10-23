"""
اختبار سريع بدون تلغرام: يتحقق من أن كل الوحدات تستورد وتعمل معًا.
(التوكن يُزوَّر عبر متغيرات البيئة خلال الاختبار فقط)
"""
import os, time, tempfile, asyncio
from pathlib import Path

os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN_FOR_IMPORT_CHECK"
os.environ["ADMIN_IDS"] = "111111111"

import config
import texts
import utils
import database
import keyboards
import exporters
import handlers
import admin
import archive
import health_server
import main as main_mod

DB_PATH = config.DATA_DIR / "test_reports.db"
if DB_PATH.exists():
    DB_PATH.unlink()
DB = database.Database(DB_PATH)

fake_row = dict(
    user_id=999,
    username="tester",
    full_name="مجرم؟ لا، مبلّغ تجريبي",
    photo_path=r"media/fake.jpg",
    photo_file_id="fid",
    post_link="https://t.me/somechannel/123",
    description="منشور يحتوي محتوى مخالف =:= خطير",
)

r1 = asyncio.run(DB.add_report(**fake_row))
r2 = asyncio.run(DB.add_report(**fake_row))
assert r1["status"] == "جديد" and r1["seq"].startswith("MRV-"), r1
print("add_report OK:", r1["seq"], "|", r2["seq"])

rows, total = asyncio.run(DB.list_reports(None, 0, 5))
assert total == 2 and rows[0]["seq"] == r2["seq"], (total, rows)
print("list_reports OK, total =", total)

ok = asyncio.run(DB.update_status(r2["id"], "قيد المراجعة", admin_id=111111111))
assert ok, "update_status failed"
r2b = asyncio.run(DB.get_by_id(r2["id"]))
assert r2b["status"] == "قيد المراجعة" and r2b["reviewed_by"] == 111111111
print("update_status OK ->", r2b["status"])

counts = asyncio.run(DB.count_by_status())
tot = asyncio.run(DB.total())
assert tot == 2 and counts.get("جديد") == 1 and counts.get("قيد المراجعة") == 1, (counts, tot)
print("stats OK:", counts, "total =", tot)

n24 = asyncio.run(DB.count_since(utils.utc_now_iso()))
day = asyncio.run(DB.count_since(utils.local_day_start_iso(), user_id=999))
assert n24 == 2 and day == 2, (n24, day)
print("count_since OK (24h =", n24, "| today per-user =", day, ")")

exports = Path(tempfile.mkdtemp())
xlsx = exporters.export_excel(asyncio.run(DB.all_reports()), out_dir=exports)
txt = exporters.export_text(asyncio.run(DB.all_reports()), out_dir=exports)
assert xlsx.exists() and txt.exists() and xlsx.stat().st_size > 4000 and txt.stat().st_size > 500
print("exporters OK:", xlsx.name, "|", txt.name)

# فحص الترحيب/العرض (نصوص جاهزة) — الترحيب الآن يقود لقائمة الأزرار
w = texts.WELCOME.format(name="أحمد")
v = texts.badge("قيد المراجعة")
assert "إرسال بلاغ جديد" in w and "فتح تطبيق البلاغ" in w and v == "🟠 قيد المراجعة"
print("texts OK")

# ------------------------------------------------------------------
# فحص طبقة الأرشفة (بدون اتصال بتليغرام)
# ------------------------------------------------------------------
cap = texts.ARCHIVE_REPORT.format(
    seq="MRV-2026-0001",
    name="مبلّغ تجريبي",
    username="@tester",
    time="2026-10-22 09:00",
    anchor="<a href=\"https://t.me/x/1\">المنشور</a>",
    desc="وصف المخالفة",
)
assert "MRV-2026-0001" in cap and "وصف المخالفة" in cap, cap
assert "<b>" in cap and "{seq}" not in cap, cap
print("ARCHIVE_REPORT OK")

cid = texts.CHAT_ID.format(chat_id=-100123, title="قناة", chat_type="channel", user_id=5)
assert "-100123" in cid, cid
print("CHAT_ID OK")

# النسخ الاحتياطي locally (بلا قناة) يجب أن يعطي False بلا أخطاء
assert asyncio.run(archive.backup_database(None, DB_PATH)) is False
assert asyncio.run(archive.post_report(None, r1)) is False
print("archive: بلا ARCHIVE_CHAT_ID لا يفعل شيئًا (سلوك صحيح)")

# نسخة متسقة من القاعدة + فحص وجود صفوف
snap = Path(tempfile.mkdtemp()) / "snap.db"
assert archive._snapshot(DB_PATH, snap) and snap.exists()
assert archive._has_rows(snap) is True
print("archive: النسخ المتسق وفحص الصفوف OK")

# خادم الصحة: ليس بيئة سحابية محليًا
assert health_server.is_space_environment() is False
print("health_server OK")

print("\n=== كل الاختبارات نجحت ✅ ===")
DB.close()
