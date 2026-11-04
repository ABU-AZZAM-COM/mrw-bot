"""اختبارات حماية محاولات المصادقة الإدارية (api/rate_limit.py)."""
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.rate_limit import (
    AdminAuthGuardMiddleware,
    AuthFailureGuard,
    client_key,
)


class FakeClock:
    """ساعة قابلة للتقديم يدويًا لاختبار النوافذ الزمنية."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AuthFailureGuardTests(unittest.TestCase):
    def test_blocks_after_reaching_the_limit(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=3, window_seconds=60, block_seconds=120, clock=clock)

        for _ in range(3):
            self.assertEqual(guard.retry_after("1.2.3.4"), 0.0)
            guard.record_failure("1.2.3.4")

        self.assertGreater(guard.retry_after("1.2.3.4"), 0.0)
        self.assertEqual(guard.retry_after("5.6.7.8"), 0.0)

    def test_success_clears_previous_failures(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=3, window_seconds=60, block_seconds=120, clock=clock)

        guard.record_failure("1.2.3.4")
        guard.record_failure("1.2.3.4")
        guard.record_success("1.2.3.4")
        guard.record_failure("1.2.3.4")

        self.assertEqual(guard.retry_after("1.2.3.4"), 0.0)

    def test_failures_expire_after_the_window(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=3, window_seconds=60, block_seconds=120, clock=clock)

        guard.record_failure("1.2.3.4")
        guard.record_failure("1.2.3.4")
        clock.advance(61)
        guard.record_failure("1.2.3.4")

        self.assertEqual(guard.retry_after("1.2.3.4"), 0.0)

    def test_block_expires(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=1, window_seconds=60, block_seconds=30, clock=clock)

        guard.record_failure("1.2.3.4")
        self.assertGreater(guard.retry_after("1.2.3.4"), 0.0)

        clock.advance(31)
        self.assertEqual(guard.retry_after("1.2.3.4"), 0.0)

    def test_reset_clears_everything(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=1, window_seconds=60, block_seconds=30, clock=clock)
        guard.record_failure("1.2.3.4")

        guard.reset()

        self.assertEqual(guard.retry_after("1.2.3.4"), 0.0)


class ClientKeyTests(unittest.TestCase):
    def test_uses_last_forwarded_address(self) -> None:
        scope = {
            "headers": [(b"x-forwarded-for", b"10.0.0.1, 203.0.113.9")],
            "client": ("172.16.0.5", 1234),
        }
        self.assertEqual(client_key(scope), "203.0.113.9")

    def test_falls_back_to_the_connection_address(self) -> None:
        scope = {"headers": [], "client": ("172.16.0.5", 1234)}
        self.assertEqual(client_key(scope), "172.16.0.5")

    def test_unknown_without_client(self) -> None:
        self.assertEqual(client_key({"headers": []}), "unknown")


class AdminAuthGuardMiddlewareTests(unittest.TestCase):
    def _client(self, guard: AuthFailureGuard) -> TestClient:
        app = FastAPI()
        app.add_middleware(AdminAuthGuardMiddleware, guard=guard, prefix="/api/admin/")

        @app.get("/api/admin/secret")
        def secret() -> JSONResponse:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

        @app.get("/api/admin/ok")
        def ok() -> JSONResponse:
            return JSONResponse({"ok": True})

        @app.get("/health")
        def health() -> JSONResponse:
            return JSONResponse({"ok": True})

        return TestClient(app)

    def test_repeated_failures_become_429(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=2, window_seconds=60, block_seconds=120, clock=clock)
        client = self._client(guard)

        self.assertEqual(client.get("/api/admin/secret").status_code, 401)
        self.assertEqual(client.get("/api/admin/secret").status_code, 401)

        blocked = client.get("/api/admin/secret")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["error"], "too_many_attempts")
        self.assertIn("retry-after", {key.lower() for key in blocked.headers})

    def test_success_resets_the_counter(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=2, window_seconds=60, block_seconds=120, clock=clock)
        client = self._client(guard)

        client.get("/api/admin/secret")
        client.get("/api/admin/ok")
        client.get("/api/admin/secret")

        self.assertEqual(client.get("/api/admin/secret").status_code, 401)

    def test_other_paths_are_not_throttled(self) -> None:
        clock = FakeClock()
        guard = AuthFailureGuard(max_failures=1, window_seconds=60, block_seconds=120, clock=clock)
        client = self._client(guard)

        for _ in range(5):
            self.assertEqual(client.get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
