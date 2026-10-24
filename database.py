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
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class Database:
    """غلاف آمن حول قاعدة البيانات SQLite."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # إعدادات تعطي أداءً أفضل وموثوقية أكبر للكتابة على القرص
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._migrate()

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
        needs_review: int = 0,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO reports
                    (user_id, username, full_name, photo_path, photo_file_id,
                     post_link, description, status, created_at,
                     violation_type, place, latitude, longitude, source, platform,
                     needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, full_name, photo_path, photo_file_id,
                 post_link, description, STATUS_NEW, now_iso,
                 violation_type, place, latitude, longitude, source, platform,
                 needs_review),
            )
            rid = cur.lastrowid
            # توليد الرقم التسلسلي الفريد MRV-YYYY-NNNN اعتمادًا على id الصف
            seq = f"MRV-{datetime.now(timezone.utc).year}-{rid:04d}"
            self._conn.execute("UPDATE reports SET seq = ? WHERE id = ?", (seq, rid))
            self._conn.commit()
            return self._get_by_id(rid) or {}

    async def add_report(self, **kwargs) -> dict[str, Any]:
        return await asyncio.to_thread(self._add_report, **kwargs)

    # --------------------------------------------------------------
    # قراءة البيانات
    # --------------------------------------------------------------
    def _get_by_id(self, rid: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
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

    def _count_since(self, iso: str, user_id: Optional[int] = None) -> int:
        with self._lock:
            if user_id is None:
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM reports WHERE created_at >= ?", (iso,)
                )
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM reports WHERE created_at >= ? AND user_id = ?",
                    (iso, user_id),
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

    def _count_events(self, since_iso: str, user_id: Optional[int] = None,
                      kind: Optional[str] = None) -> int:
        with self._lock:
            query = "SELECT COUNT(*) AS c FROM user_events WHERE ts >= ?"
            params: list[Any] = [since_iso]
            if user_id is not None:
                query += " AND user_id = ?"
                params.append(user_id)
            if kind:
                query += " AND kind = ?"
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

    def _save_block(self, user_id: int, strikes: int, frozen_until: Optional[str],
                    reason: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
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
            self._conn.commit()

    async def save_block(self, user_id: int, strikes: int, frozen_until: Optional[str],
                         reason: str) -> None:
        await asyncio.to_thread(self._save_block, user_id, strikes, frozen_until, reason)

    def _clear_block(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE user_blocks SET strikes = 0, frozen_until = NULL, reason = NULL,"
                " updated_at = ? WHERE user_id = ?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id),
            )
            self._conn.commit()

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

    def _get_state(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    async def get_state(self, key: str) -> Optional[str]:
        return await asyncio.to_thread(self._get_state, key)

    def _set_state(self, key: str, value: Optional[str]) -> None:
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM system_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO system_state (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            self._conn.commit()

    async def set_state(self, key: str, value: Optional[str]) -> None:
        await asyncio.to_thread(self._set_state, key, value)

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
                f"SELECT * FROM reports{where}"
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
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reports SET priority = ?, assigned_to = ?, due_at = ? WHERE id = ?",
                (priority, assigned_to, due_at, rid),
            )
            self._conn.commit()
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
            cur = self._conn.execute("DELETE FROM reports WHERE id = ?", (rid,))
            if cur.rowcount > 0:
                self._conn.execute(
                    "DELETE FROM report_fingerprints WHERE report_id = ?", (rid,)
                )
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_report(self, rid: int) -> bool:
        return await asyncio.to_thread(self._delete_report, rid)

    def _list_users(
        self, q: Optional[str] = None, page: int = 0, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """قائمة مبلّغين مجملة: آخر اسم/معرف، عدد البلاغات، آخر ظهور + حالة الحجب."""
        like = f"%{(q or '').strip()}%"
        having = ""
        if q and q.strip():
            having = (
                " HAVING MAX(r.full_name) LIKE ? OR MAX(r.username) LIKE ?"
                " OR CAST(r.user_id AS TEXT) LIKE ?"
            )
        params = [like, like, like] if having else []
        base = """
            SELECT r.user_id AS user_id,
                   MAX(r.full_name) AS full_name,
                   MAX(r.username)  AS username,
                   COUNT(*)         AS reports_count,
                   MAX(r.created_at) AS last_at,
                   b.frozen_until   AS frozen_until,
                   b.reason         AS block_reason
            FROM reports r
            LEFT JOIN user_blocks b ON b.user_id = r.user_id
            GROUP BY r.user_id{having}
            ORDER BY last_at DESC
            LIMIT ? OFFSET ?
        """.format(having=having)
        total = """
            SELECT COUNT(*) FROM (
                SELECT r.user_id AS user_id, MAX(r.full_name) AS u,
                       MAX(r.username) AS n
                FROM reports r
                GROUP BY r.user_id{having}
            )
        """.format(having=having)
        size = max(1, min(int(size), 100))
        page = max(0, int(page))
        with self._lock:
            count = self._conn.execute(total, params).fetchone()[0]
            rows = self._conn.execute(
                base, [*params, size, page * size]
            ).fetchall()
            return [_row_to_dict(r) for r in rows], int(count)

    async def list_users(self, q: Optional[str] = None, page: int = 0,
                         size: int = 20):
        return await asyncio.to_thread(self._list_users, q, page, size)

    def _list_blocked_users(
        self, page: int = 0, size: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        """قائمة الحجب الساري، بما يشمل المعرفات التي لا تملك بلاغات."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        size = max(1, min(int(size), 100))
        page = max(0, int(page))
        active = "b.frozen_until IS NOT NULL AND b.frozen_until > ?"
        query = f"""
            SELECT b.user_id AS user_id,
                   r.full_name AS full_name,
                   r.username AS username,
                   b.frozen_until AS frozen_until,
                   b.reason AS reason,
                   b.strikes AS strikes,
                   b.updated_at AS updated_at
            FROM user_blocks b
            LEFT JOIN reports r ON r.id = (
                SELECT MAX(r2.id) FROM reports r2 WHERE r2.user_id = b.user_id
            )
            WHERE {active}
            ORDER BY b.frozen_until DESC, b.user_id
            LIMIT ? OFFSET ?
        """
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM user_blocks b WHERE {active}",
                (now_iso,),
            ).fetchone()[0]
            rows = self._conn.execute(
                query, (now_iso, size, page * size)
            ).fetchall()
            return [_row_to_dict(row) for row in rows], int(total)

    async def list_blocked_users(self, page: int = 0, size: int = 50):
        return await asyncio.to_thread(self._list_blocked_users, page, size)

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
        if row is None:
            return _row_to_dict(block) if block is not None else None
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
        })
        return profile

    async def user_profile(self, user_id: int):
        return await asyncio.to_thread(self._user_profile, user_id)

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
        rows = self._conn.execute(
            "SELECT violation_type AS vtype, COUNT(*) AS n FROM reports"
            " WHERE violation_type IS NOT NULL AND violation_type != ''"
            " GROUP BY vtype ORDER BY n DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    async def count_by_type(self):
        return await asyncio.to_thread(self._count_by_type)

    def _top_users(self, limit: int = 7) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT user_id, MAX(full_name) AS full_name, COUNT(*) AS n"
            " FROM reports GROUP BY user_id ORDER BY n DESC LIMIT ?",
            (max(1, min(int(limit), 20)),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    async def top_users(self, limit: int = 7):
        return await asyncio.to_thread(self._top_users, limit)

    def _all_user_ids(self, cap: int = 300) -> tuple[list[int], bool]:
        """جميع معرافات المبلّغين (من بلغ يوماً) — يمزج cap+1 لكشف الفيض."""
        rows = self._conn.execute(
            "SELECT DISTINCT user_id FROM reports ORDER BY user_id LIMIT ?",
            (int(cap) + 1,),
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        return ids[:cap], len(ids) > cap

    async def all_user_ids(self, cap: int = 300):
        return await asyncio.to_thread(self._all_user_ids, cap)

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
