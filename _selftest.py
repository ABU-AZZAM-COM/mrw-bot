"""
اختبار سريع بدون تلغرام: يتحقق من أن كل الوحدات تستورد وتعمل معًا.
(التوكن يُزوَّر عبر متغيرات البيئة خلال الاختبار فقط)
"""
import os, time, tempfile, asyncio, json
from pathlib import Path
from types import SimpleNamespace

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

_db_test_dir = tempfile.TemporaryDirectory(prefix="mrw-selftest-")
DB_PATH = Path(_db_test_dir.name) / "test_reports.db"
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

# فحص الترحيب/العرض (نصوص جاهزة) — الترحيب يؤدي إلى القائمة ذات الأزرار
w = texts.WELCOME
v = texts.badge("قيد المراجعة")
assert "منظومة الاستقبال الرقمي" in w and "يرجى اختيار" in w and v == "🟠 قيد المراجعة"

# القائمة الرئيسية: 4 إجراءات بلا زر فتح التطبيق + زر الرجوع يعيد إظهارها
mm = keyboards.main_menu_kb()
actions = [b.callback_data for row in mm.inline_keyboard for b in row]
assert actions == ["usr:report", "usr:status", "usr:about", "usr:contact"], actions
back = keyboards.back_to_menu_kb()
assert back.inline_keyboard[0][0].callback_data == "usr:menu"
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

# ------------------------------------------------------------------
# فحص طبقة النسخ إلى Hub (منطق خالص — بلا أي اتصال)
# ------------------------------------------------------------------
import hub_backup

import config as _cfg

_tok, _repo = _cfg.HF_BACKUP_TOKEN, _cfg.HF_BACKUP_REPO
try:
    # غير مهيأ → كل المسارات ترفض فورًا بلا استثناء
    _cfg.HF_BACKUP_TOKEN = ""
    _cfg.HF_BACKUP_REPO = ""
    assert hub_backup.is_configured() is False
    assert hub_backup.restore_latest_if_needed(DB_PATH) is False
    assert hub_backup.upload_backup(DB_PATH) is False

    # مهيأ + القاعدة المحلية فيها بلاغات → لِماذا؟ الحماية الصارمة:
    # لا استعادة فوق قاعدة حية (يجب ألا يُلمس الملف أبدًا) — وبلا اتصال
    _cfg.HF_BACKUP_TOKEN = "fake-token-for-test"
    _cfg.HF_BACKUP_REPO = "fake/repo"
    assert hub_backup.is_configured() is True
    assert archive._has_rows(DB_PATH) is True
    assert hub_backup.restore_latest_if_needed(DB_PATH) is False
finally:
    _cfg.HF_BACKUP_TOKEN, _cfg.HF_BACKUP_REPO = _tok, _repo
print("hub_backup: حرّاس (غير مهيأ / لا استبدال فوق بيانات) OK")

# ------------------------------------------------------------------
# فحص طبقة واجهة الإدارة (منطق خالص — بلا FastAPI وبلا شبكة)
# ------------------------------------------------------------------
import admin_api
import os as _os

# الحماية بالتوكن: فراغ = مرفوض، عدم مطابقة = مرفوض، مطابقة = مقبول
_os.environ["ADMIN_API_TOKEN"] = "test-token-xyz"
assert admin_api.expected_token() == "test-token-xyz"
assert admin_api.token_ok("", "test-token-xyz") is False
assert admin_api.token_ok("wrong", "test-token-xyz") is False
assert admin_api.token_ok("  test-token-xyz  ", "test-token-xyz") is True
_os.environ.pop("ADMIN_API_TOKEN")
assert admin_api.expected_token() == ""
print("admin_api: حماية التوكن OK")

# أزرار رفض البلاغ من إشعار البوت والتفاصيل تطلب السبب.
notify_callbacks = [
    button.callback_data
    for row in keyboards.admin_notify_buttons(123).inline_keyboard
    for button in row
]
detail_callbacks = [
    button.callback_data
    for row in keyboards.admin_view_buttons(123).inline_keyboard
    for button in row
]
assert "adm:rj:123" in notify_callbacks
assert "adm:rj:123" in detail_callbacks
assert "adm:s:123:rejected" not in detail_callbacks
print("keyboards: الرفض من أزرار البوت يطلب السبب دائمًا")

