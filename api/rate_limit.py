"""حماية مسارات الإدارة من تخمين الرموز المتكرر.

الخلفية: مسارات ``/api/admin/*`` محمية برمز أو بكلمة مرور، لكن لا شيء كان يمنع
مهاجمًا من إرسال آلاف المحاولات في الدقيقة. هذا الملف يضيف عدّادًا بسيطًا في
الذاكرة (بلا اعتماديات خارجية) يمنع مصدرًا واحدًا من تكرار المحاولات الفاشلة.

القواعد:

* تُحتسب فقط الردود ``401`` و``403`` على مسارات الإدارة.
* أي رد ناجح (أقل من 400) من نفس المصدر يصفّر العدّاد.
* عند تجاوز الحد تُرفض الطلبات بـ``429`` مع ترويسة ``Retry-After``.
* الحالة في الذاكرة فقط: تُصفَّر عند إعادة التشغيل، وهذا كافٍ للحماية من
  التخمين الآلي. التشغيل بأكثر من عملية واحدة يعطي كل عملية عدّادها الخاص.
* الحد الأقصى لعدد المصادر المتتبَّعة محدود كي لا تتحول الحماية إلى استهلاك
  للذاكرة عند توزيع المحاولات على عناوين كثيرة.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

DEFAULT_MAX_FAILURES = 20
DEFAULT_WINDOW_SECONDS = 300.0
DEFAULT_BLOCK_SECONDS = 900.0
MAX_TRACKED_KEYS = 4096


def _env_number(name: str, default: float) -> float:
    """قراءة رقم موجب من البيئة مع تجاهل القيم غير الصالحة."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def client_key(scope: dict[str, Any]) -> str:
    """مفتاح المصدر: آخر عنوان في ``X-Forwarded-For`` أو عنوان الاتصال.

    آخر عنوان هو الذي أضافه الوكيل الموثوق (Render) غالبًا. الحماية هنا طبقة
    إضافية دفاعية، ولا تُستخدم وحدها لتحديد هوية المستخدم.
    """
    forwarded = _header(scope, b"x-forwarded-for").strip()
    if forwarded:
        candidate = forwarded.split(",")[-1].strip()
        if candidate:
            return candidate
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    return "unknown"


class AuthFailureGuard:
    """عدّاد محاولات فاشلة لكل مصدر، بنافذة زمنية ومدة حجب."""

    def __init__(
        self,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        block_seconds: float = DEFAULT_BLOCK_SECONDS,
        max_keys: int = MAX_TRACKED_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = float(window_seconds)
        self.block_seconds = float(block_seconds)
        self.max_keys = max(1, int(max_keys))
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # قراءة الحالة
    # ------------------------------------------------------------------
    def retry_after(self, key: str) -> float:
        """عدد الثواني المتبقية للحجب، أو صفر إن لم يكن المصدر محجوبًا."""
        now = self._clock()
        with self._lock:
            until = self._blocked_until.get(key)
            if not until:
                return 0.0
            remaining = until - now
            if remaining <= 0:
                self._blocked_until.pop(key, None)
                self._failures.pop(key, None)
                return 0.0
            return remaining

    def failure_count(self, key: str) -> int:
        """عدد المحاولات الفاشلة المسجّلة داخل النافذة الحالية."""
        now = self._clock()
        with self._lock:
            self._prune_key(key, now)
            return len(self._failures.get(key, ()))

    # ------------------------------------------------------------------
    # تحديث الحالة
    # ------------------------------------------------------------------
    def record_failure(self, key: str) -> None:
        """تسجيل محاولة فاشلة، والحجب عند بلوغ الحد."""
        now = self._clock()
        with self._lock:
            if self._blocked_until.get(key, 0.0) > now:
                return
            self._prune_key(key, now)
            attempts = self._failures.setdefault(key, [])
            attempts.append(now)
            if len(attempts) >= self.max_failures:
                self._blocked_until[key] = now + self.block_seconds
                self._failures.pop(key, None)
            self._evict_if_needed(now)

    def record_success(self, key: str) -> None:
        """مسح سجل المصدر بعد محاولة ناجحة."""
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)

    def reset(self) -> None:
        """مسح كل الحالة (للاختبارات وإعادة التهيئة)."""
        with self._lock:
            self._failures.clear()
            self._blocked_until.clear()

    # ------------------------------------------------------------------
    # داخلي
    # ------------------------------------------------------------------
    def _prune_key(self, key: str, now: float) -> None:
        attempts = self._failures.get(key)
        if not attempts:
            return
        cutoff = now - self.window_seconds
        fresh = [stamp for stamp in attempts if stamp > cutoff]
        if fresh:
            self._failures[key] = fresh
        else:
            self._failures.pop(key, None)

    def _evict_if_needed(self, now: float) -> None:
        if len(self._failures) <= self.max_keys and len(self._blocked_until) <= self.max_keys:
            return
        for key, until in list(self._blocked_until.items()):
            if until <= now:
                self._blocked_until.pop(key, None)
        for key in list(self._failures)[: max(1, len(self._failures) - self.max_keys)]:
            self._failures.pop(key, None)


class AdminAuthGuardMiddleware:
    """Middleware ASGI: يمنع تكرار المحاولات الفاشلة على مسارات الإدارة."""

    def __init__(
        self,
        app: Any,
        guard: AuthFailureGuard | None = None,
        prefix: str = "/api/admin/",
    ) -> None:
        self.app = app
        self.guard = guard or AuthFailureGuard(
            max_failures=int(_env_number("ADMIN_AUTH_MAX_FAILURES", DEFAULT_MAX_FAILURES)),
            window_seconds=_env_number("ADMIN_AUTH_FAILURE_WINDOW", DEFAULT_WINDOW_SECONDS),
            block_seconds=_env_number("ADMIN_AUTH_BLOCK_SECONDS", DEFAULT_BLOCK_SECONDS),
        )
        self.prefix = prefix

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        key = client_key(scope)
        wait = self.guard.retry_after(key)
        if wait > 0:
            body = b'{"ok":false,"error":"too_many_attempts"}'
            headers = [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"retry-after", str(max(1, int(wait))).encode("ascii")),
            ]
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        status_holder: dict[str, int] = {}

        async def _send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 200))
            await send(message)

        await self.app(scope, receive, _send)

        status = status_holder.get("status", 200)
        if status in (401, 403):
            self.guard.record_failure(key)
        elif status < 400:
            self.guard.record_success(key)


def install_admin_auth_guard(app: Any, prefix: str = "/api/admin/") -> AuthFailureGuard | None:
    """تركيب الحماية على تطبيق FastAPI، والسماح بتعطيلها صراحة من البيئة."""
    if os.getenv("ADMIN_AUTH_GUARD_DISABLED", "").strip() in {"1", "true", "yes"}:
        return None
    guard = AuthFailureGuard(
        max_failures=int(_env_number("ADMIN_AUTH_MAX_FAILURES", DEFAULT_MAX_FAILURES)),
        window_seconds=_env_number("ADMIN_AUTH_FAILURE_WINDOW", DEFAULT_WINDOW_SECONDS),
        block_seconds=_env_number("ADMIN_AUTH_BLOCK_SECONDS", DEFAULT_BLOCK_SECONDS),
    )
    app.add_middleware(AdminAuthGuardMiddleware, guard=guard, prefix=prefix)
    return guard
