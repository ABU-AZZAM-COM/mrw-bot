"""أساليب توثيق المراسلين (mixins لطبقة البيانات).

نُقلت من ``database/__init__.py`` كما هي، لتبقى ``Database`` مركّبة من
وحدات صغيرة بدل ملف واحد ضخم. تعتمد على ``self`` وعلى أدوات ``_shared``.
"""
from __future__ import annotations

import asyncio
import json
import logging
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


class VerificationMixin:
    def _create_verification_ticket(
        self,
        user_id: int,
        ticket_hash: str,
        phone_ciphertext: bytes,
        expires_at: str,
        consented_at: str | None = None,
        consent_version: str | None = None,
    ) -> int | None:
        if not consented_at or not consent_version:
            raise ValueError("verification_consent_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            latest = self._conn.execute(
                "SELECT id, status FROM verified_reporters "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if latest:
                status = latest["status"]
                if status == "verified":
                    return None
                if status == "capturing":
                    ticket = self._conn.execute(
                        "SELECT expires_at FROM verification_tickets "
                        "WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    if ticket and ticket["expires_at"] > now:
                        return None
                    self._conn.execute(
                        "DELETE FROM verification_tickets WHERE user_id = ?",
                        (user_id,),
                    )
                    self._conn.execute(
                        "DELETE FROM verified_reporters "
                        "WHERE id = ? AND status = 'capturing'",
                        (latest["id"],),
                    )
                elif status == "pending":
                    documents = self._conn.execute(
                        "SELECT 1 FROM verified_reporter_private WHERE request_id = ?",
                        (latest["id"],),
                    ).fetchone()
                    if documents:
                        return None
            cursor = self._conn.execute(
                """
                INSERT INTO verified_reporters
                    (user_id, status, consented_at, consent_version)
                VALUES (?, 'capturing', ?, ?)
                """,
                (user_id, consented_at, consent_version),
            )
            request_id = int(cursor.lastrowid)
            self._conn.execute(
                """
                INSERT INTO verification_tickets
                    (user_id, request_id, ticket_hash, phone_ciphertext, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    ticket_hash = excluded.ticket_hash,
                    phone_ciphertext = excluded.phone_ciphertext,
                    expires_at = excluded.expires_at
                """,
                (user_id, request_id, ticket_hash, phone_ciphertext, expires_at),
            )
            self._conn.commit()
            return request_id

    async def create_verification_ticket(
        self,
        user_id: int,
        ticket_hash: str,
        phone_ciphertext: bytes,
        expires_at: str,
        consented_at: str | None = None,
        consent_version: str | None = None,
    ) -> int | None:
        return await asyncio.to_thread(
            self._create_verification_ticket,
            user_id,
            ticket_hash,
            phone_ciphertext,
            expires_at,
            consented_at,
            consent_version,
        )

    def _extend_verification_ticket(
        self, user_id: int, ticket_hash: str, expires_at: str
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE verification_tickets SET expires_at = ?
                WHERE user_id = ? AND ticket_hash = ? AND expires_at > ?
                """,
                (expires_at, user_id, ticket_hash, now),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    async def extend_verification_ticket(
        self, user_id: int, ticket_hash: str, expires_at: str
    ) -> bool:
        return await asyncio.to_thread(
            self._extend_verification_ticket,
            user_id,
            ticket_hash,
            expires_at,
        )

    def _cancel_verification_capture(
        self, user_id: int, ticket_hash: str | None = None
    ) -> bool:
        with self._lock:
            if ticket_hash is None:
                ticket = self._conn.execute(
                    "SELECT request_id FROM verification_tickets WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            else:
                ticket = self._conn.execute(
                    """
                    SELECT request_id FROM verification_tickets
                    WHERE user_id = ? AND ticket_hash = ?
                    """,
                    (user_id, ticket_hash),
                ).fetchone()
            if ticket:
                request_id = int(ticket["request_id"])
                self._conn.execute(
                    "DELETE FROM verification_tickets WHERE user_id = ?",
                    (user_id,),
                )
            elif ticket_hash is None:
                capturing = self._conn.execute(
                    """
                    SELECT id FROM verified_reporters
                    WHERE user_id = ? AND status = 'capturing'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
                if not capturing:
                    return False
                request_id = int(capturing["id"])
            else:
                return False
            cursor = self._conn.execute(
                """
                DELETE FROM verified_reporters
                WHERE id = ? AND user_id = ? AND status = 'capturing'
                """,
                (request_id, user_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    async def cancel_verification_capture(
        self, user_id: int, ticket_hash: str | None = None
    ) -> bool:
        return await asyncio.to_thread(
            self._cancel_verification_capture,
            user_id,
            ticket_hash,
        )

    def _submit_verification_documents(
        self,
        user_id: int,
        ticket_hash: str,
        captures: dict[str, bytes],
    ) -> int | None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._conn:
            ticket = self._conn.execute(
                """
                SELECT request_id, phone_ciphertext FROM verification_tickets
                WHERE user_id = ? AND ticket_hash = ? AND expires_at > ?
                """,
                (user_id, ticket_hash, now),
            ).fetchone()
            if not ticket:
                return None
            request_id = int(ticket["request_id"])
            columns = {
                "selfie": "selfie_ciphertext",
                "id_front": "id_front_ciphertext",
                "id_back": "id_back_ciphertext",
            }
            if set(captures) != set(columns):
                raise ValueError("verification_documents_incomplete")
            self._conn.execute(
                """
                INSERT INTO verified_reporter_private
                    (request_id, user_id, phone_ciphertext, selfie_ciphertext,
                     id_front_ciphertext, id_back_ciphertext)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    user_id,
                    ticket["phone_ciphertext"],
                    captures["selfie"],
                    captures["id_front"],
                    captures["id_back"],
                ),
            )
            for kind, ciphertext in captures.items():
                self._conn.execute(
                    """
                    INSERT INTO verified_reporter_document_versions
                        (request_id, kind, ciphertext, submitted_at,
                         submitted_by_user_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (request_id, kind, ciphertext, now, user_id),
                )
            self._conn.execute(
                "UPDATE verified_reporters SET status = 'pending', "
                "review_state = 'pending', submitted_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'capturing'",
                (now, request_id, user_id),
            )
            self._conn.execute(
                "DELETE FROM verification_tickets WHERE user_id = ?",
                (user_id,),
            )
            self._insert_audit_locked(
                user_id,
                "verified_reporter_submitted",
                str(request_id),
                f"documents={','.join(sorted(captures))}",
            )
            self._conn.commit()
            return request_id

    async def submit_verification_documents(
        self, user_id: int, ticket_hash: str, captures: dict[str, bytes]
    ) -> int | None:
        return await asyncio.to_thread(
            self._submit_verification_documents,
            user_id,
            ticket_hash,
            captures,
        )

    def _verification_status(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT r.id, r.user_id, r.status, r.review_state,
                       r.submitted_at, r.decided_at,
                       r.rejection_reason,
                       r.supplement_requested_at,
                       r.supplement_message,
                       r.supplement_requested_documents,
                       CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                WHERE r.user_id = ?
                ORDER BY r.id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def verification_status(self, user_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._verification_status, user_id)

    def _verification_request(self, request_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, user_id, status, review_state, submitted_at, decided_at,
                       reviewed_by, consented_at, consent_version,
                       rejection_reason, supplement_requested_at,
                       supplement_message, supplement_requested_documents,
                       documents_deleted_at, documents_deleted_by,
                       documents_deleted_reason
                FROM verified_reporters
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def verification_request(
        self, request_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._verification_request, request_id)

    def _update_verification_consent(
        self, user_id: int, consented_at: str, consent_version: str
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE verified_reporters
                SET consented_at = ?, consent_version = ?
                WHERE id = (
                    SELECT id FROM verified_reporters
                    WHERE user_id = ? AND status = 'verified'
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (consented_at, consent_version, user_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    async def update_verification_consent(
        self, user_id: int, consented_at: str, consent_version: str
    ) -> bool:
        return await asyncio.to_thread(
            self._update_verification_consent,
            user_id,
            consented_at,
            consent_version,
        )

    def _delete_verified_reporter(
        self, request_id: int, actor_id: int, reason: str
    ) -> dict[str, Any] | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id, user_id, status FROM verified_reporters WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not row:
                return None
            item = _row_to_dict(row)
            if item["status"] not in {"verified", "rejected"}:
                return None
            self._conn.execute(
                "DELETE FROM verified_reporter_private WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                "DELETE FROM verified_reporter_document_versions WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                "DELETE FROM verification_supplement_uploads WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                "DELETE FROM verification_tickets WHERE request_id = ?",
                (request_id,),
            )
            self._conn.execute(
                """
                UPDATE verified_reporters
                SET documents_deleted_at = ?, documents_deleted_by = ?,
                    documents_deleted_reason = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    actor_id,
                    reason,
                    request_id,
                ),
            )
            self._insert_audit_locked(
                actor_id,
                "verified_reporter_documents_deleted",
                str(request_id),
                f"reason={reason}; user_id={item['user_id']}",
            )
            self._conn.commit()
            return item

    async def delete_verified_reporter(
        self, request_id: int, actor_id: int, reason: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._delete_verified_reporter,
            request_id,
            actor_id,
            reason,
        )

    def _list_verification_requests(
        self, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        if status not in {"pending", "needs_info", "verified", "rejected", "all"}:
            raise ValueError("invalid_verification_status")
        limit = max(1, min(int(limit), 200))
        if status == "all":
            clause = ""
            params: tuple[Any, ...] = (limit,)
        elif status == "needs_info":
            clause = "WHERE r.status = 'pending' AND r.review_state = ?"
            params = ("needs_info", limit)
        elif status == "pending":
            clause = "WHERE r.status = ? AND r.review_state = 'pending'"
            params = (status, limit)
        else:
            clause = "WHERE r.status = ?"
            params = (status, limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT r.id, r.user_id, r.status, r.submitted_at,
                       r.review_state,
                       r.decided_at, r.reviewed_by, r.consented_at,
                       r.consent_version, r.rejection_reason,
                       r.supplement_requested_at,
                       r.supplement_message,
                       r.supplement_requested_documents,
                       r.documents_deleted_at, r.documents_deleted_by,
                       r.documents_deleted_reason,
                       CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents,
                       u.full_name, u.username
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                LEFT JOIN bot_users u ON u.user_id = r.user_id
                {clause}
                ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END,
                         r.submitted_at DESC, r.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_verification_requests(
        self, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_verification_requests, status, limit
        )

    def _search_verification_requests(
        self,
        *,
        status: str,
        query: str | None,
        start_date: str | None,
        end_date: str | None,
        documents: str = "all",
        sort: str = "waiting",
        page: int = 0,
        size: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        if status not in {"pending", "needs_info", "verified", "rejected", "all"}:
            raise ValueError("invalid_verification_status")
        if documents not in {"all", "available", "missing"}:
            raise ValueError("invalid_verification_documents_filter")
        if sort not in {"waiting", "oldest", "newest"}:
            raise ValueError("invalid_verification_sort")
        page = max(0, int(page))
        size = max(1, min(int(size), 100))
        clauses: list[str] = []
        params: list[Any] = []
        if status == "pending":
            clauses.append("r.status = 'pending' AND r.review_state = 'pending'")
        elif status == "needs_info":
            clauses.append("r.status = 'pending' AND r.review_state = 'needs_info'")
        elif status != "all":
            clauses.append("r.status = ?")
            params.append(status)
        if query:
            clauses.append(
                "(u.full_name LIKE ? OR u.username LIKE ? "
                "OR CAST(r.user_id AS TEXT) LIKE ?)"
            )
            pattern = f"%{query.strip()}%"
            params.extend((pattern, pattern, pattern))
        if start_date:
            clauses.append("substr(r.submitted_at, 1, 10) >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("substr(r.submitted_at, 1, 10) <= ?")
            params.append(end_date)
        if documents == "available":
            clauses.append("p.request_id IS NOT NULL")
        elif documents == "missing":
            clauses.append("p.request_id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = page * size
        order_by = {
            "waiting": (
                "CASE "
                "WHEN r.status = 'pending' AND r.review_state = 'pending' THEN 0 "
                "WHEN r.status = 'pending' AND r.review_state = 'needs_info' THEN 1 "
                "ELSE 2 END, r.submitted_at ASC, r.id ASC"
            ),
            "oldest": "r.submitted_at ASC, r.id ASC",
            "newest": "r.submitted_at DESC, r.id DESC",
        }[sort]
        with self._lock:
            total = int(
                self._conn.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM verified_reporters r
                    LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                    LEFT JOIN bot_users u ON u.user_id = r.user_id
                    {where}
                    """,
                    params,
                ).fetchone()["count"]
            )
            rows = self._conn.execute(
                f"""
                SELECT r.id, r.user_id, r.status, r.review_state,
                       r.submitted_at, r.decided_at, r.reviewed_by,
                       r.rejection_reason, r.supplement_requested_at,
                       r.supplement_message, r.supplement_requested_documents,
                       r.documents_deleted_at, r.documents_deleted_by,
                       r.documents_deleted_reason,
                       CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents,
                       u.full_name, u.username
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                LEFT JOIN bot_users u ON u.user_id = r.user_id
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (*params, size, offset),
            ).fetchall()
            return [_row_to_dict(row) for row in rows], total

    async def search_verification_requests(
        self,
        *,
        status: str = "pending",
        query: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        documents: str = "all",
        sort: str = "waiting",
        page: int = 0,
        size: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        return await asyncio.to_thread(
            self._search_verification_requests,
            status=status,
            query=query,
            start_date=start_date,
            end_date=end_date,
            documents=documents,
            sort=sort,
            page=page,
            size=size,
        )

    def _verification_analytics(self, days: int = 30) -> dict[str, Any]:
        days = max(7, min(int(days), 90))
        start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
        start_iso = start.isoformat()
        with self._lock:
            counts = self._conn.execute(
                """
                SELECT status, review_state, COUNT(*) AS count
                FROM verified_reporters
                WHERE status != 'capturing'
                GROUP BY status, review_state
                """
            ).fetchall()
            wait = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS open_count,
                    MIN(submitted_at) AS oldest_submitted_at,
                    AVG((julianday(?) - julianday(submitted_at)) * 24)
                        AS average_wait_hours
                FROM verified_reporters
                WHERE status = 'pending' AND submitted_at IS NOT NULL
                """,
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            ).fetchone()
            trend_rows = self._conn.execute(
                """
                SELECT substr(submitted_at, 1, 10) AS day, COUNT(*) AS count
                FROM verified_reporters
                WHERE submitted_at IS NOT NULL AND substr(submitted_at, 1, 10) >= ?
                GROUP BY substr(submitted_at, 1, 10)
                ORDER BY day
                """,
                (start_iso,),
            ).fetchall()
        status_counts = {"pending": 0, "needs_info": 0, "verified": 0, "rejected": 0}
        for row in counts:
            key = (
                "needs_info"
                if row["status"] == "pending" and row["review_state"] == "needs_info"
                else row["status"]
            )
            if key in status_counts:
                status_counts[key] += int(row["count"])
        trend_map = {str(row["day"]): int(row["count"]) for row in trend_rows}
        trend = [
            {"date": (start + timedelta(days=offset)).isoformat(),
             "count": trend_map.get((start + timedelta(days=offset)).isoformat(), 0)}
            for offset in range(days)
        ]
        return {
            "counts": status_counts,
            "waiting": {
                "open_count": int(wait["open_count"] or 0),
                "oldest_submitted_at": wait["oldest_submitted_at"],
                "average_wait_hours": (
                    round(float(wait["average_wait_hours"]), 2)
                    if wait["average_wait_hours"] is not None
                    else None
                ),
            },
            "trend": trend,
        }

    async def verification_analytics(self, days: int = 30) -> dict[str, Any]:
        return await asyncio.to_thread(self._verification_analytics, days)

    def _verification_supplement_history(
        self, request_id: int
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, request_id, requested_documents, message,
                       requested_by, requested_at, response_at
                FROM verification_supplement_requests
                WHERE request_id = ?
                ORDER BY id
                """,
                (request_id,),
            ).fetchall()
        history = []
        for row in rows:
            item = _row_to_dict(row)
            item["requested_documents"] = json.loads(item["requested_documents"])
            history.append(item)
        return history

    async def verification_supplement_history(
        self, request_id: int
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._verification_supplement_history, request_id
        )

    def _verification_audit_history(
        self, request_id: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, ts, actor_id, action, target, details
                FROM audit_log
                WHERE target = ? AND action LIKE 'verified_reporter_%'
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(request_id), limit),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def verification_audit_history(
        self, request_id: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._verification_audit_history, request_id, limit
        )

    def _verification_document_versions(
        self, request_id: int, kind: str
    ) -> list[dict[str, Any]]:
        if kind not in {"selfie", "id_front", "id_back"}:
            raise ValueError("invalid_verification_document_kind")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, kind, submitted_at, submitted_by_user_id
                FROM verified_reporter_document_versions
                WHERE request_id = ? AND kind = ?
                ORDER BY id
                """,
                (request_id, kind),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def verification_document_versions(
        self, request_id: int, kind: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._verification_document_versions, request_id, kind
        )

    def _verification_document_version(
        self, request_id: int, kind: str, version_id: int
    ) -> bytes | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT ciphertext FROM verified_reporter_document_versions
                WHERE request_id = ? AND kind = ? AND id = ?
                """,
                (request_id, kind, version_id),
            ).fetchone()
            return bytes(row["ciphertext"]) if row else None

    async def verification_document_version(
        self, request_id: int, kind: str, version_id: int
    ) -> bytes | None:
        return await asyncio.to_thread(
            self._verification_document_version, request_id, kind, version_id
        )

    def _request_verification_supplement(
        self,
        request_id: int,
        reviewer_id: int,
        requested_documents: list[str],
        message: str,
    ) -> dict[str, Any] | None:
        allowed = {"selfie", "id_front", "id_back"}
        documents = list(dict.fromkeys(requested_documents))
        text = message.strip()
        if not documents or any(item not in allowed for item in documents):
            raise ValueError("invalid_supplement_documents")
        if not text or len(text) > 1000:
            raise ValueError("supplement_message_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._conn:
            request = self._conn.execute(
                """
                SELECT r.user_id
                FROM verified_reporters r
                JOIN verified_reporter_private p ON p.request_id = r.id
                WHERE r.id = ? AND r.status = 'pending'
                """,
                (request_id,),
            ).fetchone()
            if not request:
                return None
            cursor = self._conn.execute(
                """
                INSERT INTO verification_supplement_requests
                    (request_id, requested_documents, message, requested_by, requested_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (request_id, json.dumps(documents), text, reviewer_id, now),
            )
            self._conn.execute(
                """
                UPDATE verified_reporters
                SET review_state = 'needs_info', supplement_requested_at = ?,
                    supplement_message = ?, supplement_requested_documents = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, text, json.dumps(documents), request_id),
            )
            self._conn.execute(
                "DELETE FROM verification_supplement_uploads WHERE request_id = ?",
                (request_id,),
            )
            self._conn.commit()
            return {
                "request_id": request_id,
                "user_id": int(request["user_id"]),
                "supplement_id": int(cursor.lastrowid),
                "requested_documents": documents,
                "message": text,
                "requested_at": now,
            }

    async def request_verification_supplement(
        self,
        request_id: int,
        reviewer_id: int,
        requested_documents: list[str],
        message: str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._request_verification_supplement,
            request_id,
            reviewer_id,
            requested_documents,
            message,
        )

    def _active_verification_supplement(
        self, request_id: int, user_id: int
    ) -> dict[str, Any] | None:
        with self._lock:
            request = self._conn.execute(
                """
                SELECT r.id, r.user_id, s.id AS supplement_id,
                       s.requested_documents, s.message, s.requested_at,
                       s.response_at
                FROM verified_reporters r
                JOIN verification_supplement_requests s ON s.request_id = r.id
                WHERE r.id = ? AND r.user_id = ? AND r.status = 'pending'
                  AND r.review_state = 'needs_info' AND s.response_at IS NULL
                ORDER BY s.id DESC LIMIT 1
                """,
                (request_id, user_id),
            ).fetchone()
            if not request:
                return None
            item = _row_to_dict(request)
            item["requested_documents"] = json.loads(item["requested_documents"])
            uploaded = self._conn.execute(
                "SELECT kind FROM verification_supplement_uploads WHERE request_id = ?",
                (request_id,),
            ).fetchall()
            item["uploaded_documents"] = [str(row["kind"]) for row in uploaded]
            return item

    async def active_verification_supplement(
        self, request_id: int, user_id: int
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._active_verification_supplement, request_id, user_id
        )

    def _save_verification_supplement_upload(
        self, request_id: int, user_id: int, kind: str, ciphertext: bytes
    ) -> dict[str, Any] | None:
        if kind not in {"selfie", "id_front", "id_back"}:
            raise ValueError("invalid_verification_document_kind")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._conn:
            request = self._conn.execute(
                """
                SELECT s.id, s.requested_documents
                FROM verified_reporters r
                JOIN verified_reporter_private p ON p.request_id = r.id
                JOIN verification_supplement_requests s ON s.request_id = r.id
                WHERE r.id = ? AND r.user_id = ? AND r.status = 'pending'
                  AND r.review_state = 'needs_info' AND s.response_at IS NULL
                ORDER BY s.id DESC LIMIT 1
                """,
                (request_id, user_id),
            ).fetchone()
            if not request:
                return None
            requested = json.loads(request["requested_documents"])
            if kind not in requested:
                raise ValueError("document_not_requested")
            self._conn.execute(
                """
                INSERT INTO verification_supplement_uploads
                    (request_id, kind, ciphertext, submitted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(request_id, kind) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    submitted_at = excluded.submitted_at
                """,
                (request_id, kind, ciphertext, now),
            )
            rows = self._conn.execute(
                "SELECT kind, ciphertext, submitted_at FROM verification_supplement_uploads "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchall()
            uploads = {str(row["kind"]): row for row in rows}
            remaining = [doc for doc in requested if doc not in uploads]
            completed = not remaining
            self._insert_audit_locked(
                user_id,
                "verified_reporter_supplement_received",
                str(request_id),
                f"document={kind}; completed={str(completed).lower()}",
            )
            if completed:
                for doc in requested:
                    row = uploads[doc]
                    self._conn.execute(
                        """
                        INSERT INTO verified_reporter_document_versions
                            (request_id, kind, ciphertext, submitted_at,
                             submitted_by_user_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (request_id, doc, row["ciphertext"], row["submitted_at"], user_id),
                    )
                    column = {
                        "selfie": "selfie_ciphertext",
                        "id_front": "id_front_ciphertext",
                        "id_back": "id_back_ciphertext",
                    }[doc]
                    self._conn.execute(
                        f"UPDATE verified_reporter_private SET {column} = ? "
                        "WHERE request_id = ? AND user_id = ?",
                        (row["ciphertext"], request_id, user_id),
                    )
                self._conn.execute(
                    """
                    UPDATE verification_supplement_requests
                    SET response_at = ?
                    WHERE id = ? AND response_at IS NULL
                    """,
                    (now, request["id"]),
                )
                self._conn.execute(
                    """
                    UPDATE verified_reporters SET review_state = 'pending'
                    WHERE id = ? AND user_id = ? AND status = 'pending'
                    """,
                    (request_id, user_id),
                )
                self._conn.execute(
                    "DELETE FROM verification_supplement_uploads WHERE request_id = ?",
                    (request_id,),
                )
            self._conn.commit()
            return {
                "request_id": request_id,
                "user_id": user_id,
                "uploaded_documents": sorted(uploads),
                "requested_documents": requested,
                "remaining_documents": remaining,
                "completed": completed,
            }

    async def save_verification_supplement_upload(
        self, request_id: int, user_id: int, kind: str, ciphertext: bytes
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._save_verification_supplement_upload,
            request_id,
            user_id,
            kind,
            ciphertext,
        )

    def _verification_detail(self, request_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT r.*, CASE WHEN p.request_id IS NULL THEN 0 ELSE 1 END AS has_documents,
                       u.full_name, u.username
                FROM verified_reporters r
                LEFT JOIN verified_reporter_private p ON p.request_id = r.id
                LEFT JOIN bot_users u ON u.user_id = r.user_id
                WHERE r.id = ?
                """,
                (request_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    async def verification_detail(self, request_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._verification_detail, request_id)

    def _verification_document(
        self, request_id: int, kind: str
    ) -> bytes | None:
        columns = {
            "selfie": "selfie_ciphertext",
            "id_front": "id_front_ciphertext",
            "id_back": "id_back_ciphertext",
        }
        column = columns.get(kind)
        if column is None:
            raise ValueError("invalid_verification_document_kind")
        with self._lock:
            row = self._conn.execute(
                f"SELECT {column} AS document FROM verified_reporter_private "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return bytes(row["document"]) if row else None

    async def verification_document(
        self, request_id: int, kind: str
    ) -> bytes | None:
        return await asyncio.to_thread(
            self._verification_document, request_id, kind
        )

    def _verification_phone(self, request_id: int) -> bytes | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT phone_ciphertext FROM verified_reporter_private "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return bytes(row["phone_ciphertext"]) if row else None

    async def verification_phone(self, request_id: int) -> bytes | None:
        return await asyncio.to_thread(self._verification_phone, request_id)

    def _decide_verification(
        self, request_id: int, reviewer_id: int, decision: str,
        rejection_reason: str | None = None,
    ) -> bool:
        if decision not in {"verified", "rejected"}:
            raise ValueError("invalid_verification_decision")
        reason = (rejection_reason or "").strip()
        if decision == "rejected" and not reason:
            raise ValueError("verification_rejection_reason_required")
        if len(reason) > 1000:
            raise ValueError("verification_rejection_reason_too_long")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            docs = self._conn.execute(
                "SELECT 1 FROM verified_reporter_private WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if not docs:
                return False
            cursor = self._conn.execute(
                """
                UPDATE verified_reporters
                SET status = ?, decided_at = ?, reviewed_by = ?,
                    rejection_reason = ?, review_state = 'pending'
                WHERE id = ? AND status IN ('pending', 'verified')
                """,
                (
                    decision, now, reviewer_id,
                    reason if decision == "rejected" else None,
                    request_id,
                ),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    async def decide_verification(
        self, request_id: int, reviewer_id: int, decision: str,
        rejection_reason: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._decide_verification,
            request_id,
            reviewer_id,
            decision,
            rejection_reason,
        )

