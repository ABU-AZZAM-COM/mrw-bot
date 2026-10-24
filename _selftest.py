"""
اختبار سريع بدون تلغرام: يتحقق من أن كل الوحدات تستورد وتعمل معًا.
(التوكن يُزوَّر عبر متغيرات البيئة خلال الاختبار فقط)
"""
import os, time, tempfile, asyncio, json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN_FOR_IMPORT_CHECK"
os.environ["ADMIN_IDS"] = "111111111"

import config
import guards
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
import sightengine_moderation
from unittest.mock import AsyncMock, patch

assert not sightengine_moderation.parse_scores({
    "status": "success",
    "nudity": {"raw": 0.70, "sexual_activity": 0.10},
}).is_explicit
assert sightengine_moderation.parse_scores({
    "status": "success",
    "nudity": {"raw": 0.7001, "sexual_activity": 0.10},
}).is_explicit
assert sightengine_moderation.parse_scores({
    "status": "success",
    "nudity": {"raw": 0.10, "sexual_activity": 0.71},
}).is_explicit

_sightengine_request = {}

class _SightengineResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "status": "success",
            "nudity": {"raw": 0.2, "sexual_activity": 0.1},
        }

class _SightengineClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, data, files):
        _sightengine_request.update(
            url=url,
            data=data,
            filename=files["media"][0],
            content=files["media"][1].read(),
        )
        return _SightengineResponse()

_old_sightengine_user = os.environ.get("SIGHTENGINE_API_USER")
_old_sightengine_secret = os.environ.get("SIGHTENGINE_API_SECRET")
os.environ["SIGHTENGINE_API_USER"] = "test-user"
os.environ["SIGHTENGINE_API_SECRET"] = "test-secret"
with tempfile.TemporaryDirectory(prefix="mrw-sightengine-selftest-") as sightengine_dir:
    sightengine_image = Path(sightengine_dir) / "test.jpg"
    sightengine_image.write_bytes(b"api-test-image")
    with patch.object(sightengine_moderation.httpx, "AsyncClient", _SightengineClient):
        _api_scores = asyncio.run(
            sightengine_moderation.inspect_image(sightengine_image)
        )
assert _api_scores.raw == 0.2 and _api_scores.sexual_activity == 0.1
assert _sightengine_request["url"] == sightengine_moderation.SIGHTENGINE_URL
assert _sightengine_request["data"]["models"] == "nudity-2.0"
assert _sightengine_request["content"] == b"api-test-image"
if _old_sightengine_user is None:
    os.environ.pop("SIGHTENGINE_API_USER", None)
else:
    os.environ["SIGHTENGINE_API_USER"] = _old_sightengine_user
if _old_sightengine_secret is None:
    os.environ.pop("SIGHTENGINE_API_SECRET", None)
else:
    os.environ["SIGHTENGINE_API_SECRET"] = _old_sightengine_secret
print("Sightengine API request and nudity-score threshold parsing OK")

_old_sightengine_user = os.environ.get("SIGHTENGINE_API_USER")
_old_sightengine_secret = os.environ.get("SIGHTENGINE_API_SECRET")
os.environ["SIGHTENGINE_API_USER"] = "test-user"
os.environ["SIGHTENGINE_API_SECRET"] = "test-secret"
with tempfile.TemporaryDirectory(prefix="mrw-private-report-selftest-") as image_dir:
    uploaded_image = Path(image_dir) / "report.jpg"
    uploaded_image.write_bytes(b"private-report-image")
    with (
        patch.object(
            handlers.image_moderation,
            "screen_image",
            return_value=handlers.image_moderation.ScreeningResult(False),
        ),
        patch.object(
            sightengine_moderation,
            "inspect_image",
            new=AsyncMock(
                return_value=sightengine_moderation.NudityScores(0.71, 0.10)
            ),
        ),
    ):
        _private_report_screening = asyncio.run(
            handlers._screen_uploaded_photo(
                media=None,
                photo_path=str(uploaded_image),
                extension=".jpg",
                user_id=654321,
            )
        )
    assert _private_report_screening.flagged
    assert "SIGHTENGINE_RAW" in _private_report_screening.labels
    flagged_result = _private_report_screening

    with (
        patch.object(
            handlers.image_moderation,
            "screen_image",
            return_value=handlers.image_moderation.ScreeningResult(False),
        ),
        patch.object(
            sightengine_moderation,
            "inspect_image",
            new=AsyncMock(
                return_value=sightengine_moderation.NudityScores(0.70, 0.10)
            ),
        ),
    ):
        _threshold_report_screening = asyncio.run(
            handlers._screen_uploaded_photo(
                media=None,
                photo_path=str(uploaded_image),
                extension=".jpg",
                user_id=654321,
            )
        )
    assert not _threshold_report_screening.flagged

    class _PrivateTelegramFile:
        async def download_to_drive(self, custom_path):
            Path(custom_path).write_bytes(b"private-temporary-image")

    class _PrivateMedia:
        async def get_file(self):
            return _PrivateTelegramFile()

    _temporary_scan_paths = []

    async def _inspect_temporary_image(path):
        _temporary_scan_paths.append(Path(path))
        assert Path(path).read_bytes() == b"private-temporary-image"
        return sightengine_moderation.NudityScores(0.1, 0.1)

    with (
        patch.object(
            handlers.image_moderation,
            "screen_image",
            return_value=handlers.image_moderation.ScreeningResult(False),
        ),
        patch.object(
            sightengine_moderation,
            "inspect_image",
            new=_inspect_temporary_image,
        ),
    ):
        asyncio.run(
            handlers._screen_uploaded_photo(
                media=_PrivateMedia(),
                photo_path="",
                extension=".jpg",
                user_id=654321,
            )
        )
    assert _temporary_scan_paths and all(
        not path.exists() for path in _temporary_scan_paths
    )
