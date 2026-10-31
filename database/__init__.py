"""
طبقة قاعدة البيانات — SQLite.

- جدول واحد رئيسي: reports (البلاغات).
- كل العمليات الحساسة مغلقة بـ Lock لضمان عدم تعارض الكتابة.
- الاستدعاءات من البوت غير متزامنة (async) عبر asyncio.to_thread
  حتى لا يتجمد البوت أثناء الكتابة/القراءة.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# حالات البلاغ (تُخزّن بالعربية في القاعدة لسهولة قراءتها في التقارير)
# ------------------------------------------------------------------
STATUS_NEW = "جديد"
STATUS_REVIEW = "قيد المراجعة"
STATUS_APPROVED = "معتمد"
STATUS_REJECTED = "مرفوض"
STATUS_AWAITING_INFO = "بانتظار معلومات إضافية من المبلّغ"
STATUS_CLOSED_NO_RESPONSE = "أُغلق لعدم الرد"

STATUS_CODES: dict[str, str] = {          # كود الأزرار -> اسم الحالة في القاعدة
    "new": STATUS_NEW,
    "review": STATUS_REVIEW,
    "approved": STATUS_APPROVED,
    "rejected": STATUS_REJECTED,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    seq           TEXT UNIQUE,                       -- الرقم التسلسلي المعروض MRV-YYYY-NNNN
    user_id       INTEGER NOT NULL,                  -- الرقم التعريفي Telegram للمبلّغ
    username      TEXT,                              -- معرف المستخدم (@username)
    full_name     TEXT,                              -- الاسم الكامل
    photo_path    TEXT NOT NULL,                     -- مسار الصورة المحفوظة على الجهاز
    photo_file_id TEXT,                              -- file_id من تلغرام (احتياطي لإعادة التحميل)
    post_link     TEXT NOT NULL,                     -- رابط المنشور الأصلي
    description   TEXT NOT NULL,                     -- وصف المخالفة
    status        TEXT NOT NULL DEFAULT 'جديد',      -- حالة البلاغ
    created_at    TEXT NOT NULL,                     -- وقت الإرسال (UTC, ISO)
    reviewed_by   INTEGER,                           -- رقم ID للمشرف الذي راجع
    reviewed_at   TEXT,                              -- وقت المراجعة
    admin_note    TEXT,                              -- ملاحظات المشرف
    violation_type TEXT,                             -- نوع المخالفة (من التطبيق)
    place        TEXT,                              -- وصف الموقع
    latitude     REAL,                              -- خط العرض (من التطبيق)
    longitude    REAL,                              -- خط الطول (من التطبيق)
    source       TEXT NOT NULL DEFAULT 'chat',      -- مصدر البلاغ: chat | app
    platform     TEXT,                              -- منصة التواصل التي نُشر عليها
    category     TEXT,                              -- المسار الرئيسي للبلاغ
    contact_phone TEXT,                             -- رقم تواصل اختياري بموافقة المبلّغ
    needs_review INTEGER NOT NULL DEFAULT 0,        -- يحتاج مراجعة (تكرار/شك)
    published    INTEGER NOT NULL DEFAULT 0,        -- هل نُشر في القناة الرسمية
    priority     TEXT NOT NULL DEFAULT 'normal',
    assigned_to  INTEGER,
    due_at       TEXT,
    followup_requested_at TEXT,
    followup_due_at TEXT,
    followup_note TEXT,
    followup_response TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id);
CREATE TABLE IF NOT EXISTS report_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_review'
);
CREATE INDEX IF NOT EXISTS idx_report_appeals_report
    ON report_appeals(report_id, submitted_at);

-- ==================================================================
-- جداول طبقة الحماية (إساءة / إغراق / كيد / تخريب)
-- ==================================================================

-- سجل كل محاولة بلاغ (لكل مستخدم) — يخدم تحديد المعدلات وكشف الإغراق
CREATE TABLE IF NOT EXISTS user_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,               -- صاحب المحاولة
    ts      TEXT NOT NULL,                  -- وقت المحاولة (UTC ISO)
    kind    TEXT NOT NULL DEFAULT 'report'  -- نوع الحدث (report / attempt / admin)
);
CREATE INDEX IF NOT EXISTS idx_events_user_ts ON user_events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON user_events(ts);

-- حالة المبلّغ: عدد المخالفات + مدة التجميد بعد تجاوز الحدود
CREATE TABLE IF NOT EXISTS user_blocks (
    user_id     INTEGER PRIMARY KEY,
    strikes     INTEGER NOT NULL DEFAULT 0, -- عدد المخالفات التراكمية
    frozen_until TEXT,                      -- نهاية التجميد (UTC ISO) أو NULL
    reason      TEXT,                       -- سبب آخر تجميد
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    full_name     TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_users_last_seen ON bot_users(last_seen_at);

CREATE TABLE IF NOT EXISTS verified_reporters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('capturing', 'pending', 'verified', 'rejected')),
    review_state TEXT NOT NULL DEFAULT 'pending',
    submitted_at TEXT,
    decided_at TEXT,
    reviewed_by INTEGER,
    consented_at TEXT,
    consent_version TEXT,
    rejection_reason TEXT,
    supplement_requested_at TEXT,
    supplement_message TEXT,
    supplement_requested_documents TEXT,
    documents_deleted_at TEXT,
    documents_deleted_by INTEGER,
    documents_deleted_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_verified_reporters_status
    ON verified_reporters(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_verified_reporters_user
    ON verified_reporters(user_id, id);

CREATE TABLE IF NOT EXISTS verification_tickets (
    user_id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL,
    ticket_hash TEXT NOT NULL UNIQUE,
    phone_ciphertext BLOB NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verified_reporter_private (
    request_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    phone_ciphertext BLOB NOT NULL,
    selfie_ciphertext BLOB NOT NULL,
    id_front_ciphertext BLOB NOT NULL,
    id_back_ciphertext BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS verified_reporter_document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('selfie', 'id_front', 'id_back')),
    ciphertext BLOB NOT NULL,
    submitted_at TEXT NOT NULL,
    submitted_by_user_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verification_document_versions
    ON verified_reporter_document_versions(request_id, kind, id);

CREATE TABLE IF NOT EXISTS verification_supplement_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    requested_documents TEXT NOT NULL,
    message TEXT NOT NULL,
    requested_by INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    response_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_verification_supplement_requests
    ON verification_supplement_requests(request_id, response_at, id);

CREATE TABLE IF NOT EXISTS verification_supplement_uploads (
    request_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('selfie', 'id_front', 'id_back')),
    ciphertext BLOB NOT NULL,
    submitted_at TEXT NOT NULL,
    PRIMARY KEY(request_id, kind)
);

CREATE TABLE IF NOT EXISTS user_limit_resets (
    user_id  INTEGER PRIMARY KEY,
    reset_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_conversations (
    user_id              INTEGER PRIMARY KEY,
    opened_at            TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_read_message_id INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('in', 'out')),
    text            TEXT NOT NULL,
    admin_id        INTEGER,
    created_at      TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'delivered',
    error_code      TEXT,
    media_type      TEXT,
    telegram_file_id TEXT,
    telegram_message_id INTEGER,
    filename        TEXT,
    is_edited       INTEGER NOT NULL DEFAULT 0,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_user_messages_user_id ON user_messages(user_id, id);

CREATE TABLE IF NOT EXISTS broadcast_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    text         TEXT NOT NULL,
    actor_id     INTEGER NOT NULL,
    status       TEXT NOT NULL,
    total        INTEGER NOT NULL,
    sent         INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT,
    media_type   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS broadcast_deliveries (
    job_id       INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    sent_at      TEXT NOT NULL,
    delete_status TEXT NOT NULL DEFAULT 'available',
    delete_error TEXT,
    deleted_at   TEXT,
    edit_status  TEXT NOT NULL DEFAULT 'available',
    edit_error   TEXT,
    PRIMARY KEY (job_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_job_status
    ON broadcast_deliveries(job_id, delete_status);

CREATE TABLE IF NOT EXISTS broadcast_delete_jobs (
    job_id       INTEGER PRIMARY KEY,
    actor_id     INTEGER NOT NULL,
    status       TEXT NOT NULL,
    total        INTEGER NOT NULL,
    deleted      INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    expired      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS broadcast_edit_jobs (
    job_id       INTEGER PRIMARY KEY,
    actor_id     INTEGER NOT NULL,
    status       TEXT NOT NULL,
    content      TEXT NOT NULL,
    total        INTEGER NOT NULL,
    edited       INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    expired      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

-- سجل تدقيق: كل عملية حساسة (تغيير حالة، تجميد، إيقاف عام، تصدير…)
CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,                -- وقت العملية (UTC ISO)
    actor_id INTEGER,                      -- من قام بالعملية (رقم تيليغرام)
    action   TEXT NOT NULL,                -- اسم العملية
    target   TEXT,                         -- الهدف (رقم بلاغ / معرّف مستخدم)
    details  TEXT                          -- تفاصيل إضافية
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

-- حالة النظام العامة (متغير مفتاح/قيمة): example: emergency = 1
CREATE TABLE IF NOT EXISTS system_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS welcome_photo (
    id           INTEGER PRIMARY KEY CHECK(id = 1),
    data         BLOB NOT NULL,
    content_type TEXT NOT NULL,
    filename     TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    target_admin_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_notifications_read_created
    ON notifications(is_read, id);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at
    ON notifications(created_at);

-- عدّاد ضغطات أزرار البوت (تحليلات لوحة التحكم)
CREATE TABLE IF NOT EXISTS bot_button_stats (
    action     TEXT PRIMARY KEY,
    presses    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_contact_settings (
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocked_user_evidence (
    user_id INTEGER PRIMARY KEY,
    photo_path TEXT,
    photo_file_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS osint_sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id    INTEGER,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    source_level TEXT NOT NULL,
    accessed_at  TEXT NOT NULL,
    admin_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_osint_sources_report
    ON osint_sources(report_id, accessed_at);

-- بصمات المحتوى لكشف التكرار (رابط مطبَّع + تشابه الوصف)
CREATE TABLE IF NOT EXISTS report_fingerprints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL,           -- رقم البلاغ المرتبط
    link_hash   TEXT,                      -- بصمة الرابط المطبَّع
    text_hash   TEXT,                      -- بصمة الوصف
    photo_hash  TEXT,                      -- بصمة الصورة (sha256 أو pHash)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fp_link ON report_fingerprints(link_hash);
CREATE INDEX IF NOT EXISTS idx_fp_text ON report_fingerprints(text_hash);
CREATE INDEX IF NOT EXISTS idx_fp_photo ON report_fingerprints(photo_hash);
"""

_IDENTITY_PRIVATE_TABLES = (
    "verification_tickets",
    "verified_reporter_private",
)


class _DatabaseRow(dict[str, Any]):
    def __init__(self, values: dict[str, Any], ordered_values: tuple[Any, ...]) -> None:
        super().__init__(values)
        self._ordered_values = ordered_values

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._ordered_values[key]
        return super().__getitem__(key)


