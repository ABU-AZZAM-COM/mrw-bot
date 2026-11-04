"""أساليب chat (mixins لطبقة البيانات).

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


class ChatMixin(object):
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
        where = """
            WHERE EXISTS (
                SELECT 1 FROM user_messages visible
                WHERE visible.user_id = c.user_id
                  AND (
                      visible.direction = 'in'
                      OR (visible.direction = 'out' AND visible.admin_id IS NOT NULL)
                  )
            )
        """
        params: list[Any] = []
        if query:
            needle = f"%{query}%"
            where += (
                "AND (CAST(c.user_id AS TEXT) LIKE ? "
                "OR COALESCE(u.username, '') LIKE ? "
                "OR COALESCE(u.full_name, '') LIKE ?)"
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
                        WHERE m.user_id = c.user_id
                          AND (m.direction = 'in'
                               OR (m.direction = 'out' AND m.admin_id IS NOT NULL))
                        ORDER BY m.id DESC LIMIT 1)
                           AS last_message_id,
                       (SELECT m.text FROM user_messages m
                        WHERE m.user_id = c.user_id
                          AND (m.direction = 'in'
                               OR (m.direction = 'out' AND m.admin_id IS NOT NULL))
                        ORDER BY m.id DESC LIMIT 1)
                           AS last_message,
                       (SELECT m.direction FROM user_messages m
                        WHERE m.user_id = c.user_id
                          AND (m.direction = 'in'
                               OR (m.direction = 'out' AND m.admin_id IS NOT NULL))
                        ORDER BY m.id DESC LIMIT 1)
                           AS last_direction,
                       (SELECT m.created_at FROM user_messages m
                        WHERE m.user_id = c.user_id
                          AND (m.direction = 'in'
                               OR (m.direction = 'out' AND m.admin_id IS NOT NULL))
                        ORDER BY m.id DESC LIMIT 1)
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
        attachment_data: bytes | None = None,
        attachment_content_type: str | None = None,
    ) -> int:
        if direction not in {"in", "out"}:
            raise ValueError("invalid message direction")
        if delivery_status not in {"delivered", "failed", "received"}:
            raise ValueError("invalid delivery status")
        if attachment_data is not None and (
            not attachment_content_type or not filename
        ):
            raise ValueError("attachment metadata is required")
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
            if attachment_data is not None:
                self._conn.execute(
                    """
                    INSERT INTO user_message_attachments
                        (message_id, data, content_type, filename, size_bytes)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        attachment_data,
                        attachment_content_type,
                        filename,
                        len(attachment_data),
                    ),
                )
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
        attachment_data: bytes | None = None,
        attachment_content_type: str | None = None,
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
            attachment_data,
            attachment_content_type,
        )

    def _get_user_message_attachment(
        self, user_id: int, message_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT a.data, a.content_type, a.filename, a.size_bytes
                FROM user_message_attachments a
                JOIN user_messages m ON m.id = a.message_id
                WHERE m.user_id = ? AND m.id = ?
                """,
                (user_id, message_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "data": bytes(row["data"]),
                "content_type": str(row["content_type"]),
                "filename": str(row["filename"]),
                "size_bytes": int(row["size_bytes"]),
            }

    async def get_user_message_attachment(
        self, user_id: int, message_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._get_user_message_attachment, user_id, message_id
        )

    def _user_contact_preferences(self, user_id: int) -> dict[str, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT telegram_reply_alerts, publication_alerts "
                "FROM user_contact_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return {"telegram_reply_alerts": True, "publication_alerts": True}
            return {
                "telegram_reply_alerts": bool(row["telegram_reply_alerts"]),
                "publication_alerts": bool(row["publication_alerts"]),
            }

    async def user_contact_preferences(self, user_id: int) -> dict[str, bool]:
        return await asyncio.to_thread(self._user_contact_preferences, user_id)

    def _set_user_contact_preferences(
        self,
        user_id: int,
        telegram_reply_alerts: bool,
        publication_alerts: bool,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_contact_preferences
                    (user_id, telegram_reply_alerts, publication_alerts, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    telegram_reply_alerts = excluded.telegram_reply_alerts,
                    publication_alerts = excluded.publication_alerts,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    int(telegram_reply_alerts),
                    int(publication_alerts),
                    now_iso,
                ),
            )
            self._conn.commit()

    async def set_user_contact_preferences(
        self,
        user_id: int,
        telegram_reply_alerts: bool,
        publication_alerts: bool,
    ) -> None:
        await asyncio.to_thread(
            self._set_user_contact_preferences,
            user_id,
            telegram_reply_alerts,
            publication_alerts,
        )

    def _delete_user_conversation(
        self, user_id: int, actor_id: int, reason: str
    ) -> bool:
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM user_conversations WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if exists is None:
                return False
            self._conn.execute(
                """
                DELETE FROM user_message_attachments
                WHERE message_id IN (
                    SELECT id FROM user_messages WHERE user_id = ?
                )
                """,
                (user_id,),
            )
            self._conn.execute("DELETE FROM user_messages WHERE user_id = ?", (user_id,))
            cursor = self._conn.execute(
                "DELETE FROM user_conversations WHERE user_id = ?", (user_id,)
            )
            if cursor.rowcount:
                self._insert_audit_locked(
                    actor_id,
                    "user_conversation_deleted",
                    str(user_id),
                    f"reason={reason}",
                )
            self._conn.commit()
            return cursor.rowcount > 0

    async def delete_user_conversation(
        self, user_id: int, actor_id: int, reason: str
    ) -> bool:
        return await asyncio.to_thread(
            self._delete_user_conversation,
            user_id,
            actor_id,
            reason,
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
        self,
        user_id: int,
        after_id: int = 0,
        limit: int = 100,
        conversation_only: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        conversation_filter = (
            "AND (direction = 'in' "
            "OR (direction = 'out' AND admin_id IS NOT NULL))"
            if conversation_only
            else ""
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, user_id, direction, text, admin_id, created_at,
                       delivery_status, error_code, media_type, telegram_file_id,
                       telegram_message_id, filename, is_edited, is_deleted,
                       EXISTS(
                           SELECT 1 FROM user_message_attachments a
                           WHERE a.message_id = user_messages.id
                       ) AS has_attachment
                FROM user_messages
                WHERE user_id = ? AND id > ? {conversation_filter}
                ORDER BY id ASC LIMIT ?
                """,
                (user_id, max(0, int(after_id)), limit),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_user_messages(
        self,
        user_id: int,
        after_id: int = 0,
        limit: int = 100,
        conversation_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_user_messages,
            user_id,
            after_id,
            limit,
            conversation_only,
        )

    def _list_user_messages_before(
        self,
        user_id: int,
        before_id: int = 0,
        limit: int = 100,
        conversation_only: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        conversation_filter = (
            "AND (direction = 'in' "
            "OR (direction = 'out' AND admin_id IS NOT NULL))"
            if conversation_only
            else ""
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, user_id, direction, text, admin_id, created_at,
                       delivery_status, error_code, media_type, telegram_file_id,
                       telegram_message_id, filename, is_edited, is_deleted,
                       EXISTS(
                           SELECT 1 FROM user_message_attachments a
                           WHERE a.message_id = user_messages.id
                       ) AS has_attachment
                FROM user_messages
                WHERE user_id = ? AND (? = 0 OR id < ?) {conversation_filter}
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, max(0, int(before_id)), max(0, int(before_id)), limit),
            ).fetchall()
            return [_row_to_dict(row) for row in reversed(rows)]

    async def list_user_messages_before(
        self,
        user_id: int,
        before_id: int = 0,
        limit: int = 100,
        conversation_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_user_messages_before,
            user_id,
            before_id,
            limit,
            conversation_only,
        )

    def _get_outgoing_user_message(
        self, user_id: int, message_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_messages "
                "WHERE user_id = ? AND id = ? AND direction = 'out' "
                "AND admin_id IS NOT NULL",
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