if _old_sightengine_user is None:
    os.environ.pop("SIGHTENGINE_API_USER", None)
else:
    os.environ["SIGHTENGINE_API_USER"] = _old_sightengine_user
if _old_sightengine_secret is None:
    os.environ.pop("SIGHTENGINE_API_SECRET", None)
else:
    os.environ["SIGHTENGINE_API_SECRET"] = _old_sightengine_secret
print("private complaint image: Sightengine >70% freezes; exactly 70% does not")

_db_test_dir = tempfile.TemporaryDirectory(prefix="mrw-selftest-")
DB_PATH = Path(_db_test_dir.name) / "test_reports.db"
DB = database.Database(DB_PATH)

freeze_alerts = []
freeze_notices = []

async def _capture_freeze_alert(**kwargs):
    freeze_alerts.append(kwargs)

async def _capture_freeze_notice(text, **kwargs):
    freeze_notices.append((text, kwargs))

freeze_user = SimpleNamespace(id=90909090, full_name="مستخدم اختبار")
freeze_update = SimpleNamespace(
    effective_user=freeze_user,
    effective_message=SimpleNamespace(reply_text=_capture_freeze_notice),
)
freeze_context = SimpleNamespace(
    bot_data={"db": DB},
    bot=SimpleNamespace(send_message=_capture_freeze_alert),
    user_data={"app_draft": {"description": "should be discarded"}},
)
handlers._pending_app_users.add(freeze_user.id)
asyncio.run(
    handlers._freeze_for_sensitive_image(
        freeze_update,
        freeze_context,
        handlers.image_moderation.ScreeningResult(
            True,
            review_required=True,
            labels=("FEMALE_GENITALIA_EXPOSED",),
            max_score=0.97,
        ),
    )
)
automatic_block = asyncio.run(DB.get_block(freeze_user.id))
assert automatic_block["frozen_until"] == "9999-12-31T00:00:00"
assert automatic_block["reason"].startswith("كشف آلي")
assert not freeze_context.user_data and freeze_user.id not in handlers._pending_app_users
assert len(freeze_alerts) == len(config.ADMIN_IDS) and len(freeze_notices) == 1
assert all("photo" not in alert for alert in freeze_alerts)
assert any(
    row["action"] == "automated_sensitive_image_freeze"
    and row["target"] == str(freeze_user.id)
    for row in asyncio.run(DB.recent_audit(20))
)
blocked_rows, _ = asyncio.run(DB.list_blocked_users())
assert any(row["user_id"] == freeze_user.id for row in blocked_rows)
blocked_verdict = asyncio.run(guards.check_rate_limits(DB, freeze_user.id))
assert blocked_verdict.blocked, blocked_verdict
asyncio.run(DB.clear_block(freeze_user.id))
assert asyncio.run(guards.check_rate_limits(DB, freeze_user.id)).allowed
print("automatic explicit-image freeze persists, audits, alerts without forwarding image, and admin unblock restores access")

original_save_photo = handlers._save_photo