def _mapping_row_factory(cursor: Any, row: Any) -> _DatabaseRow:
    columns = cursor.description or ()
    values = tuple(row)
    return _DatabaseRow(
        {column[0]: values[index] for index, column in enumerate(columns)},
        values,
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


class Database:
    """SQLite locally or a remote Turso/libSQL database in production."""

    def __init__(self, path: Path, backend: str | None = None) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._notification_subscribers: set[queue.Queue[dict[str, Any]]] = set()
        is_render = any(
            os.getenv(name, "").strip()
            for name in ("RENDER_SERVICE_ID", "RENDER_SERVICE_NAME", "RENDER_EXTERNAL_URL")
        )
        selected_backend = (
            (backend or "").strip().lower()
            or os.getenv("DB_BACKEND", "").strip().lower()
            or ("turso" if is_render else "sqlite")
        )
        if selected_backend not in {"sqlite", "turso"}:
            raise ValueError("DB_BACKEND must be either 'sqlite' or 'turso'")
        if is_render and selected_backend != "turso":
            raise RuntimeError("Render production requires DB_BACKEND=turso")
        self.backend = selected_backend

        if self.backend == "sqlite":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        else:
            database_url = os.getenv("TURSO_DATABASE_URL", "").strip()
            auth_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
            missing = [
                name for name, value in (
                    ("TURSO_DATABASE_URL", database_url),
                    ("TURSO_AUTH_TOKEN", auth_token),
                ) if not value
            ]
            if missing:
                raise RuntimeError(
                    "Turso backend requires environment variable(s): " + ", ".join(missing)
                )
            try:
                import turso_serverless
            except ImportError as exc:
                raise RuntimeError(
                    "Turso backend requires turso_serverless; install requirements.txt"
                ) from exc
            self._conn = turso_serverless.connect(
                database_url,
                auth_token=auth_token,
            )
        self._conn.row_factory = _mapping_row_factory
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._migrate()
            # Auto-seed bot_texts if empty (for Render/Turso)
            try:
                self._create_bot_texts_table()
                count = self._conn.execute("SELECT count(*) FROM bot_texts").fetchone()[0]
                if count == 0:
                    texts = [
                        ("welcome_message","رسالة الترحيب","🛡️ منظومة البلاغات والشكاوى\nإدارة الشرطة العسكرية\n────────────────────\nأهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته من هذه المحادثة.\nستراجع بياناتك قبل الإرسال، ثم يصلك رقم تتبع للمتابعة.\n\nمشاركة رقم الهاتف والموقع اختيارية. أرسل فقط المعلومات والأدلة ذات الصلة.\n\nاختر الخدمة التي تحتاجها:","user_facing"),
                        ("verify_consent","نص الموافقة للتوثيق","🪪 التسجيل كمراسل موثوق\n\nالغرض: توثيق حساب المراسل لدى الشرطة العسكرية فرع الحسكة. يتطلب الطلب مشاركة رقم سوري عبر زر تيليجرام، وصورة وجه، وصورتي الهوية الأمامية والخلفية.\n\nيُتحقق محليًا من وضوح الصور، ومن وجود وجه واحد واضح في صورة الوجه. تُرسل صورة الوجه فقط إلى Sightengine لفحص المحتوى غير اللائق؛ ولا تُرسل صور الهوية إلى هذه الخدمة. لا يثبت الفحص أصالة الهوية أو أن الصورة حيّة. إذا اعتُمد الطلب، يُحفظ الملف والصور دون حذف تلقائي، ويمكن تنزيلها ضمن ملف PDF. يقتصر الاطلاع والتنزيل على المشرفين الذين مُنحوا صلاحية الوثائق؛ وتُسجل عمليات الاطلاع والتنزيل. لا تُحذف الصور تلقائيًا بعد القرار. يمكن لمشرف مُنح صلاحية حذف مستقلة حذف الصور ونسخها بسبب مسجل، مع الإبقاء على سجل الطلب.\n\nبالضغط على «متابعة والموافقة» تؤكد اطلاعك وموافقتك على هذه الشروط.","user_facing"),
                        ("help_text","دليل الاستخدام","📖 الدليل السريع\n────────────────────\n📝 إرسال بلاغ: اضغط /start ثم اختر القسم المناسب، وأدخل التفاصيل وأرفق الدليل أو الموقع ورقم التواصل اختياريًا عند الحاجة.\n\n📊 متابعة بلاغاتك: استخدم زر «حالة بلاغي» لعرض بطاقة لكل بلاغ وحالته وزمنه.\n\n📩 معلومات إضافية: افتح «حالة بلاغي»، ثم بطاقة البلاغ التي طلبت الإدارة معلومات إضافية عنها واضغط زر الإرسال.\n\n🚫 الإلغاء: استخدم زر (❌ إلغاء) الظاهر في خطوات البلاغ.\n\n🔔 التحديثات: تُرسل إليك فورًا عند أي تغيير في حالة بلاغك.\n\n🔗 الأدلة: أرفق ما يتوفر من روابط أو صور ذات صلة، ويمكن تخطي المرفقات الاختيارية.\n\n🔒 السرية: كل بلاغ يُعرض على المشرف المختص فقط.\n\nللبدء الآن اضغط زر «📝 إرسال بلاغ جديد» أو أرسل /start.","user_facing"),
                        ("privacy_policy","سياسة الخصوصية","🔒 سياسة الخصوصية\nتُحفظ البيانات بشكل آمن داخل نظام مخصص، ولا تُشارك مع جهات خارجية إلا بموجب أمر قضائي. تُسجل جميع عمليات الوصول إلى الصور والوثائق. لا تُحفظ بيانات المستخدم غير الضرورية.","user_facing"),
                        ("verify_button","زر التوثيق","✅ توثيق","button_label"),
                        ("menu_button","زر القائمة","📋 القائمة","button_label"),
                        ("cancel_button","زر الإلغاء","❌ إلغاء","button_label"),
                        ("confirm_button","زر التأكيد","✅ إرسال البلاغ","button_label"),
                        ("contact_admin","تواصل مع الإدارة","📞 تواصل مع الإدارة","button_label"),
                        ("about_system","عن النظام","ℹ️ تعليمات واشتراطات تقديم البلاغ\nاختر القسم الأقرب للواقعة، واكتب وصفًا دقيقًا يتضمن ما حدث ومتى وأين والأشخاص أو الجهة ذات الصلة.\n📎 أرفق صورة ذات صلة إن توفرت. في بلاغات النشر اختر المنصة وأرسل الرابط أو تخطّه إذا كان الدليل لقطة شاشة.\n☎️ مشاركة رقم الهاتف اختيارية. لا ترسل بيانات أو أدلة لا تتعلق بالبلاغ.\n🔒 راجع البيانات قبل الإرسال؛ سيظهر لك رقم تتبع ويمكنك متابعة الحالة من زر «حالة بلاغي».","user_facing"),
                    ]
                    for t in texts:
                        self._conn.execute("INSERT OR IGNORE INTO bot_texts (key, label, content, category) VALUES (?, ?, ?, ?)", t)
                    self._conn.commit()
            except Exception:
                pass

    def health_check(self) -> bool:
        """Run a minimal query against the configured database backend."""
        with self._lock:
            row = self._conn.execute("SELECT 1 AS ok").fetchone()
            return bool(row and row["ok"] == 1)

    def export_sqlite_snapshot(self, output_path: Path) -> None:
        """Write a SQLite snapshot of the configured database to a local file."""
        if self.backend == "sqlite":
            destination = sqlite3.connect(str(output_path))
            try:
                with self._lock:
                    self._conn.backup(destination)
                for table in _IDENTITY_PRIVATE_TABLES:
                    destination.execute(f'DROP TABLE IF EXISTS "{table}"')
                destination.commit()
            finally:
                destination.close()
            return

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(str(output_path))
        try:
            destination.executescript(_SCHEMA)
            destination.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_workflow "
                "ON reports(status, priority, due_at, assigned_to)"
            )
            with self._lock:
                tables = [
                    row[0]
                    for row in destination.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                ]
                for table in tables:
                    if table in _IDENTITY_PRIVATE_TABLES:
                        continue
                    rows = self._conn.execute(f'SELECT * FROM "{table}"').fetchall()
                    if not rows:
                        continue
                    columns = list(rows[0].keys())
                    quoted_columns = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    destination.executemany(
                        f'INSERT INTO "{table}" ({quoted_columns}) '
                        f"VALUES ({placeholders})",
                        [tuple(row[column] for column in columns) for row in rows],
                    )
                sequences = self._conn.execute(
                    "SELECT name, seq FROM sqlite_sequence"
                ).fetchall()
                for sequence in sequences:
                    updated = destination.execute(
                        "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                        (sequence["seq"], sequence["name"]),
                    )
                    if updated.rowcount == 0:
                        destination.execute(
                            "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                            (sequence["name"], sequence["seq"]),
                        )
                destination.commit()
        except Exception:
            destination.close()
            output_path.unlink(missing_ok=True)
            raise
        else:
            destination.close()

    # --------------------------------------------------------------
    # الترحيل: إضافة الأعمدة الجديدة إن لم تكن موجودة
    # (مهم لأن القواعد المنشأة سابقًا لا تتغيّر تلقائيًا)
    # --------------------------------------------------------------
    _MIGRATIONS: tuple[tuple[str, str], ...] = (
        ("violation_type", "TEXT"),
        ("place", "TEXT"),
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("source", "TEXT NOT NULL DEFAULT 'chat'"),
        ("platform", "TEXT"),
        ("category", "TEXT"),
        ("contact_phone", "TEXT"),
        ("needs_review", "INTEGER NOT NULL DEFAULT 0"),
        ("published", "INTEGER NOT NULL DEFAULT 0"),
        ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
        ("assigned_to", "INTEGER"),
        ("due_at", "TEXT"),
        ("followup_requested_at", "TEXT"),
        ("followup_due_at", "TEXT"),
        ("followup_note", "TEXT"),
        ("followup_response", "TEXT"),
    )

    def _migrate(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(reports)").fetchall()
        }
        for column, ddl in self._MIGRATIONS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE reports ADD COLUMN {column} {ddl}")
                print(f"🗄️ تمت إضافة العمود الجديد: {column}")
        message_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(user_messages)"
            ).fetchall()
        }
        for column, ddl in (
            ("delivery_status", "TEXT NOT NULL DEFAULT 'delivered'"),
            ("error_code", "TEXT"),
            ("media_type", "TEXT"),
            ("telegram_file_id", "TEXT"),
            ("telegram_message_id", "INTEGER"),
            ("filename", "TEXT"),
            ("is_edited", "INTEGER NOT NULL DEFAULT 0"),
            ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in message_columns:
                self._conn.execute(
                    f"ALTER TABLE user_messages ADD COLUMN {column} {ddl}"
                )
        verification_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(verified_reporters)"
            ).fetchall()
        }
        for column in ("consented_at", "consent_version", "rejection_reason"):
            if column not in verification_columns:
                self._conn.execute(
                    f"ALTER TABLE verified_reporters ADD COLUMN {column} TEXT"
                )
        for column, ddl in {
            "review_state": "TEXT NOT NULL DEFAULT 'pending'",
            "supplement_requested_at": "TEXT",
            "supplement_message": "TEXT",
            "supplement_requested_documents": "TEXT",
            "documents_deleted_at": "TEXT",
            "documents_deleted_by": "INTEGER",
            "documents_deleted_reason": "TEXT",
        }.items():
            if column not in verification_columns:
                self._conn.execute(
                    f"ALTER TABLE verified_reporters ADD COLUMN {column} {ddl}"
                )
        for kind, column in (
            ("selfie", "selfie_ciphertext"),
            ("id_front", "id_front_ciphertext"),
            ("id_back", "id_back_ciphertext"),
        ):
            self._conn.execute(
                f"""
                INSERT INTO verified_reporter_document_versions
                    (request_id, kind, ciphertext, submitted_at, submitted_by_user_id)
                SELECT p.request_id, ?, p.{column},
                       COALESCE(r.submitted_at, r.decided_at, ?), p.user_id
                FROM verified_reporter_private p
                JOIN verified_reporters r ON r.id = p.request_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM verified_reporter_document_versions v
                    WHERE v.request_id = p.request_id AND v.kind = ?
                )
                """,
                (kind, datetime.now(timezone.utc).isoformat(timespec="seconds"), kind),
            )
        broadcast_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(broadcast_jobs)"
            ).fetchall()
        }
        if "media_type" not in broadcast_columns:
            self._conn.execute(
                "ALTER TABLE broadcast_jobs ADD COLUMN "
                "media_type TEXT NOT NULL DEFAULT ''"
            )
        delivery_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(broadcast_deliveries)"
            ).fetchall()
        }
        for column, ddl in (
            ("edit_status", "TEXT NOT NULL DEFAULT 'available'"),
            ("edit_error", "TEXT"),
        ):
            if column not in delivery_columns:
                self._conn.execute(
                    f"ALTER TABLE broadcast_deliveries ADD COLUMN {column} {ddl}"
                )
        conversation_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(user_conversations)"
            ).fetchall()
        }
        if "last_read_message_id" not in conversation_columns:
            self._conn.execute(
                "ALTER TABLE user_conversations ADD COLUMN "
                "last_read_message_id INTEGER NOT NULL DEFAULT 0"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_messages_status "
            "ON user_messages(delivery_status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_workflow "
            "ON reports(status, priority, due_at, assigned_to)"
        )
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.execute(
            """
            INSERT OR IGNORE INTO bot_users
                (user_id, username, full_name, first_seen_at, last_seen_at)
            SELECT user_id, MAX(username), MAX(full_name), MIN(created_at), MAX(created_at)
            FROM reports GROUP BY user_id
            """
        )
        self._conn.execute(
            """
            INSERT OR IGNORE INTO bot_users
                (user_id, first_seen_at, last_seen_at)
            SELECT user_id, MIN(ts), MAX(ts)
            FROM user_events WHERE kind = 'start' GROUP BY user_id
            """
        )
        self._conn.execute(
            "UPDATE broadcast_jobs SET status = 'interrupted', completed_at = ? "
            "WHERE status IN ('queued', 'running', 'cancel_requested')",
            (now_iso,),
        )
        self._conn.commit()

    # --------------------------------------------------------------
    # إضافة بلاغ جديد
    # --------------------------------------------------------------
    def _add_report(
        self,
        user_id: int,
        username: str | None,
        full_name: str | None,
        photo_path: str,
        photo_file_id: str | None,
        post_link: str,
        description: str,
        violation_type: str | None = None,
        place: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        source: str = "chat",
        platform: str | None = None,
        category: str | None = None,
        contact_phone: str | None = None,
        needs_review: int = 0,
        admin_note: str | None = None,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bot_users
                    (user_id, username, full_name, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (user_id, username, full_name, now_iso, now_iso),
            )
            cur = self._conn.execute(
                """
                INSERT INTO reports
                    (user_id, username, full_name, photo_path, photo_file_id,
                     post_link, description, status, created_at,
                     violation_type, place, latitude, longitude, source, platform,
                     category, contact_phone, needs_review, admin_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, full_name, photo_path, photo_file_id,
                 post_link, description, STATUS_NEW, now_iso,
                 violation_type, place, latitude, longitude, source, platform,
                 category, contact_phone, needs_review, admin_note),
            )
            rid = cur.lastrowid
            # توليد الرقم التسلسلي الفريد MRV-YYYY-NNNN اعتمادًا على id الصف
            seq = f"MRV-{datetime.now(timezone.utc).year}-{rid:04d}"
            self._conn.execute("UPDATE reports SET seq = ? WHERE id = ?", (seq, rid))
            report = self._get_by_id(rid) or {}
            notification = self._insert_notification_locked(
                "report_new",
                {
                    "report_id": rid,
                    "seq": seq,
                    "priority": report.get("priority", "normal"),
                    "user_id": user_id,
                    "full_name": full_name or "",
                },
            )
            self._conn.commit()
        self._publish_notification(notification)
        return report

    async def add_report(self, **kwargs) -> dict[str, Any]:
        return await asyncio.to_thread(self._add_report, **kwargs)

    # --------------------------------------------------------------
    # قراءة البيانات
    # --------------------------------------------------------------
    def _get_by_id(self, rid: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT reports.*,
                       (SELECT last_seen_at FROM bot_users
                        WHERE bot_users.user_id = reports.user_id) AS user_last_at
                FROM reports
                WHERE reports.id = ?
                """,
                (rid,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def get_by_id(self, rid: int) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self._get_by_id, rid)

    def _get_by_seq(self, seq: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM reports WHERE seq = ?", (seq,)).fetchone()
            return _row_to_dict(row) if row else None

    async def get_by_seq(self, seq: str) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self._get_by_seq, seq)

    def _last_report_by_user(self, user_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def last_report_by_user(self, user_id: int) -> Optional[dict[str, Any]]:
        """آخر بلاغ لمبلّغ معيّن — لشاشة «حالة بلاغي»."""
        return await asyncio.to_thread(self._last_report_by_user, user_id)

    def _list_user_reports(
        self, user_id: int, limit: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            total = int(self._conn.execute(
                "SELECT COUNT(*) AS c FROM reports WHERE user_id = ?", (user_id,)
            ).fetchone()["c"])
            rows = self._conn.execute(
                "SELECT * FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [_row_to_dict(r) for r in rows], total

    async def list_user_reports(
        self, user_id: int, limit: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        """(بلاغات مبلّغ من الأحدث، إجمالي بلاغاته) — لشاشة «بلاغاتي»."""
        return await asyncio.to_thread(self._list_user_reports, user_id, limit)

    def _list_reports(self, status: str | None, page: int, size: int) -> tuple[list[dict], int]:
        with self._lock:
            where, params = "", []
            if status:
                where = "WHERE status = ?"
                params = [status]
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM reports {where}", params
            ).fetchone()["c"]
            rows = self._conn.execute(
                f"SELECT * FROM reports {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, size, max(page, 0) * size],
            ).fetchall()
            return [_row_to_dict(r) for r in rows], int(total)

    async def list_reports(self, status: str | None, page: int, size: int):
        return await asyncio.to_thread(self._list_reports, status, page, size)

    def _all_reports(self, status: str | None = None) -> list[dict]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM reports WHERE status = ? ORDER BY id ASC", (status,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM reports ORDER BY id ASC").fetchall()
            return [_row_to_dict(r) for r in rows]

    async def all_reports(self, status: str | None = None) -> list[dict]:
        return await asyncio.to_thread(self._all_reports, status)

    # --------------------------------------------------------------
    # إحصائيات
    # --------------------------------------------------------------
    def _count_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS c FROM reports GROUP BY status"
            ).fetchall()
            return {r["status"]: int(r["c"]) for r in rows}

    async def count_by_status(self) -> dict[str, int]:
        return await asyncio.to_thread(self._count_by_status)

    def _total(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"])

    async def total(self) -> int:
        return await asyncio.to_thread(self._total)

    def _total_users(self) -> int:
        """Count persisted subscribers and legacy report/event identities."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM (
                    SELECT user_id FROM bot_users
                    UNION
                    SELECT user_id FROM user_events WHERE kind IN ('start', 'report')
                    UNION
                    SELECT user_id FROM reports
                )
                """
            ).fetchone()
            return int(row["c"])

    async def total_users(self) -> int:
        return await asyncio.to_thread(self._total_users)

    def _count_since(self, iso: str, user_id: Optional[int] = None) -> int:
        with self._lock:
            if user_id is None:
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM reports WHERE created_at >= ?", (iso,)
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM reports
                    WHERE created_at >= ? AND user_id = ?
                      AND created_at >= COALESCE(
                          (SELECT reset_at FROM user_limit_resets WHERE user_id = ?), ''
                      )
                    """,
                    (iso, user_id, user_id),
                )
            return int(cur.fetchone()["c"])

    async def count_since(self, iso: str, user_id: Optional[int] = None) -> int:
        return await asyncio.to_thread(self._count_since, iso, user_id)

    # --------------------------------------------------------------
    # تحديث حالة بلاغ
    # --------------------------------------------------------------
    def _update_status(self, rid: int, new_status: str, admin_id: int) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE reports
                SET status = ?, reviewed_by = ?, reviewed_at = ?,
                    followup_requested_at = NULL, followup_due_at = NULL,
                    followup_note = NULL
                WHERE id = ?
                """,
                (new_status, admin_id, now_iso, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def update_status(self, rid: int, new_status: str, admin_id: int) -> bool:
        return await asyncio.to_thread(self._update_status, rid, new_status, admin_id)

    # ==================================================================
    # طبقة الحماية: أحداث، تجميد، تدقيق، حالة النظام، البصمات
    # ==================================================================
    def _log_event(self, user_id: int, kind: str = "report") -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_events (user_id, ts, kind) VALUES (?, ?, ?)",
                (user_id, now_iso, kind),
            )
            self._conn.commit()

    async def log_event(self, user_id: int, kind: str = "report") -> None:
        await asyncio.to_thread(self._log_event, user_id, kind)

    def _record_user(
        self, user_id: int, username: str | None, full_name: str | None
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bot_users
                    (user_id, username, full_name, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (user_id, username, full_name, now_iso, now_iso),
            )
            self._conn.commit()

    async def record_user(
        self, user_id: int, username: str | None, full_name: str | None
    ) -> None:
        await asyncio.to_thread(self._record_user, user_id, username, full_name)

    def _create_verification_ticket(
        self,
        user_id: int,
        ticket_hash: str,
        phone_ciphertext: bytes,
        expires_at: str,
        consented_at: str | None = None,
        consent_version: str | None = None,
    ) -> int | None:
        if not consented_at or not consent_version:
            raise ValueError("verification_consent_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            latest = self._conn.execute(
                "SELECT id, status FROM verified_reporters "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if latest:
                status = latest["status"]
                if status == "verified":
                    return None
                if status == "capturing":
                    ticket = self._conn.execute(
                        "SELECT expires_at FROM verification_tickets "
                        "WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    if ticket and ticket["expires_at"] > now:
                        return None
                    self._conn.execute(
                        "DELETE FROM verification_tickets WHERE user_id = ?",
                        (user_id,),
                    )
                    self._conn.execute(
                        "DELETE FROM verified_reporters "
                        "WHERE id = ? AND status = 'capturing'",
                        (latest["id"],),
                    )
                elif status == "pending":
                    documents = self._conn.execute(
                        "SELECT 1 FROM verified_reporter_private WHERE request_id = ?",
                        (latest["id"],),
                    ).fetchone()
                    if documents:
                        return None
            cursor = self._conn.execute(
                """
                INSERT INTO verified_reporters
                    (user_id, status, consented_at, consent_version)
                VALUES (?, 'capturing', ?, ?)
                """,
                (user_id, consented_at, consent_version),
            )
            request_id = int(cursor.lastrowid)
            self._conn.execute(
                """
                INSERT INTO verification_tickets
                    (user_id, request_id, ticket_hash, phone_ciphertext, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    ticket_hash = excluded.ticket_hash,
                    phone_ciphertext = excluded.phone_ciphertext,
                    expires_at = excluded.expires_at
                """,
                (user_id, request_id, ticket_hash, phone_ciphertext, expires_at),
            )
            self._conn.commit()
            return request_id

    async def create_verification_ticket(
        self,
        user_id: int,
        ticket_hash: str,
        phone_ciphertext: bytes,
        expires_at: str,
        consented_at: str | None = None,
        consent_version: str | None = None,
    ) -> int | None:
        return await asyncio.to_thread(
            self._create_verification_ticket,
            user_id,
            ticket_hash,
            phone_ciphertext,
            expires_at,
            consented_at,
            consent_version,
        )

    def _extend_verification_ticket(
        self, user_id: int, ticket_hash: str, expires_at: str
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE verification_tickets SET expires_at = ?
                WHERE user_id = ? AND ticket_hash = ? AND expires_at > ?
                """,
                (expires_at, user_id, ticket_hash, now),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    async def extend_verification_ticket(
        self, user_id: int, ticket_hash: str, expires_at: str
    ) -> bool:
        return await asyncio.to_thread(
            self._extend_verification_ticket,
            user_id,
            ticket_hash,
            expires_at,
        )

    def _cancel_verification_capture(
        self, user_id: int, ticket_hash: str | None = None
    ) -> bool:
        with self._lock:
            if ticket_hash is None:
                ticket = self._conn.execute(
                    "SELECT request_id FROM verification_tickets WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            else:
                ticket = self._conn.execute(
                    """
                    SELECT request_id FROM verification_tickets
                    WHERE user_id = ? AND ticket_hash = ?
                    """,
                    (user_id, ticket_hash),
                ).fetchone()
            if ticket:
                request_id = int(ticket["request_id"])
                self._conn.execute(
                    "DELETE FROM verification_tickets WHERE user_id = ?",
                    (user_id,),
                )
            elif ticket_hash is None:
                capturing = self._conn.execute(
                    """
                    SELECT id FROM verified_reporters
                    WHERE user_id = ? AND status = 'capturing'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
                if not capturing:
                    return False
                request_id = int(capturing["id"])
            else:
                return False
            cursor = self._conn.execute(
                """
                DELETE FROM verified_reporters
                WHERE id = ? AND user_id = ? AND status = 'capturing'
                """,
                (request_id, user_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    async def cancel_verification_capture(
        self, user_id: int, ticket_hash: str | None = None
    ) -> bool:
        return await asyncio.to_thread(
            self._cancel_verification_capture,
            user_id,
            ticket_hash,
        )

    def _submit_verification_documents(
        self,
        user_id: int,
        ticket_hash: str,
        captures: dict[str, bytes],
    ) -> int | None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._conn:
            ticket = self._conn.execute(
                """
                SELECT request_id, phone_ciphertext FROM verification_tickets
                WHERE user_id = ? AND ticket_hash = ? AND expires_at > ?
                """,
                (user_id, ticket_hash, now),
            ).fetchone()
            if not ticket:
                return None
            request_id = int(ticket["request_id"])
            columns = {
                "selfie": "selfie_ciphertext",
                "id_front": "id_front_ciphertext",
                "id_back": "id_back_ciphertext",
            }
            if set(captures) != set(columns):
                raise ValueError("verification_documents_incomplete")
            self._conn.execute(
                """
                INSERT INTO verified_reporter_private
                    (request_id, user_id, phone_ciphertext, selfie_ciphertext,
                     id_front_ciphertext, id_back_ciphertext)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    user_id,
                    ticket["phone_ciphertext"],
                    captures["selfie"],
                    captures["id_front"],
                    captures["id_back"],
                ),
            )
            for kind, ciphertext in captures.items():
                self._conn.execute(
                    """
                    INSERT INTO verified_reporter_document_versions
                        (request_id, kind, ciphertext, submitted_at,
                         submitted_by_user_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (request_id, kind, ciphertext, now, user_id),
                )
            self._conn.execute(
                "UPDATE verified_reporters SET status = 'pending', "
                "review_state = 'pending', submitted_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'capturing'",
                (now, request_id, user_id),
            )
            self._conn.execute(
                "DELETE FROM verification_tickets WHERE user_id = ?",
                (user_id,),
            )
            self._insert_audit_locked(
                user_id,
                "verified_reporter_submitted",
                str(request_id),
                f"documents={','.join(sorted(captures))}",
            )
            self._conn.commit()
            return request_id

    async def submit_verification_documents(
        self, user_id: int, ticket_hash: str, captures: dict[str, bytes]
    ) -> int | None:
        return await asyncio.to_thread(
            self._submit_verification_documents,
            user_id,
            ticket_hash,
            captures,
        )

    def _verification_status(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT r.id, r.user_id, r.status, r.review_state,
                       r.submitted_at, r.decided_at,
                       r.rejection_reason,
                       r.supplement_requested_at,
                       r.supplement_message,
                       r.supplement_requested_documents,
                       CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                WHERE r.user_id = ?
                ORDER BY r.id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def verification_status(self, user_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._verification_status, user_id)

    def _verification_request(self, request_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, user_id, status, review_state, submitted_at, decided_at,
                       reviewed_by, consented_at, consent_version,
                       rejection_reason, supplement_requested_at,
                       supplement_message, supplement_requested_documents,
                       documents_deleted_at, documents_deleted_by,
                       documents_deleted_reason
                FROM verified_reporters
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def verification_request(
        self, request_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._verification_request, request_id)

    def _update_verification_consent(
        self, user_id: int, consented_at: str, consent_version: str
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE verified_reporters
                SET consented_at = ?, consent_version = ?
                WHERE id = (
                    SELECT id FROM verified_reporters
                    WHERE user_id = ? AND status = 'verified'
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (consented_at, consent_version, user_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def update_verification_consent(
        self, user_id: int, consented_at: str, consent_version: str
    ) -> bool:
        return await asyncio.to_thread(
            self._update_verification_consent,
            user_id,
            consented_at,
            consent_version,
        )

    def _delete_verified_reporter(
        self, request_id: int, actor_id: int, reason: str
    ) -> dict[str, Any] | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id, user_id, status FROM verified_reporters WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not row:
                return None
            item = _row_to_dict(row)
            if item["status"] not in {"verified", "rejected"}:
                return None
            self._conn.execute(
                "DELETE FROM verified_reporter_private WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                "DELETE FROM verified_reporter_document_versions WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                "DELETE FROM verification_supplement_uploads WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                "DELETE FROM verification_tickets WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                """
                UPDATE verified_reporters
                SET documents_deleted_at = ?, documents_deleted_by = ?,
                    documents_deleted_reason = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    actor_id,
                    reason,
                    request_id,
                ),
            )
            self._insert_audit_locked(
                actor_id,
                "verified_reporter_documents_deleted",
                str(request_id),
                f"reason={reason}; user_id={item['user_id']}",
            )
            self._conn.commit()
            return item

    async def delete_verified_reporter(
        self, request_id: int, actor_id: int, reason: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._delete_verified_reporter,
            request_id,
            actor_id,
            reason,
        )

    def _list_verification_requests(
        self, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        if status not in {"pending", "needs_info", "verified", "rejected", "all"}:
            raise ValueError("invalid_verification_status")
        limit = max(1, min(int(limit), 200))
        if status == "all":
            clause = ""
            params: tuple[Any, ...] = (limit,)
        elif status == "needs_info":
            clause = "WHERE r.status = 'pending' AND r.review_state = ?"
            params = ("needs_info", limit)
        elif status == "pending":
            clause = "WHERE r.status = ? AND r.review_state = 'pending'"
            params = (status, limit)
        else:
            clause = "WHERE r.status = ?"
            params = (status, limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT r.id, r.user_id, r.status, r.submitted_at,
                       r.review_state,
                       r.decided_at, r.reviewed_by, r.consented_at,
                       r.consent_version, r.rejection_reason,
                       r.supplement_requested_at,
                       r.supplement_message,
                       r.supplement_requested_documents,
                       r.documents_deleted_at, r.documents_deleted_by,
                       r.documents_deleted_reason,
                       CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents,
                       u.full_name, u.username
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                LEFT JOIN bot_users u ON u.user_id = r.user_id
                {clause}
                ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END,
                         r.submitted_at DESC, r.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_verification_requests(
        self, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_verification_requests, status, limit
        )

    def _search_verification_requests(
        self,
        *,
        status: str,
        query: str | None,
        start_date: str | None,
        end_date: str | None,
        documents: str = "all",
        sort: str = "waiting",
        page: int = 0,
        size: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        if status not in {"pending", "needs_info", "verified", "rejected", "all"}:
            raise ValueError("invalid_verification_status")
        if documents not in {"all", "available", "missing"}:
            raise ValueError("invalid_verification_documents_filter")
        if sort not in {"waiting", "oldest", "newest"}:
            raise ValueError("invalid_verification_sort")
        page = max(0, int(page))
        size = max(1, min(int(size), 100))
        clauses: list[str] = []
        params: list[Any] = []
        if status == "pending":
            clauses.append("r.status = 'pending' AND r.review_state = 'pending'")
        elif status == "needs_info":
            clauses.append("r.status = 'pending' AND r.review_state = 'needs_info'")
        elif status != "all":
            clauses.append("r.status = ?")
            params.append(status)
        if query:
            clauses.append(
                "(u.full_name LIKE ? OR u.username LIKE ? "
                "OR CAST(r.user_id AS TEXT) LIKE ?)"
            )
            pattern = f"%{query.strip()}%"
            params.extend((pattern, pattern, pattern))
        if start_date:
            clauses.append("substr(r.submitted_at, 1, 10) >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("substr(r.submitted_at, 1, 10) <= ?")
            params.append(end_date)
        if documents == "available":
            clauses.append("p.request_id IS NOT NULL")
        elif documents == "missing":
            clauses.append("p.request_id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = page * size
        order_by = {
            "waiting": (
                "CASE "
                "WHEN r.status = 'pending' AND r.review_state = 'pending' THEN 0 "
                "WHEN r.status = 'pending' AND r.review_state = 'needs_info' THEN 1 "
                "ELSE 2 END, r.submitted_at ASC, r.id ASC"
            ),
            "oldest": "r.submitted_at ASC, r.id ASC",
            "newest": "r.submitted_at DESC, r.id DESC",
        }[sort]
        with self._lock:
            total = int(
                self._conn.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM verified_reporters r
                    LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                    LEFT JOIN bot_users u ON u.user_id = r.user_id
                    {where}
                    """,
                    params,
                ).fetchone()["count"]
            )
            rows = self._conn.execute(
                f"""
                SELECT r.id, r.user_id, r.status, r.review_state,
                       r.submitted_at, r.decided_at, r.reviewed_by,
                       r.rejection_reason, r.supplement_requested_at,
                       r.supplement_message, r.supplement_requested_documents,
                       r.documents_deleted_at, r.documents_deleted_by,
                       r.documents_deleted_reason,
                       CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents,
                       u.full_name, u.username
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                LEFT JOIN bot_users u ON u.user_id = r.user_id
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (*params, size, offset),
            ).fetchall()
            return [_row_to_dict(row) for row in rows], total

    async def search_verification_requests(
        self,
        *,
        status: str = "pending",
        query: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        documents: str = "all",
        sort: str = "waiting",
        page: int = 0,
        size: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        return await asyncio.to_thread(
            self._search_verification_requests,
            status=status,
            query=query,
            start_date=start_date,
            end_date=end_date,
            documents=documents,
            sort=sort,
            page=page,
            size=size,
        )

    def _verification_analytics(self, days: int = 30) -> dict[str, Any]:
        days = max(7, min(int(days), 90))
        start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
        start_iso = start.isoformat()
        with self._lock:
            counts = self._conn.execute(
                """
                SELECT status, review_state, COUNT(*) AS count
                FROM verified_reporters
                WHERE status != 'capturing'
                GROUP BY status, review_state
                """
            ).fetchall()
            wait = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS open_count,
                    MIN(submitted_at) AS oldest_submitted_at,
                    AVG((julianday(?) - julianday(submitted_at)) * 24)
                        AS average_wait_hours
                FROM verified_reporters
                WHERE status = 'pending' AND submitted_at IS NOT NULL
                """,
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            ).fetchone()
            trend_rows = self._conn.execute(
                """
                SELECT substr(submitted_at, 1, 10) AS day, COUNT(*) AS count
                FROM verified_reporters
                WHERE submitted_at IS NOT NULL AND substr(submitted_at, 1, 10) >= ?
                GROUP BY substr(submitted_at, 1, 10)
                ORDER BY day
                """,
                (start_iso,),
            ).fetchall()
        status_counts = {"pending": 0, "needs_info": 0, "verified": 0, "rejected": 0}
        for row in counts:
            key = (
                "needs_info"
                if row["status"] == "pending" and row["review_state"] == "needs_info"
                else row["status"]
            )
            if key in status_counts:
                status_counts[key] += int(row["count"])
        trend_map = {str(row["day"]): int(row["count"]) for row in trend_rows}
        trend = [
            {"date": (start + timedelta(days=offset)).isoformat(),
             "count": trend_map.get((start + timedelta(days=offset)).isoformat(), 0)}
            for offset in range(days)
        ]
        return {
            "counts": status_counts,
            "waiting": {
                "open_count": int(wait["open_count"] or 0),
                "oldest_submitted_at": wait["oldest_submitted_at"],
                "average_wait_hours": (
                    round(float(wait["average_wait_hours"]), 2)
                    if wait["average_wait_hours"] is not None
                    else None
                ),
            },
            "trend": trend,
        }

    async def verification_analytics(self, days: int = 30) -> dict[str, Any]:
        return await asyncio.to_thread(self._verification_analytics, days)

    def _verification_supplement_history(
        self, request_id: int
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, request_id, requested_documents, message,
                       requested_by, requested_at, response_at
                FROM verification_supplement_requests
                WHERE request_id = ?
                ORDER BY id
                """,
                (request_id,),
            ).fetchall()
        history = []
        for row in rows:
            item = _row_to_dict(row)
            item["requested_documents"] = json.loads(item["requested_documents"])
            history.append(item)
        return history

    async def verification_supplement_history(
        self, request_id: int
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._verification_supplement_history, request_id
        )

    def _verification_audit_history(
        self, request_id: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, ts, actor_id, action, target, details
                FROM audit_log
                WHERE target = ? AND action LIKE 'verified_reporter_%'
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(request_id), limit),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def verification_audit_history(
        self, request_id: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._verification_audit_history, request_id, limit
        )

    def _verification_document_versions(
        self, request_id: int, kind: str
    ) -> list[dict[str, Any]]:
        if kind not in {"selfie", "id_front", "id_back"}:
            raise ValueError("invalid_verification_document_kind")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, kind, submitted_at, submitted_by_user_id
                FROM verified_reporter_document_versions
                WHERE request_id = ? AND kind = ?
                ORDER BY id
                """,
                (request_id, kind),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def verification_document_versions(
        self, request_id: int, kind: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._verification_document_versions, request_id, kind
        )

    def _verification_document_version(
        self, request_id: int, kind: str, version_id: int
    ) -> bytes | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT ciphertext FROM verified_reporter_document_versions
                WHERE request_id = ? AND kind = ? AND id = ?
                """,
                (request_id, kind, version_id),
            ).fetchone()
            return bytes(row["ciphertext"]) if row else None

    async def verification_document_version(
        self, request_id: int, kind: str, version_id: int
    ) -> bytes | None:
        return await asyncio.to_thread(
            self._verification_document_version, request_id, kind, version_id
        )

    def _request_verification_supplement(
        self,
        request_id: int,
        reviewer_id: int,
        requested_documents: list[str],
        message: str,
    ) -> dict[str, Any] | None:
        allowed = {"selfie", "id_front", "id_back"}
        documents = list(dict.fromkeys(requested_documents))
        text = message.strip()
        if not documents or any(item not in allowed for item in documents):
            raise ValueError("invalid_supplement_documents")
        if not text or len(text) > 1000:
            raise ValueError("supplement_message_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._conn:
            request = self._conn.execute(
                """
                SELECT r.user_id
                FROM verified_reporters r
                JOIN verified_reporter_private p ON p.request_id = r.id
                WHERE r.id = ? AND r.status = 'pending'
                """,
                (request_id,),
            ).fetchone()
            if not request:
                return None
            cursor = self._conn.execute(
                """
                INSERT INTO verification_supplement_requests
                    (request_id, requested_documents, message, requested_by, requested_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (request_id, json.dumps(documents), text, reviewer_id, now),
            )
            self._conn.execute(
                """
                UPDATE verified_reporters
                SET review_state = 'needs_info', supplement_requested_at = ?,
                    supplement_message = ?, supplement_requested_documents = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, text, json.dumps(documents), request_id),
            )
            self._conn.execute(
                "DELETE FROM verification_supplement_uploads WHERE request_id = ?",
                (request_id,),
            )
            self._conn.commit()
            return {
                "request_id": request_id,
                "user_id": int(request["user_id"]),
                "supplement_id": int(cursor.lastrowid),
                "requested_documents": documents,
                "message": text,
                "requested_at": now,
            }

    async def request_verification_supplement(
        self,
        request_id: int,
        reviewer_id: int,
        requested_documents: list[str],
        message: str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._request_verification_supplement,
            request_id,
            reviewer_id,
            requested_documents,
            message,
        )

    def _active_verification_supplement(
        self, request_id: int, user_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            request = self._conn.execute(
                """
                SELECT r.id, r.user_id, s.id AS supplement_id,
                       s.requested_documents, s.message, s.requested_at,
                       s.response_at
                FROM verified_reporters r
                JOIN verification_supplement_requests s ON s.request_id = r.id
                WHERE r.id = ? AND r.user_id = ? AND r.status = 'pending'
                  AND r.review_state = 'needs_info' AND s.response_at IS NULL
                ORDER BY s.id DESC LIMIT 1
                """,
                (request_id, user_id),
            ).fetchone()
            if not request:
                return None
            item = _row_to_dict(request)
            item["requested_documents"] = json.loads(item["requested_documents"])
            uploaded = self._conn.execute(
                "SELECT kind FROM verification_supplement_uploads WHERE request_id = ?",
                (request_id,),
            ).fetchall()
            item["uploaded_documents"] = [str(row["kind"]) for row in uploaded]
            return item

    async def active_verification_supplement(
        self, request_id: int, user_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._active_verification_supplement, request_id, user_id
        )

    def _save_verification_supplement_upload(
        self, request_id: int, user_id: int, kind: str, ciphertext: bytes
    ) -> dict[str, Any] | None:
        if kind not in {"selfie", "id_front", "id_back"}:
            raise ValueError("invalid_verification_document_kind")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._conn:
            request = self._conn.execute(
                """
                SELECT s.id, s.requested_documents
                FROM verified_reporters r
                JOIN verified_reporter_private p ON p.request_id = r.id
                JOIN verification_supplement_requests s ON s.request_id = r.id
                WHERE r.id = ? AND r.user_id = ? AND r.status = 'pending'
                  AND r.review_state = 'needs_info' AND s.response_at IS NULL
                ORDER BY s.id DESC LIMIT 1
                """,
                (request_id, user_id),
            ).fetchone()
            if not request:
                return None
            requested = json.loads(request["requested_documents"])
            if kind not in requested:
                raise ValueError("document_not_requested")
            self._conn.execute(
                """
                INSERT INTO verification_supplement_uploads
                    (request_id, kind, ciphertext, submitted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(request_id, kind) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    submitted_at = excluded.submitted_at
                """,
                (request_id, kind, ciphertext, now),
            )
            rows = self._conn.execute(
                "SELECT kind, ciphertext, submitted_at FROM verification_supplement_uploads "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchall()
            uploads = {str(row["kind"]): row for row in rows}
            remaining = [doc for doc in requested if doc not in uploads]
            completed = not remaining
            self._insert_audit_locked(
                user_id,
                "verified_reporter_supplement_received",
                str(request_id),
                f"document={kind}; completed={str(completed).lower()}",
            )
            if completed:
                for doc in requested:
                    row = uploads[doc]
                    self._conn.execute(
                        """
                        INSERT INTO verified_reporter_document_versions
                            (request_id, kind, ciphertext, submitted_at,
                             submitted_by_user_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (request_id, doc, row["ciphertext"], row["submitted_at"], user_id),
                    )
                    column = {
                        "selfie": "selfie_ciphertext",
                        "id_front": "id_front_ciphertext",
                        "id_back": "id_back_ciphertext",
                    }[doc]
                    self._conn.execute(
                        f"UPDATE verified_reporter_private SET {column} = ? "
                        "WHERE request_id = ? AND user_id = ?",
                        (row["ciphertext"], request_id, user_id),
                    )
                self._conn.execute(
                    """
                    UPDATE verification_supplement_requests
                    SET response_at = ?
                    WHERE id = ? AND response_at IS NULL
                    """,
                    (now, request["id"]),
                )
                self._conn.execute(
                    """
                    UPDATE verified_reporters SET review_state = 'pending'
                    WHERE id = ? AND user_id = ? AND status = 'pending'
                    """,
                    (request_id, user_id),
                )
                self._conn.execute(
                    "DELETE FROM verification_supplement_uploads WHERE request_id = ?",
                    (request_id,),
                )
            self._conn.commit()
            return {
                "request_id": request_id,
                "user_id": user_id,
                "uploaded_documents": sorted(uploads),
                "requested_documents": requested,
                "remaining_documents": remaining,
                "completed": completed,
            }

    async def save_verification_supplement_upload(
        self, request_id: int, user_id: int, kind: str, ciphertext: bytes
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._save_verification_supplement_upload,
            request_id,
            user_id,
            kind,
            ciphertext,
        )

    def _verification_detail(self, request_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT r.*, CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents,
                       u.full_name, u.username
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                LEFT JOIN bot_users u ON u.user_id = r.user_id
                WHERE r.id = ?
                """,
                (request_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def verification_detail(self, request_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._verification_detail, request_id)

    def _verification_document(
        self, request_id: int, kind: str
    ) -> bytes | None:
        columns = {
            "selfie": "selfie_ciphertext",
            "id_front": "id_front_ciphertext",
            "id_back": "id_back_ciphertext",
        }
        column = columns.get(kind)
        if column is None:
            raise ValueError("invalid_verification_document_kind")
        with self._lock:
            row = self._conn.execute(
                f"SELECT {column} AS document FROM verified_reporter_private "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return bytes(row["document"]) if row else None

    async def verification_document(
        self, request_id: int, kind: str
    ) -> bytes | None:
        return await asyncio.to_thread(
            self._verification_document, request_id, kind
        )

    def _verification_phone(self, request_id: int) -> bytes | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT phone_ciphertext FROM verified_reporter_private "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return bytes(row["phone_ciphertext"]) if row else None

    async def verification_phone(self, request_id: int) -> bytes | None:
        return await asyncio.to_thread(self._verification_phone, request_id)

    def _decide_verification(
        self, request_id: int, reviewer_id: int, decision: str,
        rejection_reason: str | None = None,
    ) -> bool:
        if decision not in {"verified", "rejected"}:
            raise ValueError("invalid_verification_decision")
        reason = (rejection_reason or "").strip()
        if decision == "rejected" and not reason:
            raise ValueError("verification_rejection_reason_required")
        if len(reason) > 1000:
            raise ValueError("verification_rejection_reason_too_long")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            docs = self._conn.execute(
                "SELECT 1 FROM verified_reporter_private WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if not docs:
                return False
            cursor = self._conn.execute(
                """
                UPDATE verified_reporters
                SET status = ?, decided_at = ?, reviewed_by = ?,
                    rejection_reason = ?, review_state = 'pending'
                WHERE id = ? AND status IN ('pending', 'verified')
                """,
                (
                    decision, now, reviewer_id,
                    reason if decision == "rejected" else None,
                    request_id,
                ),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    async def decide_verification(
        self, request_id: int, reviewer_id: int, decision: str,
        rejection_reason: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._decide_verification,
            request_id,
            reviewer_id,
            decision,
            rejection_reason,
        )

    def _count_events(self, since_iso: str, user_id: Optional[int] = None,
                      kind: Optional[str] = None) -> int:
        with self._lock:
            query = "SELECT COUNT(*) AS c FROM user_events e WHERE e.ts >= ?"
            params: list[Any] = [since_iso]
            if user_id is not None:
                query += (
                    " AND e.user_id = ? AND e.ts >= COALESCE("
                    "(SELECT reset_at FROM user_limit_resets WHERE user_id = ?), '')"
                )
                params.extend((user_id, user_id))
            if kind:
                query += " AND e.kind = ?"
                params.append(kind)
            return int(self._conn.execute(query, params).fetchone()["c"])

    async def count_events(self, since_iso: str, user_id: Optional[int] = None,
                           kind: Optional[str] = None) -> int:
        return await asyncio.to_thread(self._count_events, since_iso, user_id, kind)

    def _prune_events(self, before_iso: str) -> int:
        """حذف الأحداث القديمة حتى لا تنمو القاعدة بلا حد."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM user_events WHERE ts < ?", (before_iso,))
            self._conn.commit()
            return cur.rowcount

    async def prune_events(self, before_iso: str) -> int:
        return await asyncio.to_thread(self._prune_events, before_iso)

    def _get_block(self, user_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_blocks WHERE user_id = ?", (user_id,)
            ).fetchone()
            return _row_to_dict(row) if row else {}

    async def get_block(self, user_id: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_block, user_id)

    def _save_block(
        self,
        user_id: int,
        strikes: int,
        frozen_until: Optional[str],
        reason: str,
        evidence_photo_path: str | None = None,
        evidence_photo_file_id: str | None = None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        notification = None
        with self._lock:
            previous = self._conn.execute(
                "SELECT frozen_until FROM user_blocks WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            self._conn.execute(
                """
                INSERT INTO user_blocks (user_id, strikes, frozen_until, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    strikes=excluded.strikes,
                    frozen_until=excluded.frozen_until,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (user_id, strikes, frozen_until, reason, now_iso),
            )
            if evidence_photo_path or evidence_photo_file_id:
                self._conn.execute(
                    """
                    INSERT INTO blocked_user_evidence
                        (user_id, photo_path, photo_file_id, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        photo_path = excluded.photo_path,
                        photo_file_id = excluded.photo_file_id,
                        created_at = excluded.created_at
                    """,
                    (
                        user_id,
                        evidence_photo_path or None,
                        evidence_photo_file_id or None,
                        now_iso,
                    ),
                )
            if frozen_until and (
                not previous or previous["frozen_until"] != frozen_until
            ):
                latest_report = self._conn.execute(
                    "SELECT id FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
                notification = self._insert_notification_locked(
                    "user_blocked",
                    {
                        "user_id": user_id,
                        "reason": reason,
                        "frozen_until": frozen_until,
                        "report_id": int(latest_report["id"]) if latest_report else None,
                    },
                )
            self._conn.commit()
        self._publish_notification(notification)

    async def save_block(
        self,
        user_id: int,
        strikes: int,
        frozen_until: Optional[str],
        reason: str,
        evidence_photo_path: str | None = None,
        evidence_photo_file_id: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._save_block,
            user_id,
            strikes,
            frozen_until,
            reason,
            evidence_photo_path,
            evidence_photo_file_id,
        )

    def _clear_block(self, user_id: int) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._lock:
            evidence = self._conn.execute(
                "SELECT photo_path FROM blocked_user_evidence WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            self._conn.execute(
                """
                INSERT INTO user_blocks
                    (user_id, strikes, frozen_until, reason, updated_at)
                VALUES (?, 0, NULL, NULL, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    strikes = 0, frozen_until = NULL, reason = NULL,
                    updated_at = excluded.updated_at
                """,
                (user_id, now_iso),
            )
            self._conn.execute(
                """
                INSERT INTO user_limit_resets (user_id, reset_at) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET reset_at = excluded.reset_at
                """,
                (user_id, now_iso),
            )
            self._conn.execute(
                "DELETE FROM blocked_user_evidence WHERE user_id = ?",
                (user_id,),
            )
            self._conn.commit()
            if evidence:
                self._remove_local_media_if_unreferenced(evidence["photo_path"])

    async def clear_block(self, user_id: int) -> None:
        await asyncio.to_thread(self._clear_block, user_id)

    def _audit(self, actor_id: Optional[int], action: str, target: Optional[str],
               details: Optional[str]) -> None:
        with self._lock:
            self._insert_audit_locked(actor_id, action, target, details)
            self._conn.commit()

    def _insert_audit_locked(
        self,
        actor_id: Optional[int],
        action: str,
        target: Optional[str],
        details: Optional[str],
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT INTO audit_log (ts, actor_id, action, target, details)"
            " VALUES (?, ?, ?, ?, ?)",
            (now_iso, actor_id, action, target, details),
        )

    async def audit(self, actor_id: Optional[int], action: str, target: Optional[str] = None,
                    details: Optional[str] = None) -> None:
        await asyncio.to_thread(self._audit, actor_id, action, target, details)

    def _recent_audit(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    async def recent_audit(self, limit: int = 30) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_audit, limit)

    def _audit_range(
        self, start_iso: str, end_exclusive_iso: str, limit: int = 5000
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE ts >= ? AND ts < ? "
                "ORDER BY id DESC LIMIT ?",
                (start_iso, end_exclusive_iso, limit),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def audit_range(
        self, start_iso: str, end_exclusive_iso: str, limit: int = 5000
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._audit_range, start_iso, end_exclusive_iso, limit
        )

    def _delete_audit_range(self, start_iso: str, end_exclusive_iso: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM audit_log WHERE ts >= ? AND ts < ?",
                (start_iso, end_exclusive_iso),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    async def delete_audit_range(self, start_iso: str, end_exclusive_iso: str) -> int:
        return await asyncio.to_thread(
            self._delete_audit_range, start_iso, end_exclusive_iso
        )

    def _get_state(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    async def get_state(self, key: str) -> Optional[str]:
        return await asyncio.to_thread(self._get_state, key)

    def admin_auth_snapshot(self) -> tuple[list[dict[str, Any]], bool]:
        """Return persisted admin credentials and whether environment tokens are disabled."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM system_state "
                "WHERE key IN ('admin_auth_accounts', 'admin_auth_bootstrap_disabled')"
            ).fetchall()
        state = {str(row["key"]): row["value"] for row in rows}
        raw_accounts = state.get("admin_auth_accounts")
        accounts = json.loads(raw_accounts) if raw_accounts else []
        if not isinstance(accounts, list) or any(
            not isinstance(account, dict) for account in accounts
        ):
            raise ValueError("Invalid persisted admin account data")
        return accounts, state.get("admin_auth_bootstrap_disabled") == "1"

    def save_admin_auth_snapshot(
        self, accounts: list[dict[str, Any]], *, disable_bootstrap: bool = False
    ) -> None:
        with self._lock:
            self._save_admin_auth_snapshot_locked(
                accounts,
                disable_bootstrap=disable_bootstrap,
            )
            self._conn.commit()

    def _save_admin_auth_snapshot_locked(
        self, accounts: list[dict[str, Any]], *, disable_bootstrap: bool
    ) -> None:
        serialized = json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            "INSERT INTO system_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("admin_auth_accounts", serialized),
        )
        if disable_bootstrap:
            self._conn.execute(
                "INSERT INTO system_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("admin_auth_bootstrap_disabled", "1"),
            )

    def mutate_admin_auth_snapshot(
        self,
        mutation: Callable[[list[dict[str, Any]], bool], Any],
        *,
        disable_bootstrap: bool = False,
        audit_event: tuple[
            Optional[int], str, Optional[str], Optional[str]
        ] | None = None,
    ) -> Any:
        """Apply one serialized account update while holding the database lock."""
        with self._lock:
            if audit_event is not None:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    accounts, bootstrap_disabled = self.admin_auth_snapshot()
                    result = mutation(accounts, bootstrap_disabled)
                    if result is False:
                        self._conn.rollback()
                        return result
                    self._save_admin_auth_snapshot_locked(
                        accounts,
                        disable_bootstrap=disable_bootstrap,
                    )
                    self._insert_audit_locked(*audit_event)
                    self._conn.commit()
                    return result
                except BaseException:
                    self._conn.rollback()
                    raise

            accounts, bootstrap_disabled = self.admin_auth_snapshot()
            result = mutation(accounts, bootstrap_disabled)
            if result is not False:
                self.save_admin_auth_snapshot(
                    accounts,
                    disable_bootstrap=disable_bootstrap,
                )
            return result

    def _set_state(self, key: str, value: Optional[str]) -> None:
        notification = None
        with self._lock:
            previous = None
            if key == "emergency":
                row = self._conn.execute(
                    "SELECT value FROM system_state WHERE key = ?", (key,)
                ).fetchone()
                previous = row["value"] if row else None
            if value is None:
                self._conn.execute("DELETE FROM system_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO system_state (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            if key == "emergency" and value == "1" and previous != "1":
                notification = self._insert_notification_locked(
                    "emergency",
                    {"on": True, "created_at": datetime.now(timezone.utc).isoformat()},
                )
            self._conn.commit()
        self._publish_notification(notification)

    async def set_state(self, key: str, value: Optional[str]) -> None:
        await asyncio.to_thread(self._set_state, key, value)

    def _record_button_press(self, action: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_button_stats (action, presses, updated_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(action) DO UPDATE SET "
                "presses = presses + 1, updated_at = excluded.updated_at",
                (action, now),
            )
            self._conn.commit()

    async def record_button_press(self, action: str) -> None:
        await asyncio.to_thread(self._record_button_press, action)

    def _button_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT action, presses, updated_at FROM bot_button_stats "
                "ORDER BY presses DESC, action ASC"
            ).fetchall()
        return [
            {
                "action": str(row["action"]),
                "presses": int(row["presses"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    async def button_stats(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._button_stats)

    def _reset_button_stats(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bot_button_stats")
            self._conn.commit()

    async def reset_button_stats(self) -> None:
        await asyncio.to_thread(self._reset_button_stats)

    def _get_welcome_photo(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data, content_type, filename, updated_at "
                "FROM welcome_photo WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            return {
                "data": bytes(row["data"]),
                "content_type": str(row["content_type"]),
                "filename": str(row["filename"]),
                "updated_at": str(row["updated_at"]),
            }

    async def get_welcome_photo(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_welcome_photo)

    def _has_welcome_photo(self) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM welcome_photo WHERE id = 1"
            ).fetchone() is not None

    async def has_welcome_photo(self) -> bool:
        return await asyncio.to_thread(self._has_welcome_photo)

    def _set_welcome_photo(
        self, data: bytes, content_type: str, filename: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO welcome_photo (id, data, content_type, filename, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data = excluded.data,
                    content_type = excluded.content_type,
                    filename = excluded.filename,
                    updated_at = excluded.updated_at
                """,
                (
                    data,
                    content_type,
                    filename,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    async def set_welcome_photo(
        self, data: bytes, content_type: str, filename: str
    ) -> None:
        await asyncio.to_thread(
            self._set_welcome_photo, data, content_type, filename
        )

    def _delete_welcome_photo(self) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM welcome_photo WHERE id = 1")
            self._conn.commit()
            return cursor.rowcount > 0

    async def delete_welcome_photo(self) -> bool:
        return await asyncio.to_thread(self._delete_welcome_photo)

    def _insert_notification_locked(
        self,
        notification_type: str,
        payload: dict[str, Any],
        target_admin_id: int | None = None,
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = self._conn.execute(
            """
            INSERT INTO notifications (type, payload, created_at, target_admin_id)
            VALUES (?, ?, ?, ?)
            """,
            (
                notification_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                created_at,
                target_admin_id,
            ),
        )
        return {
            "id": int(cursor.lastrowid),
            "type": notification_type,
            "payload": payload,
            "created_at": created_at,
            "is_read": False,
            "target_admin_id": target_admin_id,
        }

    def _publish_notification(self, notification: dict[str, Any] | None) -> None:
        if notification is None:
            return
        with self._lock:
            subscribers = tuple(self._notification_subscribers)
        for subscriber in subscribers:
            subscriber.put(notification)

    def subscribe_notifications(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._notification_subscribers.add(subscriber)
        return subscriber

    def unsubscribe_notifications(
        self, subscriber: queue.Queue[dict[str, Any]]
    ) -> None:
        with self._lock:
            self._notification_subscribers.discard(subscriber)

    def _create_notification(
        self,
        notification_type: str,
        payload: dict[str, Any],
        target_admin_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            notification = self._insert_notification_locked(
                notification_type, payload, target_admin_id
            )
            self._conn.commit()
        self._publish_notification(notification)
        return notification

    async def create_notification(
        self,
        notification_type: str,
        payload: dict[str, Any],
        target_admin_id: int | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_notification,
            notification_type,
            payload,
            target_admin_id,
        )

    def _list_notifications(
        self, unread_only: bool = False, limit: int = 20, kind: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        if kind not in (None, "regular", "heartbeat"):
            raise ValueError("invalid notification kind")
        with self._lock:
            unread_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) AS count FROM notifications WHERE is_read = 0"
                ).fetchone()["count"]
            )
            clauses: list[str] = []
            if unread_only:
                clauses.append("is_read = 0")
            if kind == "heartbeat":
                clauses.append("type = 'heartbeat_fail'")
            elif kind == "regular":
                clauses.append("type != 'heartbeat_fail'")
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = self._conn.execute(
                f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            item = _row_to_dict(row)
            item["payload"] = json.loads(item["payload"])
            item["is_read"] = bool(item["is_read"])
            items.append(item)
        return items, unread_count

    async def list_notifications(
        self, unread_only: bool = False, limit: int = 20, kind: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return await asyncio.to_thread(
            self._list_notifications, unread_only, limit, kind
        )

    def _notification_stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread,
                       MIN(created_at) AS oldest_created_at,
                       MIN(CASE WHEN is_read = 1 THEN created_at END)
                           AS oldest_read_created_at
                FROM notifications
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "unread": int(row["unread"] or 0),
            "oldest_created_at": row["oldest_created_at"],
            "oldest_read_created_at": row["oldest_read_created_at"],
        }

    async def notification_stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._notification_stats)

    def _cleanup_notifications(
        self,
        now: datetime | None = None,
    ) -> int:
        current = now or datetime.now(timezone.utc)
        read_cutoff = (current - timedelta(days=7)).isoformat(timespec="seconds")
        unread_cutoff = (current - timedelta(days=30)).isoformat(timespec="seconds")
        protected_cutoff = (current - timedelta(days=90)).isoformat(
            timespec="seconds"
        )
        with self._lock:
            cursor = self._conn.execute(
                """
                DELETE FROM notifications
                WHERE
                    (type IN ('emergency', 'heartbeat_fail') AND created_at < ?)
                    OR
                    (type NOT IN ('emergency', 'heartbeat_fail') AND (
                        (is_read = 1 AND created_at < ?)
                        OR (is_read = 0 AND created_at < ?)
                    ))
                """,
                (protected_cutoff, read_cutoff, unread_cutoff),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    async def cleanup_notifications(self) -> int:
        return await asyncio.to_thread(self._cleanup_notifications)

    def _clear_notifications(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=90)
        ).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                """
                DELETE FROM notifications
                WHERE type NOT IN ('emergency', 'heartbeat_fail')
                   OR created_at < ?
                """,
                (cutoff,),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    async def clear_notifications(self) -> int:
        return await asyncio.to_thread(self._clear_notifications)

    def _mark_notification_read(self, notification_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ?",
                (notification_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def mark_notification_read(self, notification_id: int) -> bool:
        return await asyncio.to_thread(
            self._mark_notification_read, notification_id
        )

    def _mark_all_notifications_read(self) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE is_read = 0"
            )
            self._conn.commit()
            return int(cursor.rowcount)

    async def mark_all_notifications_read(self) -> int:
        return await asyncio.to_thread(self._mark_all_notifications_read)

    def _add_fingerprint(self, report_id: int, link_hash: Optional[str],
                         text_hash: Optional[str], photo_hash: Optional[str]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO report_fingerprints"
                " (report_id, link_hash, text_hash, photo_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (report_id, link_hash, text_hash, photo_hash, now_iso),
            )
            self._conn.commit()

    async def add_fingerprint(self, report_id: int, link_hash: Optional[str],
                              text_hash: Optional[str], photo_hash: Optional[str]) -> None:
        await asyncio.to_thread(self._add_fingerprint, report_id, link_hash, text_hash, photo_hash)

    def _find_fingerprints(self, link_hash: Optional[str] = None,
                           text_hash: Optional[str] = None,
                           photo_hash: Optional[str] = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        for column, value in (("link_hash", link_hash), ("text_hash", text_hash),
                              ("photo_hash", photo_hash)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if not clauses:
            return []
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM report_fingerprints WHERE {' OR '.join(clauses)}", params
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    async def find_fingerprints(self, link_hash: Optional[str] = None,
                                text_hash: Optional[str] = None,
                                photo_hash: Optional[str] = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._find_fingerprints, link_hash, text_hash, photo_hash)

    def _recent_descriptions(self, limit: int = 60) -> list[tuple[int, str, int]]:
        """آخر الأوصاف (رقم البلاغ، النص، معرّف المبلّغ) — لكشف التشابه."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, description, user_id FROM reports ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [(int(r["id"]), r["description"], int(r["user_id"])) for r in rows]

    async def recent_descriptions(self, limit: int = 60) -> list[tuple[int, str, int]]:
        return await asyncio.to_thread(self._recent_descriptions, limit)

    def _count_user_reports(self, user_id: int) -> tuple[int, int]:
        """(عدد المعتمدة، عدد المرفوضة) لمستخدم معيّن — أساس حساب السمعة."""
        with self._lock:
            row = self._conn.execute(
                "SELECT"
                " SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS ok,"
                " SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS rejected"
                " FROM reports WHERE user_id = ?",
                (STATUS_APPROVED, STATUS_REJECTED, user_id),
            ).fetchone()
            return int(row["ok"] or 0), int(row["rejected"] or 0)

    async def count_user_reports(self, user_id: int) -> tuple[int, int]:
        return await asyncio.to_thread(self._count_user_reports, user_id)

    # --------------------------------------------------------------
    # أدوات لوحة الإدارة (واجهة admin_api) — بحث متقدم + إدارة + مستخدمون
    # --------------------------------------------------------------
    def _list_reports_filtered(
        self,
        status: Optional[str] = None,
        vtype: Optional[str] = None,
        user_id: Optional[int] = None,
        assigned_to: Optional[int] = None,
        unassigned_only: bool = False,
        priority: Optional[str] = None,
        overdue_only: bool = False,
        q: Optional[str] = None,
        page: int = 0,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """قائمة بلاغات بفلاتر كامل (الحالة/النوع/المستخدم/نص حر) + العدد الكلي."""
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if vtype:
            clauses.append("violation_type = ?")
            params.append(vtype)
        if user_id:
            clauses.append("user_id = ?")
            params.append(int(user_id))
        if assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(int(assigned_to))
        if unassigned_only:
            clauses.append("assigned_to IS NULL")
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        if overdue_only:
            clauses.append("due_at IS NOT NULL AND due_at < ? AND status NOT IN (?, ?)")
            params.extend([
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                STATUS_APPROVED,
                STATUS_REJECTED,
            ])
        if q:
            like = f"%{q.strip()}%"
            clauses.append(
                "(seq LIKE ? OR full_name LIKE ? OR description LIKE ?"
                " OR post_link LIKE ? OR CAST(user_id AS TEXT) LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        size = max(1, min(int(size), 100))
        page = max(0, int(page))
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM reports{where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT reports.*,"
                " (SELECT last_seen_at FROM bot_users"
                " WHERE bot_users.user_id = reports.user_id) AS user_last_at"
                f" FROM reports{where}"
                " ORDER BY CASE priority WHEN 'urgent' THEN 0 "
                "WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
                "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at ASC, "
                "created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, size, page * size],
            ).fetchall()
            return [_row_to_dict(r) for r in rows], int(total)

    async def list_reports_filtered(self, **kwargs):
        return await asyncio.to_thread(self._list_reports_filtered, **kwargs)

    def _workflow_counts(self) -> dict[str, int]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT "
                "SUM(CASE WHEN priority = 'urgent' AND status NOT IN (?, ?) "
                "THEN 1 ELSE 0 END) AS urgent, "
                "SUM(CASE WHEN due_at IS NOT NULL AND due_at < ? "
                "AND status NOT IN (?, ?) THEN 1 ELSE 0 END) AS overdue, "
                "SUM(CASE WHEN assigned_to IS NULL AND status NOT IN (?, ?) "
                "THEN 1 ELSE 0 END) AS unassigned "
                "FROM reports",
                (
                    STATUS_APPROVED, STATUS_REJECTED, now,
                    STATUS_APPROVED, STATUS_REJECTED,
                    STATUS_APPROVED, STATUS_REJECTED,
                ),
            ).fetchone()
            return {key: int(row[key] or 0) for key in ("urgent", "overdue", "unassigned")}

    async def workflow_counts(self) -> dict[str, int]:
        return await asyncio.to_thread(self._workflow_counts)

    def _update_report_workflow(
        self, rid: int, priority: str, assigned_to: Optional[int],
        due_at: Optional[str],
    ) -> bool:
        notification = None
        with self._lock:
            previous = self._conn.execute(
                "SELECT priority, seq FROM reports WHERE id = ?", (rid,)
            ).fetchone()
            cur = self._conn.execute(
                "UPDATE reports SET priority = ?, assigned_to = ?, due_at = ? WHERE id = ?",
                (priority, assigned_to, due_at, rid),
            )
            if (
                cur.rowcount > 0
                and priority == "urgent"
                and previous
                and previous["priority"] != "urgent"
            ):
                notification = self._insert_notification_locked(
                    "report_urgent",
                    {"report_id": rid, "seq": previous["seq"] or ""},
                )
            self._conn.commit()
        self._publish_notification(notification)
        return cur.rowcount > 0

    async def update_report_workflow(
        self, rid: int, priority: str, assigned_to: Optional[int],
        due_at: Optional[str],
    ) -> bool:
        return await asyncio.to_thread(
            self._update_report_workflow, rid, priority, assigned_to, due_at
        )

    def _set_followup_request(
        self, rid: int, note: Optional[str], due_at: str
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET followup_requested_at = ?, followup_note = ?, "
                "followup_response = NULL, followup_due_at = ?, status = ? "
                "WHERE id = ?",
                (now, note, due_at, STATUS_AWAITING_INFO, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def set_followup_request(
        self, rid: int, note: Optional[str], due_at: str
    ) -> bool:
        return await asyncio.to_thread(
            self._set_followup_request, rid, note, due_at
        )

    def _clear_followup_request(self, rid: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET followup_requested_at = NULL, "
                "followup_note = NULL, followup_due_at = NULL, status = ? "
                "WHERE id = ?",
                (STATUS_REVIEW, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def clear_followup_request(self, rid: int) -> bool:
        return await asyncio.to_thread(self._clear_followup_request, rid)

    def _save_followup_response(self, rid: int, response: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET followup_response = ?, "
                "followup_requested_at = NULL, followup_note = NULL, "
                "followup_due_at = NULL, status = ? WHERE id = ?",
                (response, STATUS_REVIEW, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def save_followup_response(self, rid: int, response: str) -> bool:
        return await asyncio.to_thread(self._save_followup_response, rid, response)

    def _close_expired_followups(self, now_iso: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, user_id, seq, followup_due_at
                FROM reports
                WHERE status = ? AND followup_due_at <= ?
                  AND followup_response IS NULL
                """,
                (STATUS_AWAITING_INFO, now_iso),
            ).fetchall()
            if not rows:
                return []
            self._conn.execute(
                """
                UPDATE reports
                SET status = ?, reviewed_at = ?, followup_due_at = NULL
                WHERE status = ? AND followup_due_at <= ?
                  AND followup_response IS NULL
                """,
                (
                    STATUS_CLOSED_NO_RESPONSE,
                    now_iso,
                    STATUS_AWAITING_INFO,
                    now_iso,
                ),
            )
            for row in rows:
                self._insert_audit_locked(
                    None,
                    "report_followup_auto_closed",
                    str(row["id"]),
                    "انتهت مهلة اليومين دون رد من المبلّغ",
                )
            self._conn.commit()
            return [_row_to_dict(row) for row in rows]

    async def close_expired_followups(
        self, now_iso: str | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._close_expired_followups,
            now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _add_report_appeal(
        self, report_id: int, user_id: int, message: str
    ) -> int | None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            report = self._conn.execute(
                "SELECT status FROM reports WHERE id = ? AND user_id = ?",
                (report_id, user_id),
            ).fetchone()
            if not report or report["status"] != STATUS_REJECTED:
                return None
            cursor = self._conn.execute(
                """
                INSERT INTO report_appeals
                    (report_id, user_id, message, submitted_at)
                VALUES (?, ?, ?, ?)
                """,
                (report_id, user_id, message, now),
            )
            self._conn.execute(
                "UPDATE reports SET needs_review = 1 WHERE id = ?",
                (report_id,),
            )
            self._insert_audit_locked(
                user_id,
                "report_appeal_submitted",
                str(report_id),
                f"عدد الأحرف: {len(message)}",
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    async def add_report_appeal(
        self, report_id: int, user_id: int, message: str
    ) -> int | None:
        return await asyncio.to_thread(
            self._add_report_appeal, report_id, user_id, message
        )

    def _list_report_appeals(self, report_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, report_id, user_id, message, submitted_at, status
                FROM report_appeals
                WHERE report_id = ?
                ORDER BY submitted_at ASC, id ASC
                """,
                (report_id,),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_report_appeals(self, report_id: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_report_appeals, report_id)

    async def get_runtime_settings(self) -> dict[str, Any]:
        raw = await self.get_state("runtime_settings")
        try:
            parsed = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    async def set_runtime_settings(self, settings: dict[str, Any]) -> None:
        await self.set_state(
            "runtime_settings", json.dumps(settings, ensure_ascii=False, sort_keys=True)
        )

    def _set_violation_type(self, rid: int, vtype: Optional[str]) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET violation_type = ? WHERE id = ?",
                (vtype, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def set_violation_type(self, rid: int, vtype: Optional[str]) -> bool:
        return await asyncio.to_thread(self._set_violation_type, rid, vtype)

    def _set_admin_note(self, rid: int, note: Optional[str]) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET admin_note = ? WHERE id = ?",
                (note, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def set_admin_note(self, rid: int, note: Optional[str]) -> bool:
        return await asyncio.to_thread(self._set_admin_note, rid, note)

    def _delete_report(self, rid: int) -> bool:
        """حذف نهائي لبلاغ واحد مع بصمته، مع إبقاء الأثر في سجل التدقيق."""
        with self._lock:
            row = self._conn.execute(
                "SELECT photo_path FROM reports WHERE id = ?", (rid,)
            ).fetchone()
            cur = self._conn.execute("DELETE FROM reports WHERE id = ?", (rid,))
            if cur.rowcount > 0:
                self._conn.execute(
                    "DELETE FROM report_appeals WHERE report_id = ?", (rid,)
                )
                self._conn.execute(
                    "DELETE FROM report_fingerprints WHERE report_id = ?", (rid,)
                )
            self._conn.commit()
            if cur.rowcount > 0 and row:
                self._remove_local_media_if_unreferenced(row["photo_path"])
            return cur.rowcount > 0

    async def delete_report(self, rid: int) -> bool:
        return await asyncio.to_thread(self._delete_report, rid)

    def _remove_local_media_if_unreferenced(self, raw_path: str | None) -> None:
        if not raw_path:
            return
        from app import config

        path = Path(raw_path)
        if not path.is_absolute():
            path = config.BASE_DIR / path
        media_root = config.MEDIA_DIR.resolve()
        try:
            resolved = path.resolve()
            resolved.relative_to(media_root)
        except (OSError, ValueError):
            return
        referenced = self._conn.execute(
            """
            SELECT 1 FROM reports WHERE photo_path = ?
            UNION ALL
            SELECT 1 FROM blocked_user_evidence WHERE photo_path = ?
            LIMIT 1
            """,
            (raw_path, raw_path),
        ).fetchone()
        if referenced:
            return
        try:
            resolved.unlink(missing_ok=True)
        except OSError:
            logger.warning("تعذر حذف ملف وسائط يتيم: %s", resolved, exc_info=True)

    def _cleanup_orphaned_media(self, older_than_days: int = 7) -> int:
        from app import config

        cutoff = datetime.now(timezone.utc).timestamp() - max(1, older_than_days) * 86400
        media_root = config.MEDIA_DIR.resolve()
        deleted = 0
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT photo_path FROM reports WHERE photo_path != ''
                UNION
                SELECT photo_path FROM blocked_user_evidence
                WHERE photo_path IS NOT NULL AND photo_path != ''
                """
            ).fetchall()
            referenced: set[Path] = set()
            for row in rows:
                raw_path = str(row["photo_path"])
                path = Path(raw_path)
                if not path.is_absolute():
                    path = config.BASE_DIR / path
                try:
                    resolved = path.resolve()
                    resolved.relative_to(media_root)
                    referenced.add(resolved)
                except (OSError, ValueError):
                    continue
            for path in media_root.iterdir():
                try:
                    resolved = path.resolve()
                    resolved.relative_to(media_root)
                    if (
                        resolved in referenced
                        or not resolved.is_file()
                        or resolved.stat().st_mtime >= cutoff
                    ):
                        continue
                    resolved.unlink()
                    deleted += 1
                except OSError:
                    logger.warning("تعذر تنظيف ملف وسائط يتيم: %s", path, exc_info=True)
        return deleted

    async def cleanup_orphaned_media(self, older_than_days: int = 7) -> int:
        return await asyncio.to_thread(self._cleanup_orphaned_media, older_than_days)

    def _cleanup_private_data(
        self,
        now: datetime | None = None,
        audit_retention_days: int = 365,
        message_retention_days: int = 90,
        verification_document_retention_days: int = 30,
    ) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        audit_cutoff = (
            current - timedelta(days=audit_retention_days)
        ).isoformat(timespec="seconds")
        message_cutoff = (
            current - timedelta(days=message_retention_days)
        ).isoformat(timespec="seconds")
        now_iso = current.isoformat(timespec="seconds")
        with self._lock:
            audit = self._conn.execute(
                "DELETE FROM audit_log WHERE ts < ?", (audit_cutoff,)
            )
            messages = self._conn.execute(
                "DELETE FROM user_messages WHERE created_at < ?", (message_cutoff,)
            )
            deliveries = self._conn.execute(
                "DELETE FROM broadcast_deliveries WHERE sent_at < ?",
                (message_cutoff,),
            )
            edits = self._conn.execute(
                "DELETE FROM broadcast_edit_jobs WHERE created_at < ?",
                (message_cutoff,),
            )
            deletions = self._conn.execute(
                "DELETE FROM broadcast_delete_jobs WHERE created_at < ?",
                (message_cutoff,),
            )
            broadcasts = self._conn.execute(
                """
                DELETE FROM broadcast_jobs
                WHERE created_at < ?
                  AND status NOT IN ('queued', 'running', 'cancel_requested')
                  AND NOT EXISTS (
                    SELECT 1 FROM broadcast_deliveries d
                    WHERE d.job_id = broadcast_jobs.id
                  )
                """,
                (message_cutoff,),
            )
            expired_tickets = self._conn.execute(
                "SELECT user_id, request_id FROM verification_tickets "
                "WHERE expires_at <= ?",
                (now_iso,),
            ).fetchall()
            for ticket in expired_tickets:
                self._conn.execute(
                    "DELETE FROM verification_tickets WHERE user_id = ?",
                    (ticket["user_id"],),
                )
                self._conn.execute(
                    "DELETE FROM verified_reporters "
                    "WHERE id = ? AND status = 'capturing'",
                    (ticket["request_id"],),
                )
            verification_tickets_deleted = len(expired_tickets)
            # Identity files and their request records require explicit audited deletion.
            self._conn.execute(
                """
                DELETE FROM verified_reporter_private
                WHERE request_id NOT IN (
                    SELECT id FROM verified_reporters
                )
                """
            )
            self._conn.commit()
            return {
                "audit_deleted": int(audit.rowcount),
                "messages_deleted": int(messages.rowcount),
                "broadcast_deliveries_deleted": int(deliveries.rowcount),
                "broadcast_edit_jobs_deleted": int(edits.rowcount),
                "broadcast_delete_jobs_deleted": int(deletions.rowcount),
                "broadcast_jobs_deleted": int(broadcasts.rowcount),
                "verification_tickets_deleted": verification_tickets_deleted,
                "verification_documents_deleted": 0,
                "verification_requests_deleted": 0,
            }

    async def cleanup_private_data(
        self,
        audit_retention_days: int = 365,
        message_retention_days: int = 90,
        verification_document_retention_days: int = 30,
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._cleanup_private_data,
            None,
            audit_retention_days,
            message_retention_days,
            verification_document_retention_days,
        )

    def _list_users(
        self, q: Optional[str] = None, page: int = 0, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """قائمة مستخدمي البوت: الاسم، البلاغات، آخر ظهور + حالة الحجب."""
        like = f"%{(q or '').strip()}%"
        where = ""
        params: list[Any] = []
        if q and q.strip():
            where = (
                " WHERE COALESCE(u.full_name, '') LIKE ?"
                " OR COALESCE(u.username, '') LIKE ?"
                " OR CAST(u.user_id AS TEXT) LIKE ?"
            )
            params = [like, like, like]
        size = max(1, min(int(size), 100))
        page = max(0, int(page))
        base = f"""
            SELECT u.user_id AS user_id,
                   u.full_name AS full_name,
                   u.username AS username,
                   COALESCE(r.reports_count, 0) AS reports_count,
                   u.last_seen_at AS last_at,
                   b.frozen_until AS frozen_until,
                   b.reason AS block_reason,
                   COALESCE(c.enabled, 1) AS contact_enabled,
                   COALESCE(vr.status, 'unverified') AS verification_status
            FROM bot_users u
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS reports_count
                FROM reports GROUP BY user_id
            ) r ON r.user_id = u.user_id
            LEFT JOIN user_blocks b ON b.user_id = u.user_id
            LEFT JOIN user_contact_settings c ON c.user_id = u.user_id
            LEFT JOIN verified_reporters vr ON vr.id = (
                SELECT MAX(vr2.id) FROM verified_reporters vr2
                WHERE vr2.user_id = u.user_id
            )
            {where}
            ORDER BY u.last_seen_at DESC
            LIMIT ? OFFSET ?
        """
        with self._lock:
            count = self._conn.execute(
                f"SELECT COUNT(*) FROM bot_users u{where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                base, [*params, size, page * size]
            ).fetchall()
            return [_row_to_dict(r) for r in rows], int(count)

    async def list_users(self, q: Optional[str] = None, page: int = 0,
                         size: int = 20):
        return await asyncio.to_thread(self._list_users, q, page, size)

    def _list_blocked_users(
        self, page: int = 0, size: int = 50, user_id: int | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """قائمة الحجب الساري، بما يشمل المعرفات التي لا تملك بلاغات."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        size = max(1, min(int(size), 100))
        page = max(0, int(page))
        active = "b.frozen_until IS NOT NULL AND b.frozen_until > ?"
        params: list[Any] = [now_iso]
        if user_id is not None:
            active += " AND b.user_id = ?"
            params.append(int(user_id))
        query = f"""
            SELECT b.user_id AS user_id,
                   COALESCE(u.full_name, r.full_name) AS full_name,
                   COALESCE(u.username, r.username) AS username,
                   r.id AS report_id,
                   b.frozen_until AS frozen_until,
                   b.reason AS reason,
                   b.strikes AS strikes,
                   COALESCE(c.enabled, 1) AS contact_enabled,
                   b.updated_at AS updated_at,
                   u.last_seen_at AS last_at,
                   CASE WHEN e.user_id IS NOT NULL THEN 1 ELSE 0 END
                       AS has_evidence_photo
            FROM user_blocks b
            LEFT JOIN bot_users u ON u.user_id = b.user_id
            LEFT JOIN reports r ON r.id = (
                SELECT MAX(r2.id) FROM reports r2 WHERE r2.user_id = b.user_id
            )
            LEFT JOIN user_contact_settings c ON c.user_id = b.user_id
            LEFT JOIN blocked_user_evidence e ON e.user_id = b.user_id
            WHERE {active}
            ORDER BY b.frozen_until DESC, b.user_id
            LIMIT ? OFFSET ?
        """
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM user_blocks b WHERE {active}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                query, (*params, size, page * size)
            ).fetchall()
            return [_row_to_dict(row) for row in rows], int(total)

    async def list_blocked_users(
        self, page: int = 0, size: int = 50, user_id: int | None = None
    ):
        return await asyncio.to_thread(
            self._list_blocked_users, page, size, user_id
        )

    def _get_block_evidence(self, user_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT photo_path, photo_file_id
                FROM blocked_user_evidence
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def get_block_evidence(self, user_id: int) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self._get_block_evidence, user_id)

    def _blocked_users_count(self) -> int:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM user_blocks "
                "WHERE frozen_until IS NOT NULL AND frozen_until > ?",
                (now_iso,),
            ).fetchone()
            return int(row[0])

    async def blocked_users_count(self) -> int:
        return await asyncio.to_thread(self._blocked_users_count)

    def _user_profile(self, user_id: int) -> Optional[dict[str, Any]]:
        """بطاقة مبلّغ: أحدث بياناته، عدّادات بلاغاته، وحالة الحجب."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reports WHERE user_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            stats = self._conn.execute(
                "SELECT COUNT(*) AS total_reports,"
                " SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS approved_n,"
                " SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS rejected_n,"
                " MIN(created_at) AS first_at"
                " FROM reports WHERE user_id = ?",
                (STATUS_APPROVED, STATUS_REJECTED, user_id),
            ).fetchone()
            block = self._conn.execute(
                "SELECT * FROM user_blocks WHERE user_id = ?", (user_id,)
            ).fetchone()
            user = self._conn.execute(
                "SELECT * FROM bot_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            contact = self._conn.execute(
                "SELECT enabled FROM user_contact_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            phone = self._conn.execute(
                "SELECT contact_phone, seq FROM reports"
                " WHERE user_id = ? AND contact_phone IS NOT NULL"
                " AND TRIM(contact_phone) != ''"
                " ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        if row is None:
            if user is None and block is None:
                return None
            profile = _row_to_dict(user) if user is not None else {"user_id": user_id}
            profile.update({
                "total_reports": 0,
                "approved_n": 0,
                "rejected_n": 0,
                "first_at": profile.get("first_seen_at"),
                "last_at": profile.get("last_seen_at"),
                "block": _row_to_dict(block) if block is not None else None,
                "contact_phone": phone["contact_phone"] if phone else None,
                "contact_phone_seq": phone["seq"] if phone else None,
                "contact_enabled": bool(contact["enabled"]) if contact else True,
            })
            return profile
        profile = _row_to_dict(row)
        profile.pop("description", None)
        profile.pop("photo_path", None)
        profile.pop("post_link", None)
        profile.update({
            "total_reports": int(stats["total_reports"] or 0),
            "approved_n": int(stats["approved_n"] or 0),
            "rejected_n": int(stats["rejected_n"] or 0),
            "first_at": stats["first_at"],
            "block": _row_to_dict(block) if block is not None else None,
            "contact_phone": phone["contact_phone"] if phone else None,
            "contact_phone_seq": phone["seq"] if phone else None,
            "contact_enabled": bool(contact["enabled"]) if contact else True,
        })
        return profile

    async def user_profile(self, user_id: int):
        return await asyncio.to_thread(self._user_profile, user_id)

    def _user_dossier(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            reports = self._conn.execute(
                "SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                (user_id,),
            ).fetchall()
            profile = self._user_profile(user_id)
            if profile is None:
                return None
            report_rows = [_row_to_dict(row) for row in reports]
            targets = [str(user_id)]
            targets.extend(
                str(report.get("seq") or report["id"])
                for report in report_rows
            )
            placeholders = ",".join("?" for _ in targets)
            audit_rows = self._conn.execute(
                f"SELECT id, ts, actor_id, action, target, details FROM audit_log "
                f"WHERE target IN ({placeholders}) ORDER BY id DESC LIMIT 500",
                targets,
            ).fetchall()
            audit_total = self._conn.execute(
                f"SELECT COUNT(*) FROM audit_log WHERE target IN ({placeholders})",
                targets,
            ).fetchone()[0]
        return {
            "profile": profile,
            "reports": report_rows,
            "reports_total": len(report_rows),
            "audit": [_row_to_dict(row) for row in audit_rows],
            "audit_total": int(audit_total or 0),
            "audit_truncated": int(audit_total or 0) > len(audit_rows),
        }

    async def user_dossier(self, user_id: int):
        return await asyncio.to_thread(self._user_dossier, user_id)

    def _set_user_contact_enabled(self, user_id: int, enabled: bool) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT enabled FROM user_contact_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            changed = row is None or bool(row["enabled"]) != enabled
            self._conn.execute(
                """
                INSERT INTO user_contact_settings (user_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (user_id, int(enabled), now_iso),
            )
            self._conn.commit()
            return changed

    async def set_user_contact_enabled(self, user_id: int, enabled: bool) -> bool:
        return await asyncio.to_thread(
            self._set_user_contact_enabled, user_id, enabled
        )

    def _user_contact_enabled(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT enabled FROM user_contact_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return bool(row["enabled"]) if row else True

    async def user_contact_enabled(self, user_id: int) -> bool:
        return await asyncio.to_thread(self._user_contact_enabled, user_id)

    def _list_osint_sources(
        self, report_id: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            if report_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM osint_sources "
                    "ORDER BY accessed_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM osint_sources WHERE report_id = ? "
                    "ORDER BY accessed_at DESC, id DESC LIMIT ?",
                    (int(report_id), limit),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_osint_sources(
        self, report_id: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_osint_sources, report_id, limit)

    def _add_osint_source(
        self,
        report_id: int | None,
        url: str,
        title: str,
        notes: str,
        source_level: str,
        admin_id: int,
    ) -> dict[str, Any]:
        accessed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osint_sources
                    (report_id, url, title, notes, source_level, accessed_at, admin_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id, url, title, notes, source_level, accessed_at, admin_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM osint_sources WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            self._conn.commit()
            return _row_to_dict(row)

    async def add_osint_source(
        self,
        report_id: int | None,
        url: str,
        title: str,
        notes: str,
        source_level: str,
        admin_id: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._add_osint_source,
            report_id,
            url,
            title,
            notes,
            source_level,
            admin_id,
        )

    def _delete_osint_source(self, source_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM osint_sources WHERE id = ?", (source_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def delete_osint_source(self, source_id: int) -> bool:
        return await asyncio.to_thread(self._delete_osint_source, source_id)

    def _open_user_conversation(self, user_id: int) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_conversations (user_id, opened_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (user_id, now_iso, now_iso),
            )
            self._conn.commit()

    async def open_user_conversation(self, user_id: int) -> None:
        await asyncio.to_thread(self._open_user_conversation, user_id)

    def _user_conversation_open(self, user_id: int) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM user_conversations WHERE user_id = ?", (user_id,)
            ).fetchone() is not None

    async def user_conversation_open(self, user_id: int) -> bool:
        return await asyncio.to_thread(self._user_conversation_open, user_id)

    def _list_user_conversations(
        self, q: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        query = (q or "").strip()
        where = ""
        params: list[Any] = []
        if query:
            needle = f"%{query}%"
            where = (
                "WHERE CAST(c.user_id AS TEXT) LIKE ? "
                "OR COALESCE(u.username, '') LIKE ? "
                "OR COALESCE(u.full_name, '') LIKE ?"
            )
            params.extend((needle, needle, needle))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT c.user_id,
                       COALESCE(u.full_name, r.full_name) AS full_name,
                       COALESCE(u.username, r.username) AS username,
                       c.updated_at,
                       (SELECT m.id FROM user_messages m
                        WHERE m.user_id = c.user_id ORDER BY m.id DESC LIMIT 1)
                           AS last_message_id,
                       (SELECT m.text FROM user_messages m
                        WHERE m.user_id = c.user_id ORDER BY m.id DESC LIMIT 1)
                           AS last_message,
                       (SELECT m.direction FROM user_messages m
                        WHERE m.user_id = c.user_id ORDER BY m.id DESC LIMIT 1)
                           AS last_direction,
                       (SELECT m.created_at FROM user_messages m
                        WHERE m.user_id = c.user_id ORDER BY m.id DESC LIMIT 1)
                           AS last_message_at,
                       (SELECT COUNT(*) FROM user_messages m
                        WHERE m.user_id = c.user_id AND m.direction = 'in'
                          AND m.id > c.last_read_message_id)
                           AS unread_count,
                       COALESCE(s.enabled, 1) AS contact_enabled
                FROM user_conversations c
                LEFT JOIN bot_users u ON u.user_id = c.user_id
                LEFT JOIN reports r ON r.id = (
                    SELECT MAX(r2.id) FROM reports r2
                    WHERE r2.user_id = c.user_id
                )
                LEFT JOIN user_contact_settings s ON s.user_id = c.user_id
                {where}
                ORDER BY COALESCE(last_message_at, c.updated_at) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_user_conversations(
        self, q: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_user_conversations, q, limit)

    def _mark_user_conversation_read(
        self, user_id: int, through_message_id: int | None = None
    ) -> int:
        with self._lock:
            query = (
                "SELECT COALESCE(MAX(id), 0) AS last_id FROM user_messages "
                "WHERE user_id = ? AND direction = 'in'"
            )
            params: tuple[int, ...] = (user_id,)
            if through_message_id is not None:
                query += " AND id <= ?"
                params = (user_id, through_message_id)
            row = self._conn.execute(
                query,
                params,
            ).fetchone()
            latest_visible_inbound = int(row["last_id"] or 0)
            current = self._conn.execute(
                "SELECT COALESCE(last_read_message_id, 0) AS last_id "
                "FROM user_conversations WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            last_id = max(
                int(current["last_id"] or 0) if current else 0,
                latest_visible_inbound,
            )
            self._conn.execute(
                "UPDATE user_conversations SET last_read_message_id = ? "
                "WHERE user_id = ?",
                (last_id, user_id),
            )
            self._conn.commit()
            return last_id

    async def mark_user_conversation_read(
        self, user_id: int, through_message_id: int | None = None
    ) -> int:
        return await asyncio.to_thread(
            self._mark_user_conversation_read, user_id, through_message_id
        )

    def _add_user_message(
        self,
        user_id: int,
        direction: str,
        text: str,
        admin_id: int | None = None,
        delivery_status: str = "delivered",
        error_code: str | None = None,
        media_type: str | None = None,
        telegram_file_id: str | None = None,
        telegram_message_id: int | None = None,
        filename: str | None = None,
    ) -> int:
        if direction not in {"in", "out"}:
            raise ValueError("invalid message direction")
        if delivery_status not in {"delivered", "failed", "received"}:
            raise ValueError("invalid delivery status")
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        notification = None
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO user_messages
                    (user_id, direction, text, admin_id, created_at,
                     delivery_status, error_code, media_type, telegram_file_id,
                     telegram_message_id, filename)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, direction, text, admin_id, now_iso,
                    delivery_status, error_code, media_type, telegram_file_id,
                    telegram_message_id, filename,
                ),
            )
            self._conn.execute(
                "UPDATE user_conversations SET updated_at = ? WHERE user_id = ?",
                (now_iso, user_id),
            )
            message_id = int(cur.lastrowid)
            if direction == "in":
                notification = self._insert_notification_locked(
                    "user_message",
                    {
                        "user_id": user_id,
                        "message_id": message_id,
                        "preview": text[:160],
                    },
                )
            self._conn.commit()
        self._publish_notification(notification)
        return message_id

    async def add_user_message(
        self,
        user_id: int,
        direction: str,
        text: str,
        admin_id: int | None = None,
        delivery_status: str = "delivered",
        error_code: str | None = None,
        media_type: str | None = None,
        telegram_file_id: str | None = None,
        telegram_message_id: int | None = None,
        filename: str | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._add_user_message,
            user_id,
            direction,
            text,
            admin_id,
            delivery_status,
            error_code,
            media_type,
            telegram_file_id,
            telegram_message_id,
            filename,
        )

    def _update_user_message_delivery(
        self,
        message_id: int,
        status: str,
        error_code: str | None = None,
        telegram_message_id: int | None = None,
    ) -> bool:
        if status not in {"delivered", "failed"}:
            raise ValueError("invalid delivery status")
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE user_messages SET delivery_status = ?, error_code = ?, "
                "telegram_message_id = COALESCE(?, telegram_message_id) "
                "WHERE id = ? AND direction = 'out'",
                (status, error_code, telegram_message_id, message_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def update_user_message_delivery(
        self,
        message_id: int,
        status: str,
        error_code: str | None = None,
        telegram_message_id: int | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._update_user_message_delivery,
            message_id,
            status,
            error_code,
            telegram_message_id,
        )

    def _reconcile_retried_message(
        self,
        user_id: int,
        failed_message_id: int,
        telegram_message_id: int | None,
    ) -> bool:
        with self._lock:
            tracked = self._conn.execute(
                "SELECT id, text, media_type, telegram_file_id, filename "
                "FROM user_messages WHERE user_id = ? AND telegram_message_id = ? "
                "AND direction = 'out' AND id != ? ORDER BY id DESC LIMIT 1",
                (user_id, telegram_message_id, failed_message_id),
            ).fetchone() if telegram_message_id is not None else None
            if tracked:
                self._conn.execute(
                    "UPDATE user_messages SET text = ?, media_type = ?, "
                    "telegram_file_id = ?, filename = ?, delivery_status = 'delivered', "
                    "error_code = NULL, telegram_message_id = ? WHERE id = ?",
                    (
                        tracked["text"],
                        tracked["media_type"],
                        tracked["telegram_file_id"],
                        tracked["filename"],
                        telegram_message_id,
                        failed_message_id,
                    ),
                )
                self._conn.execute(
                    "DELETE FROM user_messages WHERE id = ?", (tracked["id"],)
                )
            else:
                self._conn.execute(
                    "UPDATE user_messages SET delivery_status = 'delivered', "
                    "error_code = NULL, telegram_message_id = COALESCE(?, "
                    "telegram_message_id) WHERE id = ? AND user_id = ?",
                    (telegram_message_id, failed_message_id, user_id),
                )
            self._conn.commit()
            return True

    async def reconcile_retried_message(
        self,
        user_id: int,
        failed_message_id: int,
        telegram_message_id: int | None,
    ) -> bool:
        return await asyncio.to_thread(
            self._reconcile_retried_message,
            user_id,
            failed_message_id,
            telegram_message_id,
        )

    def _list_user_messages(
        self, user_id: int, after_id: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, user_id, direction, text, admin_id, created_at,
                       delivery_status, error_code, media_type, telegram_file_id,
                       telegram_message_id, filename, is_edited, is_deleted
                FROM user_messages
                WHERE user_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (user_id, max(0, int(after_id)), limit),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_user_messages(
        self, user_id: int, after_id: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_user_messages, user_id, after_id, limit
        )

    def _get_outgoing_user_message(
        self, user_id: int, message_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_messages "
                "WHERE user_id = ? AND id = ? AND direction = 'out'",
                (user_id, message_id),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def get_outgoing_user_message(
        self, user_id: int, message_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._get_outgoing_user_message, user_id, message_id
        )

    def _get_user_message_by_telegram_id(
        self, user_id: int, telegram_message_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_messages WHERE user_id = ? "
                "AND telegram_message_id = ? AND direction = 'out' "
                "ORDER BY id DESC LIMIT 1",
                (user_id, telegram_message_id),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def get_user_message_by_telegram_id(
        self, user_id: int, telegram_message_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._get_user_message_by_telegram_id, user_id, telegram_message_id
        )

    def _mark_user_message_changed(
        self, user_id: int, message_id: int, *, deleted: bool
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE user_messages SET is_edited = ?, is_deleted = ? "
                "WHERE user_id = ? AND id = ? AND direction = 'out'",
                (int(not deleted), int(deleted), user_id, message_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def mark_user_message_changed(
        self, user_id: int, message_id: int, *, deleted: bool
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_user_message_changed, user_id, message_id, deleted=deleted
        )

    def _edit_user_message_content(
        self, user_id: int, message_id: int, text: str
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE user_messages SET text = ?, is_edited = 1 "
                "WHERE user_id = ? AND id = ? AND direction = 'out' "
                "AND is_deleted = 0",
                (text, user_id, message_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def edit_user_message_content(
        self, user_id: int, message_id: int, text: str
    ) -> bool:
        return await asyncio.to_thread(
            self._edit_user_message_content, user_id, message_id, text
        )

    def _update_message_by_telegram_id(
        self,
        user_id: int,
        telegram_message_id: int,
        text: str | None,
        *,
        deleted: bool,
    ) -> bool:
        with self._lock:
            if deleted:
                cursor = self._conn.execute(
                    "UPDATE user_messages SET is_deleted = 1 "
                    "WHERE user_id = ? AND telegram_message_id = ? "
                    "AND direction = 'out'",
                    (user_id, telegram_message_id),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE user_messages SET text = ?, is_edited = 1 "
                    "WHERE user_id = ? AND telegram_message_id = ? "
                    "AND direction = 'out' AND is_deleted = 0",
                    (text or "", user_id, telegram_message_id),
                )
            self._conn.commit()
            return cursor.rowcount > 0

    async def update_message_by_telegram_id(
        self,
        user_id: int,
        telegram_message_id: int,
        text: str | None,
        *,
        deleted: bool,
    ) -> bool:
        return await asyncio.to_thread(
            self._update_message_by_telegram_id,
            user_id,
            telegram_message_id,
            text,
            deleted=deleted,
        )

    # --------------------------------------------------------------
    # التغطية الشاملة: تصنيفات قابلة للتعديل، قوالب ردود، إحصاءات، تعميم
    # --------------------------------------------------------------
    async def effective_vtypes(self) -> list[str]:
        """أنواع المخالفات الفعلية: التعديل المحفوظ أو الإعداد الافتراضي إن لم يوجد."""
        raw = await self.get_state("violation_types_override")
        try:
            parsed = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001 — قيمة تالفة تعامل كغير موجودة
            parsed = []
        clean = [str(t).strip() for t in parsed if str(t).strip()]
        if clean:
            return clean
        from app import config  # noqa: PLC0415 — داخل الدالة لتفادي الدوران

        return list(config.VIOLATION_TYPES)

    async def set_vtypes_override(self, vtypes: list[str]) -> None:
        await self.set_state(
            "violation_types_override",
            json.dumps(list(vtypes), ensure_ascii=False),
        )

    async def get_templates(self) -> list[str]:
        """قوالب الردود الجاهزة (تُخزن كحالة نظام JSON)."""
        raw = await self.get_state("reply_templates")
        try:
            parsed = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001
            parsed = []
        return [str(x) for x in parsed if str(x).strip()]

    async def set_templates(self, items: list[str]) -> None:
        await self.set_state(
            "reply_templates", json.dumps(list(items), ensure_ascii=False)
        )

    def _count_daily(self, since_date: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n"
                " FROM reports WHERE substr(created_at, 1, 10) >= ?"
                " GROUP BY day ORDER BY day",
                (since_date,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    async def count_daily(self, since_date: str):
        return await asyncio.to_thread(self._count_daily, since_date)

    def _count_by_type(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT violation_type AS vtype, COUNT(*) AS n FROM reports"
                " WHERE violation_type IS NOT NULL AND violation_type != ''"
                " GROUP BY vtype ORDER BY n DESC"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    async def count_by_type(self):
        return await asyncio.to_thread(self._count_by_type)

    def _top_users(self, limit: int = 7) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, MAX(full_name) AS full_name, COUNT(*) AS n"
                " FROM reports GROUP BY user_id ORDER BY n DESC LIMIT ?",
                (max(1, min(int(limit), 20)),),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    async def top_users(self, limit: int = 7):
        return await asyncio.to_thread(self._top_users, limit)

    def _all_user_ids(self, cap: int | None = None) -> tuple[list[int], bool]:
        """All persistent subscribers, including users who never filed a report."""
        limit_sql = " LIMIT ?" if cap is not None else ""
        params = (int(cap) + 1,) if cap is not None else ()
        rows = self._conn.execute(
            "SELECT user_id FROM ("
            "SELECT user_id FROM bot_users UNION SELECT user_id FROM reports"
            ") ORDER BY user_id" + limit_sql,
            params,
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        if cap is None:
            return ids, False
        return ids[:cap], len(ids) > cap

    async def all_user_ids(self, cap: int | None = None):
        return await asyncio.to_thread(self._all_user_ids, cap)

    def _create_broadcast_job(
        self, text: str, actor_id: int, total: int, media_type: str = ""
    ) -> int | None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            active = self._conn.execute(
                "SELECT 1 FROM broadcast_jobs "
                "WHERE status IN ('queued', 'running', 'cancel_requested') LIMIT 1"
            ).fetchone()
            if active:
                return None
            cur = self._conn.execute(
                """
                INSERT INTO broadcast_jobs
                    (text, actor_id, status, total, created_at, media_type)
                VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (text, actor_id, total, now_iso, media_type),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    async def create_broadcast_job(
        self, text: str, actor_id: int, total: int, media_type: str = ""
    ) -> int | None:
        return await asyncio.to_thread(
            self._create_broadcast_job, text, actor_id, total, media_type
        )

    def _record_broadcast_delivery(
        self, job_id: int, user_id: int, message_id: int, sent_at: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO broadcast_deliveries
                    (job_id, user_id, message_id, sent_at, delete_status,
                     delete_error, deleted_at)
                VALUES (?, ?, ?, ?, 'available', NULL, NULL)
                """,
                (job_id, user_id, message_id, sent_at),
            )
            self._conn.commit()

    async def record_broadcast_delivery(
        self, job_id: int, user_id: int, message_id: int, sent_at: str
    ) -> None:
        await asyncio.to_thread(
            self._record_broadcast_delivery, job_id, user_id, message_id, sent_at
        )

    def _expire_broadcast_deliveries(self, cutoff: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE broadcast_deliveries SET delete_status = 'expired', "
                "delete_error = 'message_expired' "
                "WHERE delete_status IN ('available', 'failed') AND sent_at <= ?",
                (cutoff,),
            )
            self._conn.execute(
                "UPDATE broadcast_deliveries SET edit_status = 'expired', "
                "edit_error = 'message_expired' "
                "WHERE edit_status IN ('available', 'failed', 'edited') "
                "AND sent_at <= ?",
                (cutoff,),
            )
            self._conn.commit()

    def _list_broadcast_jobs(
        self, limit: int, cutoff: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._conn.execute(
                "UPDATE broadcast_deliveries SET delete_status = 'expired', "
                "delete_error = 'message_expired' "
                "WHERE delete_status IN ('available', 'failed') AND sent_at <= ?",
                (cutoff,),
            )
            self._conn.execute(
                "UPDATE broadcast_deliveries SET edit_status = 'expired', "
                "edit_error = 'message_expired' "
                "WHERE edit_status IN ('available', 'failed', 'edited') "
                "AND sent_at <= ?",
                (cutoff,),
            )
            self._conn.commit()
            rows = self._conn.execute(
                """
                SELECT j.id, j.status, j.total, j.sent, j.failed,
                       j.created_at, j.completed_at, j.media_type,
                       COALESCE(d.delivered, 0) AS delivered_messages,
                       COALESCE(d.available, 0) AS deletable_messages,
                       COALESCE(d.deleted, 0) AS deleted_messages,
                       COALESCE(d.expired, 0) AS expired_messages,
                       COALESCE(d.delete_failed, 0) AS delete_failed_messages,
                       COALESCE(d.editable, 0) AS editable_messages,
                       x.status AS deletion_status,
                       x.total AS deletion_total,
                       x.deleted AS deletion_deleted,
                       x.failed AS deletion_failed,
                       x.expired AS deletion_expired,
                       x.created_at AS deletion_created_at,
                       x.completed_at AS deletion_completed_at,
                       e.status AS edit_status,
                       e.total AS edit_total,
                       e.edited AS edit_edited,
                       e.failed AS edit_failed,
                       e.expired AS edit_expired,
                       e.created_at AS edit_created_at,
                       e.completed_at AS edit_completed_at
                FROM broadcast_jobs j
                LEFT JOIN (
                    SELECT job_id,
                           COUNT(*) AS delivered,
                           SUM(CASE WHEN delete_status IN ('available', 'failed')
                               THEN 1 ELSE 0 END) AS available,
                           SUM(CASE WHEN delete_status = 'deleted'
                               THEN 1 ELSE 0 END) AS deleted,
                           SUM(CASE WHEN delete_status = 'expired'
                               THEN 1 ELSE 0 END) AS expired,
                           SUM(CASE WHEN delete_status = 'failed'
                               THEN 1 ELSE 0 END) AS delete_failed,
                           SUM(CASE WHEN edit_status IN
                                        ('available', 'failed', 'edited')
                                    AND delete_status != 'deleted'
                               THEN 1 ELSE 0 END) AS editable
                    FROM broadcast_deliveries GROUP BY job_id
                ) d ON d.job_id = j.id
                LEFT JOIN broadcast_delete_jobs x ON x.job_id = j.id
                LEFT JOIN broadcast_edit_jobs e ON e.job_id = j.id
                ORDER BY j.id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_broadcast_jobs(
        self, limit: int = 30, cutoff: str | None = None
    ) -> list[dict[str, Any]]:
        cutoff = cutoff or (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(timespec="seconds")
        return await asyncio.to_thread(self._list_broadcast_jobs, limit, cutoff)

    def _prepare_broadcast_delete(
        self, job_id: int, actor_id: int, cutoff: str
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            job = self._conn.execute(
                "SELECT status FROM broadcast_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                return {"error": "not_found"}
            if job["status"] in {"queued", "running", "cancel_requested"}:
                return {"error": "broadcast_in_progress"}
            current = self._conn.execute(
                "SELECT status FROM broadcast_delete_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if current and current["status"] == "running":
                return {"error": "deletion_in_progress"}
            self._conn.execute(
                "UPDATE broadcast_deliveries SET delete_status = 'expired', "
                "delete_error = 'message_expired' "
                "WHERE job_id = ? AND delete_status IN ('available', 'failed') "
                "AND sent_at <= ?",
                (job_id, cutoff),
            )
            self._conn.execute(
                "UPDATE broadcast_deliveries SET edit_status = 'expired', "
                "edit_error = 'message_expired' "
                "WHERE job_id = ? AND edit_status IN "
                "('available', 'failed', 'edited') "
                "AND sent_at <= ?",
                (job_id, cutoff),
            )
            rows = self._conn.execute(
                "SELECT user_id, message_id FROM broadcast_deliveries "
                "WHERE job_id = ? AND delete_status IN ('available', 'failed') "
                "AND sent_at > ? ORDER BY user_id",
                (job_id, cutoff),
            ).fetchall()
            expired = self._conn.execute(
                "SELECT COUNT(*) FROM broadcast_deliveries "
                "WHERE job_id = ? AND delete_status = 'expired'",
                (job_id,),
            ).fetchone()[0]
            deleted = self._conn.execute(
                "SELECT COUNT(*) FROM broadcast_deliveries "
                "WHERE job_id = ? AND delete_status = 'deleted'",
                (job_id,),
            ).fetchone()[0]
            if not rows:
                self._conn.commit()
                return {
                    "error": None,
                    "deliveries": [],
                    "total": 0,
                    "expired": int(expired),
                    "deleted": int(deleted),
                }
            self._conn.execute(
                """
                INSERT INTO broadcast_delete_jobs
                    (job_id, actor_id, status, total, deleted, failed, expired,
                     created_at, completed_at)
                VALUES (?, ?, 'running', ?, 0, 0, ?, ?, NULL)
                ON CONFLICT(job_id) DO UPDATE SET
                    actor_id = excluded.actor_id,
                    status = 'running',
                    total = excluded.total,
                    deleted = 0,
                    failed = 0,
                    expired = excluded.expired,
                    created_at = excluded.created_at,
                    completed_at = NULL
                """,
                (job_id, actor_id, len(rows), int(expired), now_iso),
            )
            self._conn.commit()
            return {
                "error": None,
                "deliveries": [
                    {"user_id": int(row["user_id"]),
                     "message_id": int(row["message_id"])}
                    for row in rows
                ],
                "total": len(rows),
                "expired": int(expired),
                "deleted": int(deleted),
            }

    async def prepare_broadcast_delete(
        self, job_id: int, actor_id: int, cutoff: str | None = None
    ) -> dict[str, Any]:
        cutoff = cutoff or (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(timespec="seconds")
        return await asyncio.to_thread(
            self._prepare_broadcast_delete, job_id, actor_id, cutoff
        )

    def _prepare_broadcast_edit(
        self, job_id: int, actor_id: int, content: str, cutoff: str
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            job = self._conn.execute(
                "SELECT status FROM broadcast_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                return {"error": "not_found"}
            if job["status"] in {"queued", "running", "cancel_requested"}:
                return {"error": "broadcast_in_progress"}
            current = self._conn.execute(
                "SELECT status FROM broadcast_edit_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if current and current["status"] == "running":
                return {"error": "edit_in_progress"}
            self._conn.execute(
                "UPDATE broadcast_deliveries SET edit_status = 'expired', "
                "edit_error = 'message_expired' "
                "WHERE job_id = ? AND edit_status IN "
                "('available', 'failed', 'edited') "
                "AND sent_at <= ?",
                (job_id, cutoff),
            )
            rows = self._conn.execute(
                "SELECT user_id, message_id FROM broadcast_deliveries "
                "WHERE job_id = ? AND delete_status != 'deleted' "
                "AND edit_status IN ('available', 'failed', 'edited') "
                "AND sent_at > ? "
                "ORDER BY user_id",
                (job_id, cutoff),
            ).fetchall()
            expired = self._conn.execute(
                "SELECT COUNT(*) FROM broadcast_deliveries "
                "WHERE job_id = ? AND edit_status = 'expired'",
                (job_id,),
            ).fetchone()[0]
            if not rows:
                self._conn.commit()
                return {
                    "error": None,
                    "deliveries": [],
                    "total": 0,
                    "expired": int(expired),
                }
            self._conn.execute(
                """
                INSERT INTO broadcast_edit_jobs
                    (job_id, actor_id, status, content, total, edited, failed,
                     expired, created_at, completed_at)
                VALUES (?, ?, 'running', ?, ?, 0, 0, ?, ?, NULL)
                ON CONFLICT(job_id) DO UPDATE SET
                    actor_id = excluded.actor_id,
                    status = 'running',
                    content = excluded.content,
                    total = excluded.total,
                    edited = 0,
                    failed = 0,
                    expired = excluded.expired,
                    created_at = excluded.created_at,
                    completed_at = NULL
                """,
                (job_id, actor_id, content, len(rows), int(expired), now_iso),
            )
            self._conn.commit()
            return {
                "error": None,
                "deliveries": [
                    {"user_id": int(row["user_id"]), "message_id": int(row["message_id"])}
                    for row in rows
                ],
                "total": len(rows),
                "expired": int(expired),
            }

    async def prepare_broadcast_edit(
        self, job_id: int, actor_id: int, content: str, cutoff: str | None = None
    ) -> dict[str, Any]:
        cutoff = cutoff or (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(timespec="seconds")
        return await asyncio.to_thread(
            self._prepare_broadcast_edit, job_id, actor_id, content, cutoff
        )

    def _update_broadcast_delivery_edit(
        self, job_id: int, user_id: int, status: str, error: str | None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE broadcast_deliveries SET edit_status = ?, edit_error = ? "
                "WHERE job_id = ? AND user_id = ?",
                (status, error, job_id, user_id),
            )
            self._conn.commit()

    async def update_broadcast_delivery_edit(
        self, job_id: int, user_id: int, status: str, error: str | None = None
    ) -> None:
        await asyncio.to_thread(
            self._update_broadcast_delivery_edit, job_id, user_id, status, error
        )

    def _update_broadcast_edit_job(
        self,
        job_id: int,
        edited: int,
        failed: int,
        expired: int,
        status: str | None = None,
    ) -> None:
        completed_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if status else None
        )
        with self._lock:
            self._conn.execute(
                "UPDATE broadcast_edit_jobs SET edited = ?, failed = ?, expired = ?, "
                "status = COALESCE(?, status), "
                "completed_at = COALESCE(?, completed_at) WHERE job_id = ?",
                (edited, failed, expired, status, completed_at, job_id),
            )
            if status == "completed" and failed == 0:
                self._conn.execute(
                    "UPDATE broadcast_jobs SET text = ? WHERE id = ?",
                    (
                        self._conn.execute(
                            "SELECT content FROM broadcast_edit_jobs WHERE job_id = ?",
                            (job_id,),
                        ).fetchone()["content"],
                        job_id,
                    ),
                )
            self._conn.commit()

    async def update_broadcast_edit_job(
        self,
        job_id: int,
        edited: int,
        failed: int,
        expired: int,
        status: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_broadcast_edit_job,
            job_id,
            edited,
            failed,
            expired,
            status,
        )

    def _update_broadcast_delivery_delete(
        self, job_id: int, user_id: int, status: str, error: str | None
    ) -> None:
        deleted_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if status == "deleted" else None
        )
        with self._lock:
            self._conn.execute(
                "UPDATE broadcast_deliveries SET delete_status = ?, "
                "delete_error = ?, deleted_at = ? "
                "WHERE job_id = ? AND user_id = ?",
                (status, error, deleted_at, job_id, user_id),
            )
            self._conn.commit()

    async def update_broadcast_delivery_delete(
        self, job_id: int, user_id: int, status: str, error: str | None = None
    ) -> None:
        await asyncio.to_thread(
            self._update_broadcast_delivery_delete,
            job_id, user_id, status, error,
        )

    def _update_broadcast_delete_job(
        self,
        job_id: int,
        deleted: int,
        failed: int,
        expired: int,
        status: str | None = None,
    ) -> None:
        completed_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if status else None
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE broadcast_delete_jobs
                SET deleted = ?, failed = ?, expired = ?,
                    status = COALESCE(?, status),
                    completed_at = COALESCE(?, completed_at)
                WHERE job_id = ?
                """,
                (deleted, failed, expired, status, completed_at, job_id),
            )
            self._conn.commit()

    async def update_broadcast_delete_job(
        self,
        job_id: int,
        deleted: int,
        failed: int,
        expired: int,
        status: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_broadcast_delete_job,
            job_id, deleted, failed, expired, status,
        )

    def _get_broadcast_delete_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, actor_id, status, total, deleted, failed, expired, "
                "created_at, completed_at FROM broadcast_delete_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def get_broadcast_delete_job(
        self, job_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_broadcast_delete_job, job_id)

    def _update_broadcast_job(
        self, job_id: int, sent: int, failed: int, status: str | None = None
    ) -> None:
        completed_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if status in {"completed", "failed", "cancelled"}
            else None
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE broadcast_jobs
                SET sent = ?, failed = ?,
                    status = COALESCE(?, status),
                    completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (sent, failed, status, completed_at, job_id),
            )
            self._conn.commit()

    async def update_broadcast_job(
        self, job_id: int, sent: int, failed: int, status: str | None = None
    ) -> None:
        await asyncio.to_thread(
            self._update_broadcast_job, job_id, sent, failed, status
        )

    def _finish_broadcast_job(
        self, job_id: int, sent: int, failed: int
    ) -> str | None:
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM broadcast_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            status = (
                "cancelled"
                if row["status"] == "cancel_requested"
                else "completed"
            )
            self._conn.execute(
                """
                UPDATE broadcast_jobs
                SET sent = ?, failed = ?, status = ?, completed_at = ?
                WHERE id = ?
                """,
                (sent, failed, status, completed_at, job_id),
            )
            self._conn.commit()
            return status

    async def finish_broadcast_job(
        self, job_id: int, sent: int, failed: int
    ) -> str | None:
        return await asyncio.to_thread(
            self._finish_broadcast_job, job_id, sent, failed
        )

    def _request_broadcast_cancel(self, job_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE broadcast_jobs SET status = 'cancel_requested' "
                "WHERE id = ? AND status IN ('queued', 'running')",
                (job_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def request_broadcast_cancel(self, job_id: int) -> bool:
        return await asyncio.to_thread(self._request_broadcast_cancel, job_id)

    def _get_broadcast_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=48)
            ).isoformat(timespec="seconds")
            self._conn.execute(
                "UPDATE broadcast_deliveries SET delete_status = 'expired', "
                "delete_error = 'message_expired' "
                "WHERE job_id = ? AND delete_status IN ('available', 'failed') "
                "AND sent_at <= ?",
                (job_id, cutoff),
            )
            self._conn.execute(
                "UPDATE broadcast_deliveries SET edit_status = 'expired', "
                "edit_error = 'message_expired' "
                "WHERE job_id = ? AND edit_status IN "
                "('available', 'failed', 'edited') AND sent_at <= ?",
                (job_id, cutoff),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id, status, total, sent, failed, created_at, completed_at, "
                "media_type "
                "FROM broadcast_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            job = _row_to_dict(row)
            delivery = self._conn.execute(
                """
                SELECT COUNT(*) AS delivered_messages,
                       SUM(CASE WHEN delete_status IN ('available', 'failed')
                           THEN 1 ELSE 0 END) AS deletable_messages,
                       SUM(CASE WHEN delete_status = 'deleted'
                           THEN 1 ELSE 0 END) AS deleted_messages,
                       SUM(CASE WHEN delete_status = 'expired'
                           THEN 1 ELSE 0 END) AS expired_messages,
                       SUM(CASE WHEN delete_status = 'failed'
                           THEN 1 ELSE 0 END) AS delete_failed_messages
                FROM broadcast_deliveries WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            deletion = self._conn.execute(
                "SELECT status, total, deleted, failed, expired, created_at, "
                "completed_at FROM broadcast_delete_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            job.update(_row_to_dict(delivery) or {})
            job.update({
                "delivered_messages": int(job.get("delivered_messages") or 0),
                "deletable_messages": int(job.get("deletable_messages") or 0),
                "deleted_messages": int(job.get("deleted_messages") or 0),
                "expired_messages": int(job.get("expired_messages") or 0),
                "delete_failed_messages": int(
                    job.get("delete_failed_messages") or 0
                ),
            })
            if deletion:
                deletion_values = _row_to_dict(deletion) or {}
                job.update({
                    f"deletion_{key}": value
                    for key, value in deletion_values.items()
                })
            edit = self._conn.execute(
                "SELECT status, content, total, edited, failed, expired, "
                "created_at, completed_at FROM broadcast_edit_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if edit:
                edit_values = _row_to_dict(edit) or {}
                job.update({
                    f"edit_{key}": value for key, value in edit_values.items()
                })
            return job

    async def get_broadcast_job(self, job_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_broadcast_job, job_id)

    @property
    def path(self) -> Path:
        return self._path

    # --------------------------------------------------------------
    # إغلاق الاتصال عند إيقاف البوت
    # --------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass

    # --------------------------------------------------------------
    # إدارة النصوص القابلة للتعديل (bot_texts)
    # --------------------------------------------------------------
    def _create_bot_texts_table(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_texts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    category TEXT DEFAULT 'user_facing',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.commit()

    def get_bot_text_sync(self, key: str) -> str | None:
        self._create_bot_texts_table()
        with self._lock:
            row = self._conn.execute("SELECT content FROM bot_texts WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def get_all_bot_texts_sync(self) -> list[dict]:
        self._create_bot_texts_table()
        with self._lock:
            rows = self._conn.execute("SELECT id, key, label, content, category, updated_at FROM bot_texts ORDER BY id").fetchall()
            return [{"id": r[0], "key": r[1], "label": r[2], "content": r[3], "category": r[4], "updated_at": r[5]} for r in rows]

    async def get_bot_text(self, key: str) -> str | None:
        return await asyncio.to_thread(self.get_bot_text_sync, key)

    async def get_all_bot_texts(self) -> list[dict]:
        return await asyncio.to_thread(self.get_all_bot_texts_sync)

    async def update_bot_text(self, key: str, content: str, category: str = "user_facing") -> bool:
        return await asyncio.to_thread(self.update_bot_text_sync, key, content, category)

    def update_bot_text_sync(self, key: str, content: str, category: str = "user_facing") -> bool:
        self._create_bot_texts_table()
        with self._lock:
            cursor = self._conn.execute("UPDATE bot_texts SET content = ?, category = ? WHERE key = ?", (content, category, key))
            self._conn.commit()
            if cursor.rowcount == 0:
                self._conn.execute("INSERT OR IGNORE INTO bot_texts (key, label, content, category, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", (key, key, content, category))
                self._conn.commit()
            return True
