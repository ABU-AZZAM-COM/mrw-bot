"""أساليب osint (mixins لطبقة البيانات).

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


class OsintMixin(object):
    def _list_osint_sources(
        self, report_id: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            if report_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM osint_sources "
                    "ORDER BY accessed_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM osint_sources WHERE report_id = ? "
                    "ORDER BY accessed_at DESC, id DESC LIMIT ?",
                    (int(report_id), limit),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]

    async def list_osint_sources(
        self, report_id: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_osint_sources, report_id, limit)

    def _add_osint_source(
        self,
        report_id: int | None,
        url: str,
        title: str,
        notes: str,
        source_level: str,
        admin_id: int,
    ) -> dict[str, Any]:
        accessed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osint_sources
                    (report_id, url, title, notes, source_level, accessed_at, admin_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id, url, title, notes, source_level, accessed_at, admin_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM osint_sources WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            self._conn.commit()
            return _row_to_dict(row)

    async def add_osint_source(
        self,
        report_id: int | None,
        url: str,
        title: str,
        notes: str,
        source_level: str,
        admin_id: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._add_osint_source,
            report_id,
            url,
            title,
            notes,
            source_level,
            admin_id,
        )

    def _delete_osint_source(self, source_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM osint_sources WHERE id = ?", (source_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