# أدوات اللوحة على قاعدة اختبار فعلية (محلية مؤقتة)
pbase_tmp = tempfile.TemporaryDirectory(prefix="mrw-panel-selftest-")
pbase = Path(pbase_tmp.name) / "panel.db"
pdb = database.Database(pbase)
pid = asyncio.run(pdb.add_report(
    user_id=777, username="panel_user", full_name="مستخدم لوحة",
    photo_path="media/x.jpg", photo_file_id="fid777",
    post_link="https://t.me/x/9",
    description="بلاغ لتجربة واجهة الإدارة الكاملة هنا بالتفصيل الشديد",
    violation_type="مخالفة زي",
))
rows, total = asyncio.run(pdb.list_reports_filtered(user_id=777))
assert total == 1 and rows[0]["id"] == pid["id"]
rows, total = asyncio.run(pdb.list_reports_filtered(q="واجهة الإدارة"))
assert total >= 1 and rows[0]["user_id"] == 777
rows, total = asyncio.run(pdb.list_reports_filtered(vtype="مخالفة زي"))
assert total >= 1
assert asyncio.run(pdb.set_violation_type(pid["id"], "أخرى")) is True
assert asyncio.run(pdb.set_admin_note(pid["id"], "ملاحظة من اللوحة")) is True
check = asyncio.run(pdb.get_by_id(pid["id"]))
assert check["violation_type"] == "أخرى" and check["admin_note"] == "ملاحظة من اللوحة"

# قائمة المستخدمين المجملة + بطاقة مستخدم + المستخدم الوهمي = None
users_rows, users_total = asyncio.run(pdb.list_users())
assert users_total == 1 and users_rows[0]["user_id"] == 777
profile = asyncio.run(pdb.user_profile(777))
assert profile["user_id"] == 777 and profile["total_reports"] == 1
assert asyncio.run(pdb.user_profile(42424242)) is None

# الحجب: حفظ ثم ظهور في القائمة ثم الرفع ثم الاختفاء
asyncio.run(pdb.save_block(777, 0, "9999-12-31T00:00:00", "اختبار لوحة"))
users_rows, _ = asyncio.run(pdb.list_users())
assert users_rows[0]["frozen_until"] == "9999-12-31T00:00:00"
blocked_rows, blocked_total = asyncio.run(pdb.list_blocked_users())
assert blocked_total == 1
assert blocked_rows[0]["user_id"] == 777
assert blocked_rows[0]["reason"] == "اختبار لوحة"
asyncio.run(pdb.save_block(42424242, 0, "9999-12-31T00:00:00", "بلا بلاغات"))
blocked_rows, blocked_total = asyncio.run(pdb.list_blocked_users())
assert blocked_total == 2
assert {row["user_id"] for row in blocked_rows} == {777, 42424242}
first_page, page_total = asyncio.run(pdb.list_blocked_users(page=0, size=1))
second_page, _ = asyncio.run(pdb.list_blocked_users(page=1, size=1))
assert page_total == 2 and len(first_page) == len(second_page) == 1
assert first_page[0]["user_id"] != second_page[0]["user_id"]
asyncio.run(pdb.save_block(52525252, 0, "2000-01-01T00:00:00", "منتهي"))
active_rows, active_total = asyncio.run(pdb.list_blocked_users())
assert active_total == 2
assert 52525252 not in {row["user_id"] for row in active_rows}
asyncio.run(pdb.clear_block(42424242))
asyncio.run(pdb.clear_block(777))
asyncio.run(pdb.clear_block(52525252))
assert asyncio.run(pdb.get_block(777)).get("frozen_until") in (None, "")

# رفض البلاغ عبر API لا يغيّر الحالة إذا غاب السبب، حتى لو تجاوز تحقق الواجهة.
from fastapi import FastAPI

_bot_main = main_mod

reject_report = asyncio.run(pdb.add_report(
    user_id=909, username="reject_test", full_name="اختبار الرفض",
    photo_path="media/reject.jpg", photo_file_id="reject-file",
    post_link="https://t.me/x/99", description="بلاغ لاختبار إلزام سبب الرفض",
))
api_app = FastAPI()
admin_api.register(api_app)
status_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/reports/{rid}/status"
)
message_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/users/{uid}/message"
)
original_bot_app = _bot_main.app
_bot_main.app = SimpleNamespace(bot_data={"db": pdb}, bot=None)
_os.environ["ADMIN_API_TOKEN"] = "test-token-xyz"

