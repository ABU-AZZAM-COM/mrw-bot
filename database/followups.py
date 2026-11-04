"""أساليب followups (mixins لطبقة البيانات).

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


class FollowupsMixin(object):
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

