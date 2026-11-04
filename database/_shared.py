"""ثوابت الحالات وأدوات تحويل الصفوف المشتركة في طبقة البيانات.

فُصلت عن ``database/__init__.py`` كي تستوردها وحدات الـmixins دون استيراد
دائري، وتبقى نقطة واحدة لتغيير أي ثابت أو أداة.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# حالات البلاغ (تُخزّن بالعربية في القاعدة لسهولة قراءتها في التقارير)
# ------------------------------------------------------------------
STATUS_NEW = "جديد"
STATUS_REVIEW = "قيد المراجعة"
STATUS_APPROVED = "معتمد"
STATUS_REJECTED = "مرفوض"
STATUS_AWAITING_INFO = "بانتظار معلومات إضافية من المبلّغ"
STATUS_CLOSED_NO_RESPONSE = "أُغلق لعدم الرد"

STATUS_CODES: dict[str, str] = {          # كود الأزرار -> اسم الحالة في القاعدة
    "new": STATUS_NEW,
    "review": STATUS_REVIEW,
    "approved": STATUS_APPROVED,
    "rejected": STATUS_REJECTED,
}

# جداول تُحذف بياناتها الحساسة عند التصدير/التدقيق الخاص بالهوية.
_IDENTITY_PRIVATE_TABLES = (
    "verification_tickets",
    "verified_reporter_private",
)


class _DatabaseRow(dict[str, Any]):
    """صف يسمح بالوصول بالاسم أو بالترتيب، مع الحفاظ على ترتيب القيم."""

    def __init__(self, values: dict[str, Any], ordered_values: tuple[Any, ...]) -> None:
        super().__init__(values)
        self._ordered_values = ordered_values

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._ordered_values[key]
        return super().__getitem__(key)


def _mapping_row_factory(cursor: Any, row: Any) -> _DatabaseRow:
    columns = cursor.description or ()
    values = tuple(row)
    return _DatabaseRow(
        {column[0]: values[index] for index, column in enumerate(columns)},
        values,
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)