async def _fake_flagged_photo(_update, _context):
    return "", "test-file-id", "test-file-id", flagged_result

handlers._save_photo = _fake_flagged_photo
try:
    conversation_user = SimpleNamespace(id=90909091, full_name="مستخدم محادثة")
    conversation_context = SimpleNamespace(
        bot_data={"db": DB},
        bot=SimpleNamespace(send_message=_capture_freeze_alert),
        user_data={"description": "must be discarded"},
    )
    conversation_update = SimpleNamespace(
        message=SimpleNamespace(),
        effective_user=conversation_user,
        effective_message=SimpleNamespace(reply_text=_capture_freeze_notice),
    )
    conversation_state = asyncio.run(
        handlers.receive_photo(conversation_update, conversation_context)
    )
    assert conversation_state == handlers.ConversationHandler.END

    app_user = SimpleNamespace(id=90909092, full_name="مستخدم التطبيق")
    app_context = SimpleNamespace(
        bot_data={"db": DB},
        bot=SimpleNamespace(send_message=_capture_freeze_alert),
        user_data={"app_draft": {"post_link": "https://example.test/post"}},
    )
    handlers._pending_app_users.add(app_user.id)
    app_update = SimpleNamespace(
        message=SimpleNamespace(),
        effective_user=app_user,
        effective_message=SimpleNamespace(reply_text=_capture_freeze_notice),
    )
    assert asyncio.run(handlers.app_photo_received(app_update, app_context)) is None
    assert not asyncio.run(DB.list_user_reports(conversation_user.id))[0]
    assert not asyncio.run(DB.list_user_reports(app_user.id))[0]
    assert not app_context.user_data and app_user.id not in handlers._pending_app_users
finally:
    handlers._save_photo = original_save_photo
print("conversation and Mini App photo flows freeze and stop without saving a report")

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

review_photo_path = Path(_db_test_dir.name) / "review-image.jpg"
review_photo_path.write_bytes(b"test-image")
review_report = {
    **r2,
    "photo_path": str(review_photo_path),
    "photo_file_id": "review-file-id",
    "admin_note": "مؤشر تعرٍّ يحتاج مراجعة",
    "needs_review": 1,
}
review_notifications = []

async def _capture_review_photo(**kwargs):
    review_notifications.append(("photo", kwargs))

async def _capture_review_message(**kwargs):
    review_notifications.append(("message", kwargs))

review_context = SimpleNamespace(
    bot=SimpleNamespace(
        send_photo=_capture_review_photo,
        send_message=_capture_review_message,
    )
)
asyncio.run(handlers._notify_admins(review_context, review_report))
assert len(review_notifications) == len(config.ADMIN_IDS)
assert all(kind == "photo" for kind, _ in review_notifications)
assert all(
    "مؤشر تعرٍّ يحتاج مراجعة" in kwargs["caption"]
    for _, kwargs in review_notifications
)
print("review-required reports notify admins with the photo and moderation note")

rows, total = asyncio.run(DB.list_reports(None, 0, 5))
assert total == 2 and rows[0]["seq"] == r2["seq"], (total, rows)
print("list_reports OK, total =", total)

ok = asyncio.run(DB.update_status(r2["id"], "قيد المراجعة", admin_id=111111111))
assert ok, "update_status failed"
r2b = asyncio.run(DB.get_by_id(r2["id"]))
assert r2b["status"] == "قيد المراجعة" and r2b["reviewed_by"] == 111111111
print("update_status OK ->", r2b["status"])

workflow_due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(
    timespec="seconds"
)
assert asyncio.run(
    DB.update_report_workflow(r1["id"], "urgent", 111111111, workflow_due)
)
workflow_report = asyncio.run(DB.get_by_id(r1["id"]))
assert workflow_report["priority"] == "urgent"
assert workflow_report["assigned_to"] == 111111111
assert workflow_report["due_at"] == workflow_due
assert asyncio.run(DB.workflow_counts()) == {
    "urgent": 1, "overdue": 1, "unassigned": 1,
}
asyncio.run(DB.set_runtime_settings({"MAX_DESC_LEN": 1800}))
assert asyncio.run(DB.get_runtime_settings()) == {"MAX_DESC_LEN": 1800}
print("report workflow and persisted runtime settings OK")

