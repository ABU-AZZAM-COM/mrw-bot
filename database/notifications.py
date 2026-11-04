"""أساليب notifications (mixins لطبقة البيانات).

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


class NotificationsMixin(object):
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

