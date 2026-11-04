"""مسارات توثيق المراسلين: الطلبات، الوثائق، القرارات، والحذف.

انتقلت من ``api/admin_api.py``. تُبقي نفس صلاحيات ``identity`` وحماية
الوثائق، وتستقبل مساعدات ``admin_api`` المشتركة عند التركيب.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from api.admin_auth import _admin_actor, _auth
from services import verified_reporters


def register(app, *, db_of, bad) -> None:
    logger = logging.getLogger(__name__)

    from api import admin_api as core

    # غلافان يربطان الاسم وقت الطلب لا وقت التركيب: يبقيان قابلين للاستبدال
    # في الاختبارات (patch على admin_api) ويمنعان الاستيراد الدائري.
    def _notify_verification_decision(*args, **kwargs):
        return core._notify_verification_decision(*args, **kwargs)

    def _notify_user_verification_supplement(*args, **kwargs):
        return core._notify_user_verification_supplement(*args, **kwargs)
    @app.get("/api/admin/verified-reporters")
    async def verified_reporters_list(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            status = request.query_params.get("status", "pending")
            page = int(request.query_params.get("page", "0"))
            size = int(
                request.query_params.get("size")
                or request.query_params.get("limit", "25")
            )
            start_date = request.query_params.get("start_date") or None
            end_date = request.query_params.get("end_date") or None
            documents = request.query_params.get("documents", "all")
            sort = request.query_params.get("sort", "waiting")
            for value in (start_date, end_date):
                if value:
                    datetime.strptime(value, "%Y-%m-%d")
            items, total = await db_of().search_verification_requests(
                status=status,
                query=request.query_params.get("q"),
                start_date=start_date,
                end_date=end_date,
                documents=documents,
                sort=sort,
                page=page,
                size=size,
            )
        except (ValueError, TypeError):
            return bad("bad_query")
        return JSONResponse(
            {"ok": True, "items": items, "total": total, "page": page, "size": size},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/admin/verified-reporters/analytics")
    async def verified_reporters_analytics(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            days = int(request.query_params.get("days", "30"))
            analytics = await db_of().verification_analytics(days)
        except (ValueError, TypeError):
            return bad("bad_query")
        return JSONResponse(
            {"ok": True, **analytics},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/admin/verified-reporters/{request_id}")
    async def verified_reporter_detail(
        request_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        item = await db.verification_detail(request_id)
        if not item:
            return bad("verification_request_not_found", 404)
        history = await db.verification_supplement_history(request_id)
        activity = await db.verification_audit_history(request_id)
        versions = {
            kind: await db.verification_document_versions(request_id, kind)
            for kind in sorted(verified_reporters.DOCUMENT_KINDS)
        }
        request_status = str(item.get("status") or "")
        review_state = str(item.get("review_state") or "")
        if request_status == "pending" and review_state == "needs_info":
            notification_action = "verified_reporter_supplement_requested"
            notification_marker = "user_notified=false"
        elif request_status in {"verified", "rejected"}:
            notification_action = "verified_reporter_decided"
            notification_marker = "notification_sent=false"
        else:
            notification_action = ""
            notification_marker = ""
        last_delivery = next(
            (
                event
                for event in activity
                if event.get("action") == notification_action
            ),
            None,
        )
        last_retry = next(
            (
                event
                for event in activity
                if event.get("action") == "verified_reporter_notification_retried"
            ),
            None,
        )
        retry_succeeded_after_failure = bool(
            last_delivery
            and last_retry
            and int(last_retry["id"]) > int(last_delivery["id"])
            and "notification_sent=true"
            in str(last_retry.get("details") or "").lower()
        )
        item["notification_retryable"] = bool(
            last_delivery
            and notification_marker in str(last_delivery.get("details") or "").lower()
            and not retry_succeeded_after_failure
        )
        return JSONResponse(
            {
                "ok": True,
                "item": item,
                "supplements": history,
                "versions": versions,
                "activity": activity,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/api/admin/verified-reporters/{request_id}/retry-notification"
    )
    async def verified_reporter_retry_notification(
        request_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        item = await db.verification_request(request_id)
        if not item:
            return bad("verification_request_not_found", 404)
        activity = await db.verification_audit_history(request_id)
        status = str(item.get("status") or "")
        review_state = str(item.get("review_state") or "")
        if status == "pending" and review_state == "needs_info":
            expected_action = "verified_reporter_supplement_requested"
            failed_marker = "user_notified=false"
        elif status in {"verified", "rejected"}:
            expected_action = "verified_reporter_decided"
            failed_marker = "notification_sent=false"
        else:
            return bad("verification_notification_not_retryable", 409)

        last_delivery = next(
            (event for event in activity if event.get("action") == expected_action),
            None,
        )
        last_retry = next(
            (
                event
                for event in activity
                if event.get("action") == "verified_reporter_notification_retried"
            ),
            None,
        )
        retry_succeeded_after_failure = bool(
            last_delivery
            and last_retry
            and int(last_retry["id"]) > int(last_delivery["id"])
            and "notification_sent=true"
            in str(last_retry.get("details") or "").lower()
        )
        if (
            last_delivery is None
            or failed_marker not in str(last_delivery.get("details") or "").lower()
            or retry_succeeded_after_failure
        ):
            return bad("verification_notification_not_failed", 409)

        if expected_action == "verified_reporter_supplement_requested":
            history = await db.verification_supplement_history(request_id)
            if not history or history[-1].get("response_at") is not None:
                return bad("verification_notification_not_retryable", 409)
            supplement = history[-1]
            notified = await _notify_user_verification_supplement(
                int(item["user_id"]),
                supplement["requested_documents"],
                str(supplement["message"]),
            )
        else:
            notified = await _notify_verification_decision(
                int(item["user_id"]),
                status,
                (
                    str(item["rejection_reason"])
                    if item.get("rejection_reason")
                    else None
                ),
            )
        await db.audit(
            _admin_actor(request),
            "verified_reporter_notification_retried",
            str(request_id),
            f"notification_sent={notified}; action={expected_action}",
        )
        return JSONResponse(
            {"ok": True, "notification_sent": notified},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/admin/verified-reporters/{request_id}/supplement")
    async def verified_reporter_supplement(
        request_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            body = await request.json()
        except Exception:
            return bad("bad_json")
        requested_documents = (
            body.get("requested_documents") if isinstance(body, dict) else None
        )
        message = body.get("message") if isinstance(body, dict) else None
        if (
            not isinstance(requested_documents, list)
            or any(not isinstance(kind, str) for kind in requested_documents)
            or not isinstance(message, str)
        ):
            return bad("bad_supplement_request")
        db = db_of()
        try:
            result = await db.request_verification_supplement(
                request_id,
                _admin_actor(request),
                requested_documents,
                message,
            )
        except ValueError as exc:
            return bad(str(exc))
        if not result:
            return bad("verification_request_not_pending_or_documents_missing", 409)

        notification_created = False
        try:
            await db.create_notification(
                "verification_supplement_requested",
                {
                    "request_id": request_id,
                    "user_id": result["user_id"],
                    "dashboard_path": f"/verification/{request_id}",
                },
            )
            notification_created = True
        except Exception:
            logger.exception(
                "Could not create dashboard notification for verification supplement"
            )
        user_notified = await _notify_user_verification_supplement(
            int(result["user_id"]),
            result["requested_documents"],
            result["message"],
        )
        await db.audit(
            _admin_actor(request),
            "verified_reporter_supplement_requested",
            str(request_id),
            "requested_documents="
            + ",".join(result["requested_documents"])
            + f"; notification_created={notification_created}; "
            + f"user_notified={user_notified}",
        )
        return JSONResponse(
            {
                "ok": True,
                "notification_created": notification_created,
                "user_notified": user_notified,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        "/api/admin/verified-reporters/{request_id}/documents/{kind}"
    )
    async def verified_reporter_document(
        request_id: int, kind: str, request: Request
    ) -> Response:
        refuse = _auth(request)
        if refuse:
            return refuse
        if kind not in verified_reporters.DOCUMENT_KINDS:
            return bad("not_found", 404)
        version_value = request.query_params.get("version_id")
        if version_value is None:
            ciphertext = await db_of().verification_document(request_id, kind)
            operation = "viewed"
        else:
            try:
                version_id = int(version_value)
            except ValueError:
                return bad("bad_query")
            ciphertext = await db_of().verification_document_version(
                request_id, kind, version_id
            )
            operation = (
                "downloaded"
                if request.query_params.get("download") == "true"
                else "viewed"
            )
        if not ciphertext:
            return bad("document_not_available", 404)
        try:
            content = verified_reporters.decrypt_document(ciphertext)
        except verified_reporters.VerificationUnavailable:
            logger.exception(
                "Unable to decrypt verification document request %s",
                request_id,
            )
            return bad("document_unavailable", 503)
        await db_of().audit(
            _admin_actor(request),
            f"verified_reporter_document_{operation}",
            str(request_id),
            f"kind={kind}; version_id={version_value or 'current'}",
        )
        return Response(
            content=content,
            media_type=verified_reporters.image_media_type(content)
            or "application/octet-stream",
            headers={
                "Cache-Control": "no-store, private, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    @app.get("/api/admin/verified-reporters/{request_id}/pdf")
    async def verified_reporter_pdf(
        request_id: int, request: Request
    ) -> Response:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        item = await db.verification_request(request_id)
        if not item:
            return bad("verification_request_not_found", 404)
        encrypted_phone = await db.verification_phone(request_id)
        if not encrypted_phone:
            return bad("document_not_available", 404)
        documents = {}
        try:
            phone = verified_reporters.decrypt(encrypted_phone).decode("utf-8")
            for kind in verified_reporters.DOCUMENT_KINDS:
                encrypted = await db.verification_document(request_id, kind)
                if not encrypted:
                    return bad("document_not_available", 404)
                documents[kind] = verified_reporters.decrypt_document(encrypted)

            from io import BytesIO  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415

            import arabic_reshaper  # noqa: PLC0415
            from bidi.algorithm import get_display  # noqa: PLC0415
            from reportlab.lib.pagesizes import A4  # noqa: PLC0415
            from reportlab.lib.utils import ImageReader  # noqa: PLC0415
            from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
            from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415
            from reportlab.pdfgen import canvas  # noqa: PLC0415

            font_path = (
                Path(__file__).resolve().parents[1]
                / "admin_web"
                / "assets"
                / "fonts"
                / "NotoSansArabic-Regular.ttf"
            )
            pdfmetrics.registerFont(TTFont("IdentityArabic", str(font_path)))
            output = BytesIO()
            page_width, page_height = A4
            pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
            pdf.setTitle("Verified reporter identity documents")

            def draw_rtl(text: str, x: float, y: float, size: int = 12) -> None:
                shaped = get_display(arabic_reshaper.reshape(text))
                pdf.setFont("IdentityArabic", size)
                pdf.drawRightString(x, y, shaped)

            draw_rtl("ملف توثيق المراسل", page_width - 44, page_height - 56, 20)
            draw_rtl(f"رقم المستخدم: {item['user_id']}", page_width - 44, page_height - 100)
            profile = await db.user_profile(int(item["user_id"]))
            full_name = str((profile or {}).get("full_name") or "غير متوفر")
            username = str((profile or {}).get("username") or "غير متوفر")
            draw_rtl(f"الاسم: {full_name}", page_width - 44, page_height - 128)
            draw_rtl(f"اسم المستخدم: @{username.lstrip('@')}", page_width - 44, page_height - 156)
            draw_rtl(f"رقم الهاتف: {phone}", page_width - 44, page_height - 184)
            draw_rtl(
                f"تاريخ التقديم: {item.get('submitted_at') or 'غير متوفر'}",
                page_width - 44,
                page_height - 212,
            )
            draw_rtl(
                f"الحالة: {item.get('status') or 'غير متوفر'}",
                page_width - 44,
                page_height - 240,
            )
            pdf.showPage()

            labels = {
                "selfie": "صورة الوجه",
                "id_front": "الهوية - الوجه الأمامي",
                "id_back": "الهوية - الوجه الخلفي",
            }
            for kind in ("selfie", "id_front", "id_back"):
                draw_rtl(labels[kind], page_width - 44, page_height - 48, 16)
                image = ImageReader(BytesIO(documents[kind]))
                image_width, image_height = image.getSize()
                scale = min(
                    (page_width - 88) / image_width,
                    (page_height - 112) / image_height,
                )
                width, height = image_width * scale, image_height * scale
                pdf.drawImage(
                    image,
                    (page_width - width) / 2,
                    (page_height - height) / 2 - 12,
                    width=width,
                    height=height,
                    preserveAspectRatio=True,
                    anchor="c",
                )
                pdf.showPage()
            pdf.save()
            pdf_bytes = output.getvalue()
        except verified_reporters.VerificationUnavailable:
            logger.exception(
                "Unable to decrypt verification PDF request %s", request_id
            )
            return bad("document_unavailable", 503)
        except (ImportError, OSError):
            logger.exception("Verification PDF dependencies or font unavailable")
            return bad("pdf_generation_unavailable", 503)
        except Exception:
            logger.exception("Unable to generate verification PDF")
            return bad("pdf_generation_failed", 500)

        await db.audit(
            _admin_actor(request),
            "verified_reporter_pdf_downloaded",
            str(request_id),
            "تم تنزيل PDF لوثائق الهوية",
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Cache-Control": "no-store, private, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": (
                    f'attachment; filename="verified-reporter-{request_id}.pdf"'
                ),
            },
        )

    @app.post("/api/admin/verified-reporters/{request_id}/decision")
    async def verified_reporter_decision(
        request_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            body = await request.json()
        except Exception:
            return bad("bad_json")
        decision = body.get("decision") if isinstance(body, dict) else None
        if decision not in {"verified", "rejected"}:
            return bad("bad_decision")
        rejection_reason = (
            body.get("rejection_reason")
            if isinstance(body, dict) and decision == "rejected"
            else None
        )
        if decision == "rejected" and (
            not isinstance(rejection_reason, str)
            or not rejection_reason.strip()
            or len(rejection_reason.strip()) > 1000
        ):
            return bad("verification_rejection_reason_required")
        try:
            changed = await db_of().decide_verification(
                request_id,
                _admin_actor(request),
                decision,
                rejection_reason,
            )
        except ValueError as exc:
            if str(exc) in {
                "verification_rejection_reason_required",
                "verification_rejection_reason_too_long",
            }:
                return bad(str(exc))
            raise
        if not changed:
            return bad("request_not_pending_or_documents_missing", 409)
        item = await db_of().verification_request(request_id)
        notified = await _notify_verification_decision(
            int(item["user_id"]) if item else 0,
            decision,
            rejection_reason.strip() if isinstance(rejection_reason, str) else None,
        ) if item else False
        await db_of().audit(
            _admin_actor(request),
            "verified_reporter_decided",
            str(request_id),
            f"decision={decision}; notification_sent={notified}",
        )
        return JSONResponse(
            {"ok": True, "decision": decision, "notification_sent": notified},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/admin/verified-reporters/{request_id}/delete")
    async def verified_reporter_delete(
        request_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        permissions = set(
            getattr(request.state, "admin_permissions", [])
        )
        if "identity_delete" not in permissions:
            return bad("forbidden", 403)
        try:
            body = await request.json()
        except Exception:
            return bad("bad_json")
        reason = body.get("reason") if isinstance(body, dict) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            return bad("deletion_reason_required")
        deleted = await db_of().delete_verified_reporter(
            request_id,
            _admin_actor(request),
            reason.strip(),
        )
        if not deleted:
            return bad("verified_reporter_not_found_or_documents_not_deletable", 409)
        return JSONResponse({"ok": True})

