"""Sightengine API client and nudity-score parsing."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SIGHTENGINE_URL = "https://api.sightengine.com/1.0/check.json"
SIGHTENGINE_MODEL = "nudity-2.1"
RESTRICTION_THRESHOLD = 0.40
REQUEST_TIMEOUT_SECONDS = 25.0


class SightengineError(RuntimeError):
    """The external moderation service could not return a usable result."""


def _http_status_message(status_code: int) -> str:
    if status_code == 401:
        return "رفض Sightengine بيانات الاعتماد (HTTP 401)"
    if status_code == 403:
        return "رفض Sightengine صلاحية الحساب أو النموذج (HTTP 403)"
    if status_code == 429:
        return "تم تجاوز حد طلبات Sightengine أو رصيد الحساب (HTTP 429)"
    if 400 <= status_code < 500:
        return f"رفض Sightengine الطلب (HTTP {status_code})"
    if status_code >= 500:
        return f"خدمة Sightengine غير متاحة مؤقتًا (HTTP {status_code})"
    return f"أعاد Sightengine حالة HTTP غير متوقعة ({status_code})"


def _api_error_message(payload: Any) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "رفض Sightengine الطلب دون تفاصيل خطأ صالحة"

    error_type = error.get("type")
    code = error.get("code")
    code_suffix = f" (رمز {code})" if isinstance(code, int) else ""
    messages = {
        "credentials_error": "رفض Sightengine بيانات الاعتماد",
        "plan_error": "خطة حساب Sightengine لا تتيح هذا الطلب أو النموذج",
        "usage_limit": "تم تجاوز حصة أو حد استخدام Sightengine",
        "argument_error": "رفض Sightengine أحد مدخلات الطلب أو اسم النموذج",
        "media_error": "تعذر على Sightengine قراءة صيغة الصورة أو محتواها",
        "request_error": "رفض Sightengine بنية الطلب",
        "api_error": "حدث خطأ في واجهة Sightengine",
        "callback_error": "رفض Sightengine إعداد callback غير صالح",
        "unknown_error": "أعاد Sightengine خطأ غير مصنف",
    }
    if isinstance(error_type, str) and error_type in messages:
        return messages[error_type] + code_suffix
    return "رفض Sightengine الطلب؛ تحقق من بيانات الاعتماد والخطة والنموذج" + code_suffix


@dataclass(frozen=True)
class NudityScores:
    scores: tuple[tuple[str, float], ...]

    @property
    def max_score(self) -> float:
        return max((score for _, score in self.scores), default=0.0)

    @property
    def is_restricted(self) -> bool:
        return any(score > RESTRICTION_THRESHOLD for _, score in self.scores)


def credentials() -> tuple[str, str] | None:
    api_user = os.getenv("SIGHTENGINE_API_USER", "").strip()
    api_secret = (
        os.getenv("SIGHTENGINE_API_SECRET", "")
        or os.getenv("SIGHTENGINE_SECRET", "")
    ).strip()
    if not api_user or not api_secret:
        return None
    return api_user, api_secret


def parse_scores(payload: Any) -> NudityScores:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise SightengineError(_api_error_message(payload))
    nudity = payload.get("nudity")
    if not isinstance(nudity, dict):
        raise SightengineError("استجابة Sightengine لا تتضمن درجات التصنيف")

    required_classes = ("sexual_activity", "sexual_display", "erotica")
    if any(key not in nudity for key in required_classes):
        raise SightengineError("استجابة Sightengine لا تتضمن درجات العري الأساسية")

    scores: list[tuple[str, float]] = []

    def collect(class_name: str, value: Any) -> None:
        if isinstance(value, dict):
            for child_name, child_value in value.items():
                if child_name == "none":
                    continue
                collect(f"{class_name}.{child_name}", child_value)
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SightengineError("استجابة Sightengine تحتوي درجة تصنيف غير صالحة")
        score = float(value)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise SightengineError("استجابة Sightengine تحتوي درجة تصنيف غير صالحة")
        scores.append((class_name, score))

    for class_name in (
        "sexual_activity",
        "sexual_display",
        "erotica",
        "very_suggestive",
        "suggestive",
        "mildly_suggestive",
        "suggestive_classes",
    ):
        if class_name in nudity:
            collect(class_name, nudity[class_name])
    return NudityScores(scores=tuple(scores))


async def inspect_image(path: str | Path) -> NudityScores:
    auth = credentials()
    if auth is None:
        raise SightengineError("بيانات اعتماد Sightengine غير مكتملة")

    api_user, api_secret = auth
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            with Path(path).open("rb") as image:
                response = await client.post(
                    SIGHTENGINE_URL,
                    data={
                        "api_user": api_user,
                        "api_secret": api_secret,
                        "models": SIGHTENGINE_MODEL,
                    },
                    files={"media": (Path(path).name, image, "application/octet-stream")},
                )
            response.raise_for_status()
            return parse_scores(response.json())
    except SightengineError:
        raise
    except httpx.HTTPStatusError as exc:
        message = _http_status_message(exc.response.status_code)
        raise SightengineError(message) from exc
    except httpx.TimeoutException as exc:
        raise SightengineError("انتهت مهلة الاتصال بـSightengine") from exc
    except httpx.ConnectError as exc:
        raise SightengineError("تعذر إنشاء اتصال بخدمة Sightengine") from exc
    except httpx.RequestError as exc:
        raise SightengineError("تعذر إرسال الطلب إلى Sightengine") from exc
    except OSError as exc:
        raise SightengineError("تعذرت قراءة ملف الصورة للفحص") from exc
    except ValueError as exc:
        raise SightengineError("أعاد Sightengine استجابة غير صالحة") from exc
