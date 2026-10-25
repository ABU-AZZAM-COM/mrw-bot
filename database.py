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
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# حالات البلاغ (تُخزّن بالعربية في القاعدة لسهولة قراءتها في التقارير)
# ------------------------------------------------------------------
STATUS_NEW = "جديد"
STATUS_REVIEW = "قيد المراجعة"
STATUS_APPROVED = "معتمد"
STATUS_REJECTED = "مرفوض"

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
    followup_note TEXT,
    followup_response TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id);

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

CREATE TABLE IF NOT EXISTS user_limit_resets (
    user_id  INTEGER PRIMARY KEY,
    reset_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_conversations (
    user_id    INTEGER PRIMARY KEY,
    opened_at  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    direction  TEXT NOT NULL CHECK(direction IN ('in', 'out')),
    text       TEXT NOT NULL,
    admin_id   INTEGER,
    created_at TEXT NOT NULL
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
            "WHERE status IN ('queued', 'running')",
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
                "UPDATE reports SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
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
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (ts, actor_id, action, target, details)"
                " VALUES (?, ?, ?, ?, ?)",
                (now_iso, actor_id, action, target, details),
            )
            self._conn.commit()

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

    def _set_followup_request(self, rid: int, note: Optional[str]) -> bool:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET followup_requested_at = ?, followup_note = ?, "
                "followup_response = NULL WHERE id = ?",
                (now, note, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def set_followup_request(self, rid: int, note: Optional[str]) -> bool:
        return await asyncio.to_thread(self._set_followup_request, rid, note)

    def _clear_followup_request(self, rid: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET followup_requested_at = NULL, followup_note = NULL WHERE id = ?",
                (rid,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def clear_followup_request(self, rid: int) -> bool:
        return await asyncio.to_thread(self._clear_followup_request, rid)

    def _save_followup_response(self, rid: int, response: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET followup_response = ?, "
                "followup_requested_at = NULL, followup_note = NULL WHERE id = ?",
                (response, rid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def save_followup_response(self, rid: int, response: str) -> bool:
        return await asyncio.to_thread(self._save_followup_response, rid, response)

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
        import config

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
        import config

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
        message_retention_days: int = 180,
    ) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        audit_cutoff = (
            current - timedelta(days=audit_retention_days)
        ).isoformat(timespec="seconds")
        message_cutoff = (
            current - timedelta(days=message_retention_days)
        ).isoformat(timespec="seconds")
        with self._lock:
            audit = self._conn.execute(
                "DELETE FROM audit_log WHERE ts < ?", (audit_cutoff,)
            )
            messages = self._conn.execute(
                "DELETE FROM user_messages WHERE created_at < ?", (message_cutoff,)
            )
            self._conn.commit()
            return {
                "audit_deleted": int(audit.rowcount),
                "messages_deleted": int(messages.rowcount),
            }

    async def cleanup_private_data(
        self,
        audit_retention_days: int = 365,
        message_retention_days: int = 180,
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._cleanup_private_data,
            None,
            audit_retention_days,
            message_retention_days,
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
                   COALESCE(c.enabled, 1) AS contact_enabled
            FROM bot_users u
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS reports_count
                FROM reports GROUP BY user_id
            ) r ON r.user_id = u.user_id
            LEFT JOIN user_blocks b ON b.user_id = u.user_id
            LEFT JOIN user_contact_settings c ON c.user_id = u.user_id
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

    def _add_user_message(
        self, user_id: int, direction: str, text: str, admin_id: int | None = None
    ) -> int:
        if direction not in {"in", "out"}:
            raise ValueError("invalid message direction")
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        notification = None
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO user_messages (user_id, direction, text, admin_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, direction, text, admin_id, now_iso),
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
        self, user_id: int, direction: str, text: str, admin_id: int | None = None
    ) -> int:
        return await asyncio.to_thread(
            self._add_user_message, user_id, direction, text, admin_id
        )

    def _list_user_messages(
        self, user_id: int, after_id: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, user_id, direction, text, admin_id, created_at
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
        import config  # noqa: PLC0415 — داخل الدالة لتفادي الدوران

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
        self, text: str, actor_id: int, total: int
    ) -> int | None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            active = self._conn.execute(
                "SELECT 1 FROM broadcast_jobs "
                "WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone()
            if active:
                return None
            cur = self._conn.execute(
                """
                INSERT INTO broadcast_jobs (text, actor_id, status, total, created_at)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (text, actor_id, total, now_iso),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    async def create_broadcast_job(
        self, text: str, actor_id: int, total: int
    ) -> int | None:
        return await asyncio.to_thread(
            self._create_broadcast_job, text, actor_id, total
        )

    def _update_broadcast_job(
        self, job_id: int, sent: int, failed: int, status: str | None = None
    ) -> None:
        completed_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if status in {"completed", "failed"}
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

    def _get_broadcast_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status, total, sent, failed, created_at, completed_at "
                "FROM broadcast_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

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
