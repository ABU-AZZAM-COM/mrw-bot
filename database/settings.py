"""أساليب settings (mixins لطبقة البيانات).

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


class SettingsMixin(object):
    def _get_state(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    async def get_state(self, key: str) -> Optional[str]:
        return await asyncio.to_thread(self._get_state, key)

    def _set_state(self, key: str, value: Optional[str]) -> None:
        notification = None
        with self._lock:
            previous = None
            if key == "emergency":
                row = self._conn.execute(
                    "SELECT value FROM system_state WHERE key = ?", (key,)
                ).fetchone()
                previous = row["value"] if row else None
            if value is None:
                self._conn.execute("DELETE FROM system_state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO system_state (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            if key == "emergency" and value == "1" and previous != "1":
                notification = self._insert_notification_locked(
                    "emergency",
                    {"on": True, "created_at": datetime.now(timezone.utc).isoformat()},
                )
            self._conn.commit()
        self._publish_notification(notification)

    async def set_state(self, key: str, value: Optional[str]) -> None:
        await asyncio.to_thread(self._set_state, key, value)

    def _record_button_press(self, action: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_button_stats (action, presses, updated_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(action) DO UPDATE SET "
                "presses = presses + 1, updated_at = excluded.updated_at",
                (action, now),
            )
            self._conn.commit()

    async def record_button_press(self, action: str) -> None:
        await asyncio.to_thread(self._record_button_press, action)

    def _button_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT action, presses, updated_at FROM bot_button_stats "
                "ORDER BY presses DESC, action ASC"
            ).fetchall()
        return [
            {
                "action": str(row["action"]),
                "presses": int(row["presses"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    async def button_stats(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._button_stats)

    def _reset_button_stats(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bot_button_stats")
            self._conn.commit()

    async def reset_button_stats(self) -> None:
        await asyncio.to_thread(self._reset_button_stats)

    def _get_welcome_photo(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data, content_type, filename, updated_at "
                "FROM welcome_photo WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            return {
                "data": bytes(row["data"]),
                "content_type": str(row["content_type"]),
                "filename": str(row["filename"]),
                "updated_at": str(row["updated_at"]),
            }

    async def get_welcome_photo(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_welcome_photo)

    def _has_welcome_photo(self) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM welcome_photo WHERE id = 1"
            ).fetchone() is not None

    async def has_welcome_photo(self) -> bool:
        return await asyncio.to_thread(self._has_welcome_photo)

    def _set_welcome_photo(
        self, data: bytes, content_type: str, filename: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO welcome_photo (id, data, content_type, filename, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data = excluded.data,
                    content_type = excluded.content_type,
                    filename = excluded.filename,
                    updated_at = excluded.updated_at
                """,
                (
                    data,
                    content_type,
                    filename,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    async def set_welcome_photo(
        self, data: bytes, content_type: str, filename: str
    ) -> None:
        await asyncio.to_thread(
            self._set_welcome_photo, data, content_type, filename
        )

    def _delete_welcome_photo(self) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM welcome_photo WHERE id = 1")
            self._conn.commit()
            return cursor.rowcount > 0

    async def delete_welcome_photo(self) -> bool:
        return await asyncio.to_thread(self._delete_welcome_photo)

    async def get_runtime_settings(self) -> dict[str, Any]:
        raw = await self.get_state("runtime_settings")
        try:
            parsed = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    async def set_runtime_settings(self, settings: dict[str, Any]) -> None:
        await self.set_state(
            "runtime_settings", json.dumps(settings, ensure_ascii=False, sort_keys=True)
        )

    async def effective_vtypes(self) -> list[str]:
        """أنواع المخالفات الفعلية: التعديل المحفوظ أو الإعداد الافتراضي إن لم يوجد."""
        from services.report_taxonomy import (
            DEFAULT_VIOLATION_TYPES,
            LEGACY_VIOLATION_TYPES,
        )

        raw = await self.get_state("violation_types_override")
        try:
            parsed = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001 — قيمة تالفة تعامل كغير موجودة
            parsed = []
        clean = [str(t).strip() for t in parsed if str(t).strip()]
        if clean == list(LEGACY_VIOLATION_TYPES):
            return list(DEFAULT_VIOLATION_TYPES)
        if clean:
            return clean
        from app import config  # noqa: PLC0415 — داخل الدالة لتفادي الدوران

        return list(config.VIOLATION_TYPES)

    async def set_vtypes_override(self, vtypes: list[str]) -> None:
        await self.set_state(
            "violation_types_override",
            json.dumps(list(vtypes), ensure_ascii=False),
        )

    async def effective_vtype_groups(self) -> dict[str, list[str]]:
        from services.report_taxonomy import (
            default_vtype_groups,
            normalize_vtype_groups,
        )

        items = await self.effective_vtypes()
        raw = await self.get_state("violation_type_groups")
        try:
            parsed = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            parsed = None
        groups = normalize_vtype_groups(parsed, items)
        assigned = {
            item for values in groups.values() for item in values
        } if groups is not None else set()
        if groups is not None:
            from services.report_taxonomy import LEGACY_VIOLATION_TYPES

            if all(
                groups[category] == list(LEGACY_VIOLATION_TYPES)
                for category in groups
            ):
                return default_vtype_groups(items)
        if (
            groups is None
            or any(not values for values in groups.values())
            or any(item not in assigned for item in items)
        ):
            return default_vtype_groups(items)
        return groups

    async def set_vtype_groups(self, groups: dict[str, list[str]]) -> None:
        await self.set_state(
            "violation_type_groups",
            json.dumps(groups, ensure_ascii=False),
        )

    async def get_templates(self) -> list[str]:
        """قوالب الردود الجاهزة (تُخزن كحالة نظام JSON)."""
        raw = await self.get_state("reply_templates")
        try:
            parsed = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001
            parsed = []
        return [str(x) for x in parsed if str(x).strip()]

    async def set_templates(self, items: list[str]) -> None:
        await self.set_state(
            "reply_templates", json.dumps(list(items), ensure_ascii=False)
        )

    def _create_bot_texts_table(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_texts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    category TEXT DEFAULT 'user_facing',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.commit()

    def get_bot_text_sync(self, key: str) -> str | None:
        self._create_bot_texts_table()
        with self._lock:
            row = self._conn.execute("SELECT content FROM bot_texts WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def get_all_bot_texts_sync(self) -> list[dict]:
        self._create_bot_texts_table()
        with self._lock:
            rows = self._conn.execute("SELECT id, key, label, content, category, updated_at FROM bot_texts ORDER BY id").fetchall()
            return [{"id": r[0], "key": r[1], "label": r[2], "content": r[3], "category": r[4], "updated_at": r[5]} for r in rows]

    async def get_bot_text(self, key: str) -> str | None:
        return await asyncio.to_thread(self.get_bot_text_sync, key)

    async def get_all_bot_texts(self) -> list[dict]:
        return await asyncio.to_thread(self.get_all_bot_texts_sync)

    async def ensure_bot_text_defaults(
        self, defaults: list[tuple[str, str, str, str]]
    ) -> None:
        await asyncio.to_thread(self.ensure_bot_text_defaults_sync, defaults)

    def ensure_bot_text_defaults_sync(
        self, defaults: list[tuple[str, str, str, str]]
    ) -> None:
        self._create_bot_texts_table()
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO bot_texts "
                "(key, label, content, category, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                defaults,
            )
            self._conn.commit()

    async def update_bot_text(self, key: str, content: str, category: str = "user_facing") -> bool:
        return await asyncio.to_thread(self.update_bot_text_sync, key, content, category)

    def update_bot_text_sync(self, key: str, content: str, category: str = "user_facing") -> bool:
        self._create_bot_texts_table()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE bot_texts SET content = ?, category = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE key = ?",
                (content, category, key),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                self._conn.execute("INSERT OR IGNORE INTO bot_texts (key, label, content, category, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", (key, key, content, category))
                self._conn.commit()
            return True
