"""
طبقة قاعدة البيانات — SQLite.

- جدول واحد رئيسي: reports (البلاغات).
- كل العمليات الحساسة مغلقة بـ Lock لضمان عدم تعارض الكتابة.
- الاستدعاءات من البوت غير متزامنة (async) عبر asyncio.to_thread
  حتى لا يتجمد البوت أثناء الكتابة/القراءة.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 
# حالات البلاغ: الرمز التعبيري لكل حالة (تُستخدم في الرسائل والقوائم)
# ------------------------------------------------------------------ 
STATUS_EMOJI = {
    "new": "🟡",
    "review": "🟠",
    "approved": "🟢",
    "reject": "🔴",
    "awaiting_info": "🟠",
    "closed_no_response": "⚫",
}

STATUS_BADGE = {name: f"{emoji} {name}" for name, emoji in STATUS_EMOJI.items()}


# ------------------------------------------------------------------ 
# حالات البلاغ (تُخزّن بالعربية في القاعدة لسهولة قراءتها في التقارير)
# ------------------------------------------------------------------ 
STATUS_NEW = "جديد"
STATUS_REVIEW = "قيد المراجعة"
STATUS_APPROVED = "معتمد"
STATUS_REJECTED = "مرفوض"
STATUS_AWAITING_INFO = "بانتظار معلومات إضافية من المبلّغ"
STATUS_CLOSED_NO_RESPONSE = "أُغلق لعدم الرد"

STATUS_CODES: dict[str, str] = {
    "new": "NEW",
    "review": "REVIEW",
    "approved": "APPROVED",
    "reject": "REJECT",
    "awaiting_info": "AWAITING_INFO",
    "closed_no_response": "CLOSED_NO_RESPONSE",
}


# ------------------------------------------------------------------ 
# واجهة قاعدة البيانات
# ------------------------------------------------------------------ 
class Database:
    """طبقة الوصول إلى قاعدة البيانات SQLite."""

    def __init__(self, db_path: str = "bot_texts.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """إنشاء جدول bot_texts إذا لم يكن موجودًا."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_texts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,           -- مثل: welcome_message, verify_consent, help_text
                    label TEXT NOT NULL,                -- الاسم الظاهري في اللوحة: "رسالة الترحيب"
                    content TEXT NOT NULL DEFAULT '',
                    category TEXT DEFAULT 'user_facing',-- user_facing | button_label | system
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ 
    # CRUD operations for bot_texts
    # ------------------------------------------------------------------ 
    async def get_bot_text(self, key: str) -> Optional[str]:
        """جلب نص واحد حسب المفتاح."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT content FROM bot_texts WHERE key = ?", (key,))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None

    async def get_all_bot_texts(self) -> list[dict]:
        """جلب جميع النصوص مع تفاصيلها."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, key, label, content, category, updated_at FROM bot_texts ORDER BY id")
            rows = cursor.fetchall()
            conn.close()
            return [
                {
                    "id": r[0],
                    "key": r[1],
                    "label": r[2],
                    "content": r[3],
                    "category": r[4],
                    "updated_at": r[5],
                }
                for r in rows
            ]

    async def update_bot_text(self, key: str, content: str, category: str = "user_facing") -> bool:
        """تحديث نص موجود في قاعدة البيانات."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bot_texts SET content = ?, category = ? WHERE key = ?",
                (content, category, key),
            )
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

    async def delete_bot_text(self, key: str) -> bool:
        """حذف نص من قاعدة البيانات."""
        async with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bot_texts WHERE key = ?", (key,))
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return affected > 0

    # ------------------------------------------------------------------ 
    # Helper: get all texts with fallback defaults
    # ------------------------------------------------------------------ 
    async def get_bot_text_with_fallback(self, key: str) -> str:
        """جلب النص مع استخدام النص الافتراضي إذا لم يكن موجودًا."""
        text = await self.get_bot_text(key)
        if text is None:
            # Fallback: استخدام النص الافتراضي (يمكن تعريفه في config)
            return "هذا النص هو النص الافتراضي عند عدم وجود نص محدد"
        return text

    async def get_all_bot_texts_with_fallback(self) -> list[dict]:
        """جلب جميع النصوص مع استخدام النص الافتراضي للتي لا توجد."""
        texts = await self.get_all_bot_texts()
        # Add fallback entries for common keys if missing
        for text in texts:
            key = text["key"]
            if not text.get("content"):
                await self.update_bot_text(key, "هذا النص هو النص الافتراضي عند عدم وجود نص محدد", category="user_facing")
        return texts
