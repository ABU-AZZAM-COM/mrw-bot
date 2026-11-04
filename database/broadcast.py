"""أساليب البثّ الجماعي والمنشورات (mixins لطبقة البيانات).

نُقلت من ``database/__init__.py`` كما هي.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from database._shared import (
    STATUS_NEW,
    _mapping_row_factory,
    _row_to_dict,
    logger,
)


class BroadcastMixin:
    def _publication_subscriber_ids(
        self, cap: int | None = None
    ) -> tuple[list[int], bool]:
        limit_sql = " LIMIT ?" if cap is not None else ""
        params = (int(cap) + 1,) if cap is not None else ()
        with self._lock:
            rows = self._conn.execute(
                "SELECT u.user_id FROM ("
                "SELECT user_id FROM bot_users UNION SELECT user_id FROM reports"
                ") u LEFT JOIN user_contact_preferences p ON p.user_id = u.user_id "
                "WHERE COALESCE(p.publication_alerts, 1) = 1 "
                "ORDER BY u.user_id" + limit_sql,
                params,
            ).fetchall()
        ids = [int(row[0]) for row in rows]
        if cap is None:
            return ids, False
        return ids[:cap], len(ids) > cap

    async def publication_subscriber_ids(self, cap: int | None = None):
        return await asyncio.to_thread(self._publication_subscriber_ids, cap)

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