class _Request:
    headers = {"x-admin-token": "test-token-xyz"}
    query_params = {}

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

response = asyncio.run(status_endpoint(
    reject_report["id"], _Request({"status": "rejected"})
))
assert response.status_code == 400
assert json.loads(response.body)["error"] == "reason_required"
assert asyncio.run(pdb.get_by_id(reject_report["id"]))["status"] == database.STATUS_NEW

send_calls = []

async def _fake_send_direct_message(user_id, text):
    send_calls.append((user_id, text))
    return True

original_sender = admin_api._send_direct_message
admin_api._send_direct_message = _fake_send_direct_message
message_response = asyncio.run(message_endpoint(
    777, _Request({"text": "رسالة اختبار"})
))
assert message_response.status_code == 200
assert json.loads(message_response.body)["sent"] is True
assert send_calls == [(777, "رسالة اختبار")]
assert any(
    row["action"] == "user_message" and row["target"] == "777"
    for row in asyncio.run(pdb.recent_audit(20))
)
admin_api._send_direct_message = original_sender
_os.environ.pop("ADMIN_API_TOKEN", None)
_bot_main.app = original_bot_app
print("admin_api: رفض الحالة بلا سبب مرفوض بلا تعديل على البلاغ")
print("admin_api: الرسالة المباشرة تُرسل عبر طبقة البوت وتُسجل")

# الحذف النهائي يعيد True ثم يختفي البلاغ
assert asyncio.run(pdb.delete_report(pid["id"])) is True
assert asyncio.run(pdb.get_by_id(pid["id"])) is None

# --- التغطية الشاملة: تصنيفات/قوالب/إحصاءات/تجميد (منطق القاعدة) ---
# التصنيفات: الافتراضي من الإعداد ثم تعديل فعل ثم رجوع
import config as _cfg_mod

_default = asyncio.run(pdb.effective_vtypes())
assert list(_default) == list(_cfg_mod.VIOLATION_TYPES)
await_none = asyncio.run(pdb.set_vtypes_override(["نوع أ", "نوع ب", "نوع ج"]))
assert asyncio.run(pdb.effective_vtypes()) == ["نوع أ", "نوع ب", "نوع ج"]
await_none = asyncio.run(pdb.set_vtypes_override([]))
assert asyncio.run(pdb.effective_vtypes()) == list(_cfg_mod.VIOLATION_TYPES)

# القوالب الجاهزة: حفظ/قراءة/فراغ
await_none = asyncio.run(pdb.set_templates(["تم قبول بلاغك.", "بلاغك مرفوض لأسباب الشروط."]))
tpl = asyncio.run(pdb.get_templates())
assert tpl == ["تم قبول بلاغك.", "بلاغك مرفوض لأسباب الشروط."]

# إحصاءات: بلاغان فاعلان ثم يوميات/أنواع/أعلى المتبلّغين/شرائح المستخدمين
await_none = asyncio.run(pdb.add_report(
    user_id=888, username="st8", full_name="مستخدم إحصاء",
    photo_path="m/x.jpg", photo_file_id="f8",
    post_link="https://t.me/x/22", description="وصف إحصائي",
    violation_type="مخالفة زي",
))
daily = asyncio.run(pdb.count_daily("2000-01-01"))
assert daily and daily[-1]["n"] >= 1 and len(daily[-1]["day"]) == 10
by_type = asyncio.run(pdb.count_by_type())
assert by_type and by_type[0]["vtype"] == "مخالفة زي"
tops = asyncio.run(pdb.top_users(7))
assert tops[0]["n"] == 1 and tops[0]["user_id"] == 888
ids, overflow = asyncio.run(pdb.all_user_ids(cap=300))
assert 888 in ids and overflow is False
ids, overflow = asyncio.run(pdb.all_user_ids(cap=1))
assert len(ids) <= 1

# مسار القاعدة للتنزيل
assert pdb.path.is_file()
pdb.close()
pbase_tmp.cleanup()
print("admin_api: أدوات اللوحة على القاعدة (بحث/تصنيف/ملاحظة/حجب/حذف) OK")
print("admin_api: تصنيفات ديناميكية + قوالب + إحصاءات + معرفات التعميم OK")

# خادم الصحة: ليس بيئة سحابية محليًا
assert health_server.is_space_environment() is False
print("health_server OK")

print("\n=== كل الاختبارات نجحت ✅ ===")
DB.close()
_db_test_dir.cleanup()
