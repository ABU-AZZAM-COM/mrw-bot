"""أساليب maintenance (mixins لطبقة البيانات).

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


class MaintenanceMixin(object):
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

