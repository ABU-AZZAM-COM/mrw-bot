"""أساليب users (mixins لطبقة البيانات).

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


class UsersMixin(object):
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