legacy_tmp = tempfile.TemporaryDirectory(
    prefix="mrw-legacy-selftest-", ignore_cleanup_errors=True
)
legacy_path = Path(legacy_tmp.name) / "legacy.db"
with sqlite3.connect(legacy_path) as legacy_conn:
    legacy_conn.execute(
        "CREATE TABLE reports (id INTEGER PRIMARY KEY, seq TEXT, user_id INTEGER, "
        "username TEXT, full_name TEXT, photo_path TEXT NOT NULL, photo_file_id TEXT, "
        "post_link TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL, "
        "created_at TEXT NOT NULL, reviewed_by INTEGER, reviewed_at TEXT, admin_note TEXT, "
        "violation_type TEXT, place TEXT, latitude REAL, longitude REAL, source TEXT, "
        "platform TEXT, needs_review INTEGER, published INTEGER)"
    )
    legacy_conn.execute(
        "INSERT INTO reports (id, seq, user_id, photo_path, post_link, description, "
        "status, created_at) VALUES (1, 'LEGACY-1', 123, '', '', 'قديم', 'جديد', ?)",
        (utils.utc_now_iso(),),
    )
legacy_db = database.Database(legacy_path)
legacy_report = asyncio.run(legacy_db.get_by_id(1))
assert legacy_report["seq"] == "LEGACY-1"
assert legacy_report["priority"] == "normal"
assert legacy_report["assigned_to"] is None
legacy_db.close()
legacy_tmp.cleanup()
print("legacy database migration preserves existing reports")

counts = asyncio.run(DB.count_by_status())
tot = asyncio.run(DB.total())
assert tot == 2 and counts.get("جديد") == 1 and counts.get("قيد المراجعة") == 1, (counts, tot)
print("stats OK:", counts, "total =", tot)

assert asyncio.run(DB.total_users()) == 1
asyncio.run(DB.log_event(999, "start"))
assert asyncio.run(DB.total_users()) == 1
asyncio.run(DB.log_event(1000, "start"))
assert asyncio.run(DB.total_users()) == 2
stats_replies = []

async def _capture_stats(text, **kwargs):
    stats_replies.append(text)

stats_update = SimpleNamespace(
    effective_user=SimpleNamespace(id=111111111),
    effective_chat=SimpleNamespace(type="private"),
    effective_message=SimpleNamespace(reply_text=_capture_stats),
)
asyncio.run(
    admin.cmd_stats(stats_update, SimpleNamespace(bot_data={"db": DB}))
)
assert len(stats_replies) == 1 and "المستخدمون الفريدون: <b>2</b>" in stats_replies[0]
stats_replies.clear()
non_admin_update = SimpleNamespace(
    effective_user=SimpleNamespace(id=222222222),
    effective_chat=SimpleNamespace(type="private"),
    effective_message=SimpleNamespace(reply_text=_capture_stats),
)
asyncio.run(
    admin.cmd_stats(non_admin_update, SimpleNamespace(bot_data={"db": DB}))
)
assert len(stats_replies) == 1 and "وصول مرفوض" in stats_replies[0]
stats_replies.clear()
group_admin_update = SimpleNamespace(
    effective_user=SimpleNamespace(id=111111111),
    effective_chat=SimpleNamespace(type="group"),
    effective_message=SimpleNamespace(reply_text=_capture_stats),
)
asyncio.run(
    admin.cmd_stats(group_admin_update, SimpleNamespace(bot_data={"db": DB}))
)
assert len(stats_replies) == 1 and "المحادثة الخاصة" in stats_replies[0]
print("/stats counts unique report and start users, private admin only")

