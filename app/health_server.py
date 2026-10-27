"""
خادم صحة صغير (Health Server) لمنصات الاستضافة السحابية.

تستخدم منصات مثل Hugging Face Spaces فحصًا عبر HTTP للتأكد من أن الخدمة حيّة،
وتعتبرها ميتة إن لم تستجب — عندها توقف الخدمة. هذا الخادم يردّ بـ "OK" فورًا.

يعتمد على المكتبة القياسية في بايثون فقط (بدون أي مكتبة إضافية)
ويعمل في خيط منفصل حتى لا يعطّل البوت.
"""
from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    """يردّ على أي طلب GET بكلمة OK مع رمز 200."""

    def do_GET(self) -> None:  # noqa: N802  (اسم الدالة مطلوب من المكتبة)
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        """إسكات سجلات الطلبات التفصيلية (منخفضة المستوى)."""
        logger.debug("%s - %s", self.address_string(), fmt % args)


def start_health_server(port: int | None = None) -> bool:
    """يشغّل خادم الصحة في خيط منفصل. يعيد True إذا تم التشغيل بنجاح."""
    port = port or int(os.getenv("PORT", "7860") or 7860)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    except Exception:
        logger.exception("تعذّر تشغيل خادم الصحة على المنفذ %s", port)
        return False

    thread = threading.Thread(
        target=server.serve_forever, daemon=True, name="health-server"
    )
    thread.start()
    logger.info("🩺 خادم الصحة يعمل على المنفذ %s", port)
    return True


def is_space_environment() -> bool:
    """هل نحن داخل Hugging Face Space؟ (تضبط المنصة متغير PORT تلقائيًا)

    تُعطَّل يدويًا عبر DISABLE_HEALTH_SERVER=1 عندما يستمع خادم آخر
    (مثل Gradio) على المنفذ نفسه.
    """
    if os.getenv("DISABLE_HEALTH_SERVER", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return bool(os.getenv("PORT") or os.getenv("HF_SPACE_ID"))
