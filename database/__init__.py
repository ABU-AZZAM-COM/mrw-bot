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

from database._shared import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_CODES,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
    _DatabaseRow,
    _IDENTITY_PRIVATE_TABLES,
    _mapping_row_factory,
    _row_to_dict,
    logger,
)

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

CREATE TABLE IF NOT EXISTS user_message_attachments (
    message_id INTEGER PRIMARY KEY,
    data BLOB NOT NULL,
    content_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    FOREIGN KEY(message_id) REFERENCES user_messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_contact_preferences (
    user_id INTEGER PRIMARY KEY,
    telegram_reply_alerts INTEGER NOT NULL DEFAULT 1,
    publication_alerts INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

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

from database.verification import VerificationMixin  # noqa: E402


from database.broadcast import BroadcastMixin  # noqa: E402


from database.notifications import NotificationsMixin  # noqa: E402


from database.audit import AuditMixin  # noqa: E402


from database.osint import OsintMixin  # noqa: E402


from database.chat import ChatMixin  # noqa: E402


from database.users import UsersMixin  # noqa: E402

class Database(VerificationMixin, BroadcastMixin, NotificationsMixin, AuditMixin, OsintMixin, ChatMixin, UsersMixin):
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
            # Seed missing defaults and repair known legacy or blank seed values.
            self._create_bot_texts_table()
            texts = [
                ("welcome_message","رسالة الترحيب","🛡️ <b>منظومة البلاغات والشكاوى</b>\nإدارة الشرطة العسكرية\n────────────────────\nأهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته من هذه المحادثة.\nستراجع بياناتك قبل الإرسال، ثم يصلك رقم تتبع للمتابعة.\n\nمشاركة رقم الهاتف والموقع اختيارية. أرسل فقط المعلومات والأدلة ذات الصلة.\n\nاختر الخدمة التي تحتاجها:","user_facing"),
                ("verify_consent","نص الموافقة للتوثيق","🪪 <b>التسجيل كمراسل موثوق</b>\n\nالغرض: توثيق حساب المراسل لدى الشرطة العسكرية فرع الحسكة. يتطلب الطلب مشاركة رقم سوري عبر زر تيليجرام، وصورة وجه، وصورتي الهوية الأمامية والخلفية.\n\nيُتحقق محليًا من وضوح الصور، ومن وجود وجه واحد واضح في صورة الوجه. تُرسل صورة الوجه فقط إلى Sightengine لفحص المحتوى غير اللائق؛ ولا تُرسل صور الهوية إلى هذه الخدمة. لا يثبت الفحص أصالة الهوية أو أن الصورة حيّة. إذا اعتُمد الطلب، يُحفظ الملف والصور دون حذف تلقائي، ويمكن تنزيلها ضمن ملف PDF. يقتصر الاطلاع والتنزيل على المشرفين الذين مُنحوا صلاحية الوثائق؛ وتُسجل عمليات الاطلاع والتنزيل. لا تُحذف الصور تلقائيًا بعد القرار. يمكن لمشرف مُنح صلاحية حذف مستقلة حذف الصور ونسخها بسبب مسجل، مع الإبقاء على سجل الطلب.\n\nبالضغط على «متابعة والموافقة» تؤكد اطلاعك وموافقتك على هذه الشروط.","user_facing"),
                ("help_text","دليل الاستخدام","📖 <b>الدليل السريع</b>\n────────────────────\n📝 <b>إرسال بلاغ:</b> اضغط /start ثم اختر القسم المناسب، وأدخل التفاصيل وأرفق الدليل أو الموقع ورقم التواصل اختياريًا عند الحاجة.\n\n📊 <b>متابعة بلاغاتك:</b> استخدم زر «حالة بلاغي» لعرض بطاقة لكل بلاغ وحالته وزمنه.\n\n📩 <b>معلومات إضافية:</b> افتح «حالة بلاغي»، ثم بطاقة البلاغ التي طلبت الإدارة معلومات إضافية عنها واضغط زر الإرسال.\n\n🚫 <b>الإلغاء:</b> استخدم زر (❌ إلغاء) الظاهر في خطوات البلاغ.\n\n🔔 <b>التحديثات:</b> تُرسل إليك فورًا عند أي تغيير في حالة بلاغك.\n\n🔗 <b>الأدلة:</b> أرفق ما يتوفر من روابط أو صور ذات صلة، ويمكن تخطي المرفقات الاختيارية.\n\n🔒 <b>السرية:</b> كل بلاغ يُعرض على المشرف المختص فقط.\n\nللبدء الآن اضغط زر «📝 إرسال بلاغ جديد» أو أرسل /start.","user_facing"),
                ("privacy_policy","سياسة الخصوصية","🔒 <b>سياسة الخصوصية</b>\nتُحفظ البيانات بشكل آمن داخل نظام مخصص، ولا تُشارك مع جهات خارجية إلا بموجب أمر قضائي. تُسجل جميع عمليات الوصول إلى الصور والوثائق. لا تُحفظ بيانات المستخدم غير الضرورية.","user_facing"),
                ("verify_button","زر التوثيق","✅ توثيق","button_label"),
                ("menu_button","زر القائمة","📋 القائمة","button_label"),
                ("cancel_button","زر الإلغاء","❌ إلغاء","button_label"),
                ("confirm_button","زر التأكيد","✅ إرسال البلاغ","button_label"),
                ("contact_admin","تواصل مع الإدارة","📞 تواصل مع الإدارة","button_label"),
                ("about_system","عن النظام","ℹ️ <b>التعليمات واشتراطات تقديم البلاغ</b>\nاختر القسم الأقرب للواقعة، واكتب وصفًا دقيقًا يتضمن ما حدث ومتى وأين والأشخاص أو الجهة ذات الصلة.\n📎 أرفق صورة ذات صلة إن توفرت. في بلاغات النشر اختر المنصة وأرسل الرابط أو تخطّه إذا كان الدليل لقطة شاشة.\n☎️ مشاركة رقم الهاتف اختيارية. لا ترسل بيانات أو أدلة لا تتعلق بالبلاغ.\n🔒 راجع البيانات قبل الإرسال؛ سيظهر لك رقم تتبع ويمكنك متابعة الحالة من زر «حالة بلاغي».","user_facing"),
                ("reporting_paused","إيقاف الاستقبال","🛑 <b>استقبال البلاغات الجديدة متوقف مؤقتًا.</b>\nيمكنك مع ذلك متابعة بلاغاتك أو مراسلة الإدارة من القائمة.","user_facing"),
                ("status_update","تحديث الحالة","🔔 <b>تحديث بلاغك رقم {seq}</b>\n────────────────────\n📌 الحالة الجديدة: {status}\n🕒 وقت التحديث: {time}\n{note}\n{final_line}","system"),
            ]
            legacy_defaults = {
                "welcome_message": "🛡️ منظومة البلاغات والشكاوى\nإدارة الشرطة العسكرية\n────────────────────\nأهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته من هذه المحادثة.\nستراجع بياناتك قبل الإرسال، ثم يصلك رقم تتبع للمتابعة.\n\nمشاركة رقم الهاتف والموقع اختيارية. أرسل فقط المعلومات والأدلة ذات الصلة.\n\nاختر الخدمة التي تحتاجها:",
                "verify_consent": "🪪 التسجيل كمراسل موثوق\n\nالغرض: توثيق حساب المراسل لدى الشرطة العسكرية فرع الحسكة. يتطلب الطلب مشاركة رقم سوري عبر زر تيليجرام، وصورة وجه، وصورتي الهوية الأمامية والخلفية.\n\nيُتحقق محليًا من وضوح الصور، ومن وجود وجه واحد واضح في صورة الوجه. تُرسل صورة الوجه فقط إلى Sightengine لفحص المحتوى غير اللائق؛ ولا تُرسل صور الهوية إلى هذه الخدمة. لا يثبت الفحص أصالة الهوية أو أن الصورة حيّة. إذا اعتُمد الطلب، يُحفظ الملف والصور دون حذف تلقائي، ويمكن تنزيلها ضمن ملف PDF. يقتصر الاطلاع والتنزيل على المشرفين الذين مُنحوا صلاحية الوثائق؛ وتُسجل عمليات الاطلاع والتنزيل. لا تُحذف الصور تلقائيًا بعد القرار. يمكن لمشرف مُنح صلاحية حذف مستقلة حذف الصور ونسخها بسبب مسجل، مع الإبقاء على سجل الطلب.\n\nبالضغط على «متابعة والموافقة» تؤكد اطلاعك وموافقتك على هذه الشروط.",
                "help_text": "📖 الدليل السريع\n────────────────────\n📝 إرسال بلاغ: اضغط /start ثم اختر القسم المناسب، وأدخل التفاصيل وأرفق الدليل أو الموقع ورقم التواصل اختياريًا عند الحاجة.\n\n📊 متابعة بلاغاتك: استخدم زر «حالة بلاغي» لعرض بطاقة لكل بلاغ وحالته وزمنه.\n\n📩 معلومات إضافية: افتح «حالة بلاغي»، ثم بطاقة البلاغ التي طلبت الإدارة معلومات إضافية عنها واضغط زر الإرسال.\n\n🚫 الإلغاء: استخدم زر (❌ إلغاء) الظاهر في خطوات البلاغ.\n\n🔔 التحديثات: تُرسل إليك فورًا عند أي تغيير في حالة بلاغك.\n\n🔗 الأدلة: أرفق ما يتوفر من روابط أو صور ذات صلة، ويمكن تخطي المرفقات الاختيارية.\n\n🔒 السرية: كل بلاغ يُعرض على المشرف المختص فقط.\n\nللبدء الآن اضغط زر «📝 إرسال بلاغ جديد» أو أرسل /start.",
                "privacy_policy": "🔒 سياسة الخصوصية\nتُحفظ البيانات بشكل آمن داخل نظام مخصص، ولا تُشارك مع جهات خارجية إلا بموجب أمر قضائي. تُسجل جميع عمليات الوصول إلى الصور والوثائق. لا تُحفظ بيانات المستخدم غير الضرورية.",
                "about_system": "ℹ️ تعليمات واشتراطات تقديم البلاغ\nاختر القسم الأقرب للواقعة، واكتب وصفًا دقيقًا يتضمن ما حدث ومتى وأين والأشخاص أو الجهة ذات الصلة.\n📎 أرفق صورة ذات صلة إن توفرت. في بلاغات النشر اختر المنصة وأرسل الرابط أو تخطّه إذا كان الدليل لقطة شاشة.\n☎️ مشاركة رقم الهاتف اختيارية. لا ترسل بيانات أو أدلة لا تتعلق بالبلاغ.\n🔒 راجع البيانات قبل الإرسال؛ سيظهر لك رقم تتبع ويمكنك متابعة الحالة من زر «حالة بلاغي».",
            }
            with self._lock:
                for key, label, content, category in texts:
                    row = self._conn.execute(
                        "SELECT content FROM bot_texts WHERE key = ?", (key,)
                    ).fetchone()
                    if row is None:
                        self._conn.execute(
                            "INSERT INTO bot_texts "
                            "(key, label, content, category, updated_at) "
                            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                            (key, label, content, category),
                        )
                    elif not row[0] or row[0] == legacy_defaults.get(key):
                        self._conn.execute(
                            "UPDATE bot_texts SET label = ?, content = ?, "
                            "category = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE key = ?",
                            (label, content, category, key),
                        )
                self._conn.commit()

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
        self, user_id: int, limit: int = 10, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            total = int(self._conn.execute(
                "SELECT COUNT(*) AS c FROM reports WHERE user_id = ?", (user_id,)
            ).fetchone()["c"])
            rows = self._conn.execute(
                "SELECT * FROM reports WHERE user_id = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, max(0, int(offset))),
            ).fetchall()
            return [_row_to_dict(r) for r in rows], total

    async def list_user_reports(
        self, user_id: int, limit: int = 10, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """(بلاغات مبلّغ من الأحدث، إجمالي بلاغاته) — لشاشة «بلاغاتي»."""
        return await asyncio.to_thread(
            self._list_user_reports, user_id, limit, offset
        )

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

    async def delete_osint_source(self, source_id: int) -> bool:
        return await asyncio.to_thread(self._delete_osint_source, source_id)

    async def effective_vtypes(self) -> list[str]:
        """أنواع المخالفات الفعلية: التعديل المحفوظ أو الإعداد الافتراضي إن لم يوجد."""
        from services.report_taxonomy import (
            DEFAULT_VIOLATION_TYPES,
            LEGACY_VIOLATION_TYPES,
        )

        raw = await self.get_state("violation_types_override")
        try:
            parsed = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001 — قيمة تالفة تعامل كغير موجودة
            parsed = []
        clean = [str(t).strip() for t in parsed if str(t).strip()]
        if clean == list(LEGACY_VIOLATION_TYPES):
            return list(DEFAULT_VIOLATION_TYPES)
        if clean:
            return clean
        from app import config  # noqa: PLC0415 — داخل الدالة لتفادي الدوران

        return list(config.VIOLATION_TYPES)

    async def set_vtypes_override(self, vtypes: list[str]) -> None:
        await self.set_state(
            "violation_types_override",
            json.dumps(list(vtypes), ensure_ascii=False),
        )

    async def effective_vtype_groups(self) -> dict[str, list[str]]:
        from services.report_taxonomy import (
            default_vtype_groups,
            normalize_vtype_groups,
        )

        items = await self.effective_vtypes()
        raw = await self.get_state("violation_type_groups")
        try:
            parsed = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            parsed = None
        groups = normalize_vtype_groups(parsed, items)
        assigned = {
            item for values in groups.values() for item in values
        } if groups is not None else set()
        if groups is not None:
            from services.report_taxonomy import LEGACY_VIOLATION_TYPES

            if all(
                groups[category] == list(LEGACY_VIOLATION_TYPES)
                for category in groups
            ):
                return default_vtype_groups(items)
        if (
            groups is None
            or any(not values for values in groups.values())
            or any(item not in assigned for item in items)
        ):
            return default_vtype_groups(items)
        return groups

    async def set_vtype_groups(self, groups: dict[str, list[str]]) -> None:
        await self.set_state(
            "violation_type_groups",
            json.dumps(groups, ensure_ascii=False),
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

    async def ensure_bot_text_defaults(
        self, defaults: list[tuple[str, str, str, str]]
    ) -> None:
        await asyncio.to_thread(self.ensure_bot_text_defaults_sync, defaults)

    def ensure_bot_text_defaults_sync(
        self, defaults: list[tuple[str, str, str, str]]
    ) -> None:
        self._create_bot_texts_table()
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO bot_texts "
                "(key, label, content, category, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                defaults,
            )
            self._conn.commit()

    async def update_bot_text(self, key: str, content: str, category: str = "user_facing") -> bool:
        return await asyncio.to_thread(self.update_bot_text_sync, key, content, category)

    def update_bot_text_sync(self, key: str, content: str, category: str = "user_facing") -> bool:
        self._create_bot_texts_table()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE bot_texts SET content = ?, category = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE key = ?",
                (content, category, key),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                self._conn.execute("INSERT OR IGNORE INTO bot_texts (key, label, content, category, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", (key, key, content, category))
                self._conn.commit()
            return True