n24 = asyncio.run(
    DB.count_since((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())
)
day = asyncio.run(DB.count_since(utils.local_day_start_iso(), user_id=999))
assert n24 == 2 and day == 2, (n24, day)
print("count_since OK (24h =", n24, "| today per-user =", day, ")")

import space_app

_space_app = space_app.build_app()
_vtypes_route = next(
    route for route in _space_app.routes
    if getattr(route, "path", None) == "/api/public/vtypes"
)
_prior_bot_app = main_mod.app
try:
    main_mod.app = SimpleNamespace(bot_data={"db": DB})
    _vtypes_response = asyncio.run(_vtypes_route.endpoint())
    _vtypes_payload = json.loads(_vtypes_response.body)
    assert _vtypes_response.status_code == 200
    assert _vtypes_payload["items"] == asyncio.run(DB.effective_vtypes())
    assert _vtypes_response.headers["cache-control"] == "no-store"
finally:
    main_mod.app = _prior_bot_app
print("Mini App public categories endpoint uses live database values")

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

_os.environ["ADMIN_API_TOKENS_JSON"] = json.dumps([
    {"token": "reviewer-test-token", "role": "reviewer", "admin_id": 111111111},
])

class _RoleRequest:
    headers = {"x-admin-token": "reviewer-test-token"}
    query_params = {}
    method = "POST"
    state = SimpleNamespace()

    def __init__(self, path):
        self.url = SimpleNamespace(path=path)

review_req = _RoleRequest("/api/admin/reports/1/status")
assert admin_api._auth(review_req) is None
assert review_req.state.admin_role == "reviewer"
assert review_req.state.admin_id == 111111111
denied_req = _RoleRequest("/api/admin/settings")
denied = admin_api._auth(denied_req)
assert denied is not None and denied.status_code == 403
_os.environ.pop("ADMIN_API_TOKENS_JSON")
print("admin_api: server-side role authorization OK")

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
workflow_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/reports/{rid}/workflow"
)
settings_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/settings"
    and "PUT" in getattr(route, "methods", set())
)
auto_freeze_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/system/auto-freeze"
)
user_limits_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/system/user-limits"
)
followup_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/reports/{rid}/followup"
)
message_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/users/{uid}/message"
)
photo_endpoint = next(
    route.endpoint for route in api_app.routes
    if getattr(route, "path", None) == "/api/admin/photo/{rid}"
)
original_bot_app = _bot_main.app
_os.environ["ADMIN_API_TOKEN"] = "test-token-xyz"

photo_db_tmp = tempfile.TemporaryDirectory(prefix="mrw-photo-selftest-")
photo_db = database.Database(Path(photo_db_tmp.name) / "photo.db")
_bot_main.app = SimpleNamespace(bot_data={"db": photo_db}, bot=None)
photo_path = Path(photo_db_tmp.name) / "legacy-image.png"
photo_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-payload")
legacy_image = asyncio.run(photo_db.add_report(
    user_id=778, username="legacy_image", full_name="صورة قديمة",
    photo_path=str(photo_path), photo_file_id=None,
    post_link="https://t.me/x/image", description="بلاغ صورة محفوظة محليًا",
))
assert admin_api._safe_report(legacy_image)["has_photo"] is True

class _PhotoRequest:
    headers = {}
    query_params = {"token": "test-token-xyz"}
    method = "GET"
    state = SimpleNamespace()

    def __init__(self, path):
        self.url = SimpleNamespace(path=path)

local_photo_response = asyncio.run(photo_endpoint(
    legacy_image["id"], _PhotoRequest("/api/admin/photo/1")
))
assert local_photo_response.status_code == 200
assert local_photo_response.headers["content-type"] == "image/png"
assert local_photo_response.headers["cache-control"] == "private, no-store"
print("admin_api: local PNG served with correct MIME; legacy reports expose image availability")

remote_image = asyncio.run(photo_db.add_report(
    user_id=779, username="remote_image", full_name="صورة تلغرام",
    photo_path="", photo_file_id="test-file-id",
    post_link="https://t.me/x/remote", description="بلاغ صورة محفوظة في تلغرام",
))
_original_photo_bytes = admin_api._photo_bytes

async def _fake_photo_bytes(_file_id):
    return b"\x89PNG\r\n\x1a\nfake-png-payload"

admin_api._photo_bytes = _fake_photo_bytes
try:
    remote_photo_response = asyncio.run(photo_endpoint(
        remote_image["id"], _PhotoRequest("/api/admin/photo/2")
    ))
    assert remote_photo_response.status_code == 200
    assert remote_photo_response.headers["content-type"] == "image/png"
finally:
    admin_api._photo_bytes = _original_photo_bytes
    photo_db.close()
    photo_db_tmp.cleanup()
    _bot_main.app = SimpleNamespace(bot_data={"db": pdb}, bot=None)
print("admin_api: Telegram PNG is served with signature-detected MIME")

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

due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
workflow_response = asyncio.run(workflow_endpoint(
    reject_report["id"],
    _Request({
        "priority": "high",
        "assigned_to": 111111111,
        "due_at": due_at,
    }),
))
assert workflow_response.status_code == 200
workflow_saved = asyncio.run(pdb.get_by_id(reject_report["id"]))
assert workflow_saved["priority"] == "high"
assert workflow_saved["assigned_to"] == 111111111

