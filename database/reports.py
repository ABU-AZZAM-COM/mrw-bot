"""أساليب reports (mixins لطبقة البيانات).

نُقلت من ``database/__init__.py`` كما هي.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from database._shared import (
    STATUS_APPROVED,
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_CODES,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
    _IDENTITY_PRIVATE_TABLES,
    _mapping_row_factory,
    _row_to_dict,
    logger,
)


class ReportsMixin(object):
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

