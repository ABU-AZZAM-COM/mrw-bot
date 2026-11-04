"""أساليب audit (mixins لطبقة البيانات).

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


class AuditMixin(object):
    def _audit(self, actor_id: Optional[int], action: str, target: Optional[str],
               details: Optional[str]) -> None:
        with self._lock:
            self._insert_audit_locked(actor_id, action, target, details)
            self._conn.commit()

    def _insert_audit_locked(
        self,
        actor_id: Optional[int],
        action: str,
        target: Optional[str],
        details: Optional[str],
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT INTO audit_log (ts, actor_id, action, target, details)"
            " VALUES (?, ?, ?, ?, ?)",
            (now_iso, actor_id, action, target, details),
        )

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