old_runtime_settings = {
    name: getattr(config, name) for name in config.RUNTIME_SETTING_LIMITS
}
settings_response = asyncio.run(settings_endpoint(
    _Request({
        "max_description_length": 1800,
        "max_reports_per_hour": 7,
        "max_reports_per_hour_global": 120,
        "max_reports_per_day_global": 600,
        "freeze_after_strikes": 2,
        "freeze_minutes_first": 15,
        "freeze_minutes_second": 120,
        "freeze_minutes_repeat": 720,
        "block_messages": {
            "frozen": "اختبار حظر: {minutes} دقيقة",
            "manual_block": "تم تقييد الحساب: {kind} — {reason}",
        },
    })
))
assert settings_response.status_code == 200
assert config.MAX_DESC_LEN == 1800
assert config.MAX_REPORTS_PER_HOUR == 7
assert config.MAX_REPORTS_PER_HOUR_GLOBAL == 120
assert config.MAX_REPORTS_PER_DAY_GLOBAL == 600
assert config.FREEZE_AFTER_STRIKES == 2
assert config.FREEZE_MINUTES_FIRST == 15
assert config.FREEZE_MINUTES_SECOND == 120
assert config.FREEZE_MINUTES_REPEAT == 720
saved_messages = json.loads(settings_response.body)["items"]["block_messages"]
assert saved_messages["frozen"] == "اختبار حظر: {minutes} دقيقة"
assert saved_messages["manual_block"] == "تم تقييد الحساب: {kind} — {reason}"
bad_message_response = asyncio.run(settings_endpoint(
    _Request({"block_messages": {"frozen": "تجاوز غير مدعوم {unknown}"}})
))
assert bad_message_response.status_code == 400
bad_order_response = asyncio.run(settings_endpoint(
    _Request({
        "freeze_minutes_first": 500,
        "freeze_minutes_second": 200,
        "freeze_minutes_repeat": 700,
    })
))
assert bad_order_response.status_code == 400
config.apply_runtime_settings(old_runtime_settings)
rendered_message = asyncio.run(
    handlers.guards.render_block_message(pdb, "frozen", {"minutes": "<12>"})
)
assert "&lt;12&gt;" in rendered_message and "<12>" not in rendered_message

invalid_pause = asyncio.run(auto_freeze_endpoint(
    _Request({"on": True, "duration_minutes": 4, "reason": "اختبار"})
))
assert invalid_pause.status_code == 400
assert asyncio.run(pdb.get_state("auto_freeze_paused_until")) is None
pause_response = asyncio.run(auto_freeze_endpoint(
    _Request({"on": True, "duration_minutes": 30, "reason": "اختبار لوحة"})
))
assert pause_response.status_code == 200
assert json.loads(pause_response.body)["paused"] is True
resume_response = asyncio.run(auto_freeze_endpoint(
    _Request({"on": False, "reason": "انتهاء الاختبار"})
))
assert resume_response.status_code == 200
assert asyncio.run(pdb.get_state("auto_freeze_paused_until")) is None

limits_pause_response = asyncio.run(user_limits_endpoint(
    _Request({"on": True, "duration_minutes": 60, "reason": "اختبار لوحة"})
))
assert limits_pause_response.status_code == 200
assert asyncio.run(pdb.get_state("user_limits_paused_until"))
limits_resume_response = asyncio.run(user_limits_endpoint(
    _Request({"on": False, "reason": "انتهاء الاختبار"})
))
assert limits_resume_response.status_code == 200
assert asyncio.run(pdb.get_state("user_limits_paused_until")) is None

send_calls = []

async def _fake_send_direct_message(user_id, text):
    send_calls.append((user_id, text))
    return True

original_sender = admin_api._send_direct_message
admin_api._send_direct_message = _fake_send_direct_message
followup_response = asyncio.run(followup_endpoint(
    reject_report["id"], _Request({"note": "أرسل تفاصيل إضافية"})
))
assert followup_response.status_code == 200
followup_saved = asyncio.run(pdb.get_by_id(reject_report["id"]))
assert followup_saved["followup_requested_at"]
assert len(send_calls) == 1 and "/reply" in send_calls[0][1]
message_response = asyncio.run(message_endpoint(
    777, _Request({"text": "رسالة اختبار"})
))
assert message_response.status_code == 200
assert json.loads(message_response.body)["sent"] is True
assert send_calls[0][0] == 909 and "/reply" in send_calls[0][1]
assert send_calls[1] == (777, "رسالة اختبار")
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
