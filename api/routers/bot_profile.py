"""مسارات الملف التعريفي للبوت: النصوص، الكلمات المحجوبة، صورة الترحيب.

انتقلت من ``api/routers/bot_config.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from api.admin_auth import _admin_actor, _auth, _owner_only
from bot import keyboards, texts
from services import word_filter

logger = logging.getLogger(__name__)


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``admin_api`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from api import admin_api as core

        return getattr(core, name)(*args, **kwargs)

    return call


_bot_runtime = _late_bound("_bot_runtime")
_main_module = _late_bound("_main_module")
_on_bot_loop_value = _late_bound("_on_bot_loop_value")
_set_telegram_bot_profile = _late_bound("_set_telegram_bot_profile")
_json = _late_bound("_json")


def register(app, *, db_of, bad) -> None:
    @app.get("/api/admin/settings")
    async def runtime_settings_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from app import config  # noqa: PLC0415

        saved = await db_of().get_runtime_settings()
        values = {
            "MAX_DESC_LEN": config.MAX_DESC_LEN,
            "MAX_PHOTO_MB": config.MAX_PHOTO_MB,
            "MAX_REPORTS_PER_DAY": config.MAX_REPORTS_PER_DAY,
            "MAX_REPORTS_PER_MINUTE": config.MAX_REPORTS_PER_MINUTE,
            "MAX_REPORTS_PER_HOUR": config.MAX_REPORTS_PER_HOUR,
            "MAX_REPORTS_PER_HOUR_GLOBAL": config.MAX_REPORTS_PER_HOUR_GLOBAL,
            "MAX_REPORTS_PER_DAY_GLOBAL": config.MAX_REPORTS_PER_DAY_GLOBAL,
            "FREEZE_AFTER_STRIKES": config.FREEZE_AFTER_STRIKES,
            "FREEZE_MINUTES_FIRST": config.FREEZE_MINUTES_FIRST,
            "FREEZE_MINUTES_SECOND": config.FREEZE_MINUTES_SECOND,
            "FREEZE_MINUTES_REPEAT": config.FREEZE_MINUTES_REPEAT,
        }
        for key in values:
            candidate = saved.get(key)
            minimum, maximum = config.RUNTIME_SETTING_LIMITS[key]
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and minimum <= candidate <= maximum
            ):
                values[key] = candidate
        import guards  # noqa: PLC0415
        db = db_of()

        return JSONResponse({
            "ok": True,
            "items": {
                "max_description_length": values["MAX_DESC_LEN"],
                "max_photo_mb": values["MAX_PHOTO_MB"],
                "max_reports_per_day": values["MAX_REPORTS_PER_DAY"],
                "max_reports_per_minute": values["MAX_REPORTS_PER_MINUTE"],
                "max_reports_per_hour": values["MAX_REPORTS_PER_HOUR"],
                "max_reports_per_hour_global": values["MAX_REPORTS_PER_HOUR_GLOBAL"],
                "max_reports_per_day_global": values["MAX_REPORTS_PER_DAY_GLOBAL"],
                "freeze_after_strikes": values["FREEZE_AFTER_STRIKES"],
                "freeze_minutes_first": values["FREEZE_MINUTES_FIRST"],
                "freeze_minutes_second": values["FREEZE_MINUTES_SECOND"],
                "freeze_minutes_repeat": values["FREEZE_MINUTES_REPEAT"],
                "auto_freeze_paused_until": await guards.auto_freeze_pause_until(db),
                "user_limits_paused_until": await guards.user_limits_pause_until(db),
                "block_messages": await guards.get_block_messages(db),
            },
        })

    @app.get("/api/admin/bot-profile")
    async def bot_profile_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        raw = await db.get_state("bot_profile")
        try:
            saved = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        values = {
            "name": str(saved.get("name") or ""),
            "short_description": str(saved.get("short_description") or ""),
            "description": str(saved.get("description") or ""),
            "welcome_text": str(
                saved.get("welcome_text") or texts.BOT_PROFILE_DEFAULT_WELCOME
            ),
            "privacy_policy_url": str(saved.get("privacy_policy_url") or ""),
            "verification_consent_text": str(
                saved.get("verification_consent_text") or ""
            ),
            "photo_updated_at": str(saved.get("photo_updated_at") or ""),
            "has_welcome_photo": await db.has_welcome_photo(),
        }
        bot, _ = _bot_runtime()
        if bot is not None:
            async def _load_remote_profile():
                remote = {}
                me = await bot.get_me()
                remote["username"] = me.username or ""
                for key, method_name, attribute in (
                    ("name", "get_my_name", "name"),
                    ("short_description", "get_my_short_description", "short_description"),
                    ("description", "get_my_description", "description"),
                ):
                    method = getattr(bot, method_name, None)
                    if method is not None:
                        result = await method()
                        remote[key] = getattr(result, attribute, "") or ""
                return remote

            remote = await _on_bot_loop_value(_load_remote_profile)
            if isinstance(remote, dict):
                for key in ("name", "short_description", "description"):
                    if key in remote:
                        values[key] = remote[key]
                values["username"] = remote.get("username", "")
        return JSONResponse({
            "ok": True,
            "items": values,
            "bot_ready": bot is not None,
            "can_edit": getattr(getattr(request, "state", None), "admin_role", "") == "owner",
        })

    @app.get("/api/admin/bot-profile/blocked-words")
    async def blocked_words_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        raw = await db_of().get_state("support_blocklist")
        try:
            saved = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            logger.exception("تعذرت قراءة قائمة الكلمات المحظورة")
            return bad("wordlist_unavailable", 503)
        if not isinstance(saved, dict) or not isinstance(saved.get("words", []), list):
            return bad("wordlist_unavailable", 503)
        words = saved.get("words", [])
        if any(not isinstance(word, str) for word in words):
            return bad("wordlist_unavailable", 503)
        return JSONResponse({
            "ok": True,
            "count": len(words),
            "content": "\n".join(words),
            "updated_at": saved.get("updated_at"),
        })

    @app.post("/api/admin/bot-profile/blocked-words")
    async def blocked_words_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        try:
            form = await request.form()
        except Exception:
            return bad("multipart_unavailable", 503)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return bad("wordlist_file_required")
        filename = str(getattr(upload, "filename", "") or "")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:120]
        if not filename.lower().endswith(".txt"):
            return bad("wordlist_must_be_txt")
        content = await upload.read(word_filter.MAX_WORD_LIST_BYTES + 1)
        try:
            terms = word_filter.parse_word_list(content)
        except ValueError as exc:
            return bad(str(exc))
        value = {
            "words": terms,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        await db_of().set_state(
            "support_blocklist",
            json.dumps(value, ensure_ascii=False, sort_keys=True),
        )
        await db_of().audit(
            _admin_actor(request),
            "support_blocklist_replaced",
            "قائمة الكلمات المحظورة",
            f"count={len(terms)}; filename={filename}",
        )
        return JSONResponse({"ok": True, "count": len(terms)})

    @app.delete("/api/admin/bot-profile/blocked-words")
    async def blocked_words_delete(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        await db_of().set_state(
            "support_blocklist",
            json.dumps(
                {"words": [], "updated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        await db_of().audit(
            _admin_actor(request),
            "support_blocklist_cleared",
            "قائمة الكلمات المحظورة",
            "تم تفريغ القائمة يدويًا",
        )
        return JSONResponse({"ok": True, "count": 0})

    @app.get("/api/admin/bot-profile/welcome-photo")
    async def welcome_photo_get(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        photo = await db_of().get_welcome_photo()
        if photo is None:
            return bad("welcome_photo_not_found", 404)
        return Response(
            content=photo["data"],
            media_type=photo["content_type"],
            headers={"Cache-Control": "private, no-store"},
        )

    @app.post("/api/admin/bot-profile/welcome-photo")
    async def welcome_photo_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة صورة الترحيب")
            return bad("multipart_unavailable", 503)
        upload = form.get("media")
        if upload is None or not hasattr(upload, "read"):
            return bad("welcome_photo_required")
        photo_bytes = await upload.read(10 * 1024 * 1024 + 1)
        if len(photo_bytes) > 10 * 1024 * 1024:
            return bad("welcome_photo_too_large", 413)
        if photo_bytes.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
            extension = ".jpg"
        elif photo_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
            extension = ".png"
        else:
            return bad("welcome_photo_must_be_jpeg_or_png")
        filename = str(getattr(upload, "filename", "") or "")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:120]
        if not filename:
            filename = f"welcome{extension}"

        db = db_of()
        await db.set_welcome_photo(photo_bytes, content_type, filename)
        bot_main = _main_module()
        app = getattr(bot_main, "app", None)
        loop = getattr(app, "bot_data", {}).get("loop") if app else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(
                app.bot_data.__setitem__, "welcome_photo", {
                    "data": photo_bytes,
                    "content_type": content_type,
                    "filename": filename,
                }
            )
        await db.audit(
            _admin_actor(request),
            "welcome_photo_changed",
            "هوية البوت",
            f"تحديث صورة الترحيب ({content_type})",
        )
        return JSONResponse({
            "ok": True,
            "has_welcome_photo": True,
            "filename": filename,
        })

    @app.delete("/api/admin/bot-profile/welcome-photo")
    async def welcome_photo_delete(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        deleted = await db.delete_welcome_photo()
        bot_main = _main_module()
        app = getattr(bot_main, "app", None)
        loop = getattr(app, "bot_data", {}).get("loop") if app else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(
                app.bot_data.__setitem__, "welcome_photo", None
            )
        if deleted:
            await db.audit(
                _admin_actor(request),
                "welcome_photo_removed",
                "هوية البوت",
                "إزالة صورة الترحيب",
            )
        return JSONResponse({"ok": True, "has_welcome_photo": False})

    @app.post("/api/admin/bot-profile")
    async def bot_profile_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        bot, _ = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة إعدادات هوية البوت")
            return bad("multipart_unavailable", 503)

        name = str(form.get("name") or "").strip()
        short_description = str(form.get("short_description") or "").strip()
        description = str(form.get("description") or "").strip()
        welcome_text = str(form.get("welcome_text") or "").strip()
        privacy_policy_url = str(form.get("privacy_policy_url") or "").strip()
        verification_consent_text = str(
            form.get("verification_consent_text") or ""
        ).strip()
        if not name or len(name) > 64:
            return bad("bad_bot_name")
        if len(short_description) > 120:
            return bad("short_description_too_long")
        if len(description) > 512:
            return bad("description_too_long")
        if not welcome_text or len(welcome_text) > 3500:
            return bad("bad_welcome_text")
        if len(verification_consent_text) > 2500:
            return bad("bad_verification_consent_text")
        if privacy_policy_url:
            parsed_policy_url = urllib.parse.urlsplit(privacy_policy_url)
            if (
                len(privacy_policy_url) > 500
                or parsed_policy_url.scheme != "https"
                or not parsed_policy_url.netloc
                or parsed_policy_url.username
                or parsed_policy_url.password
            ):
                return bad("bad_privacy_policy_url")

        upload = form.get("photo") or form.get("media")
        photo_bytes = None
        if upload is not None and hasattr(upload, "read"):
            photo_bytes = await upload.read(10 * 1024 * 1024 + 1)
            if len(photo_bytes) > 10 * 1024 * 1024:
                return bad("profile_photo_too_large", 413)
            if photo_bytes and not photo_bytes.startswith(b"\xff\xd8\xff"):
                return bad("profile_photo_must_be_jpeg")
            if not photo_bytes:
                photo_bytes = None

        db = db_of()
        previous_raw = await db.get_state("bot_profile")
        try:
            previous = json.loads(previous_raw) if previous_raw else {}
        except (TypeError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}

        requested = {
            "name": name,
            "short_description": short_description,
            "description": description,
        }
        applied = await _on_bot_loop_value(
            lambda: _set_telegram_bot_profile(bot, requested, photo_bytes)
        )
        if not isinstance(applied, dict) or not applied.get("ok"):
            error = applied.get("error") if isinstance(applied, dict) else None
            return bad(error or "telegram_profile_update_failed", 502)

        values = {
            "name": name,
            "short_description": short_description,
            "description": description,
            "welcome_text": welcome_text,
            "privacy_policy_url": privacy_policy_url,
            "verification_consent_text": verification_consent_text,
            "photo_updated_at": (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
                if photo_bytes else str(previous.get("photo_updated_at") or "")
            ),
        }
        consent_text_changed = (
            previous.get("verification_consent_text")
            or texts.VERIFICATION_CONSENT_DEFAULT
        ) != verification_consent_text
        await db.set_state(
            "bot_profile", json.dumps(values, ensure_ascii=False, sort_keys=True)
        )
        await db.audit(
            _admin_actor(request),
            "bot_profile_changed",
            "هوية البوت",
            "تحديث بيانات الحساب ورسالة البداية"
            + (" وتغيير الصورة" if photo_bytes else "")
            + (" وتحديث إفصاح توثيق المراسل" if consent_text_changed else ""),
        )
        return JSONResponse({"ok": True, "items": values})

    @app.put("/api/admin/settings")
    async def runtime_settings_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from app import config  # noqa: PLC0415

        body = await _json(request)
        aliases = {
            "max_description_length": "MAX_DESC_LEN",
            "max_photo_mb": "MAX_PHOTO_MB",
            "max_reports_per_day": "MAX_REPORTS_PER_DAY",
            "max_reports_per_minute": "MAX_REPORTS_PER_MINUTE",
            "max_reports_per_hour": "MAX_REPORTS_PER_HOUR",
            "max_reports_per_hour_global": "MAX_REPORTS_PER_HOUR_GLOBAL",
            "max_reports_per_day_global": "MAX_REPORTS_PER_DAY_GLOBAL",
            "freeze_after_strikes": "FREEZE_AFTER_STRIKES",
            "freeze_minutes_first": "FREEZE_MINUTES_FIRST",
            "freeze_minutes_second": "FREEZE_MINUTES_SECOND",
            "freeze_minutes_repeat": "FREEZE_MINUTES_REPEAT",
        }
        if not body or set(body) - set(aliases) - {"block_messages"}:
            return bad("bad_settings")
        db = db_of()
        current = await db.get_runtime_settings()
        clean = {}
        for public_key, config_key in aliases.items():
            raw = body.get(public_key, current.get(config_key, getattr(config, config_key)))
            if isinstance(raw, bool):
                return bad("bad_setting_value")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return bad("bad_setting_value")
            minimum, maximum = config.RUNTIME_SETTING_LIMITS[config_key]
            if not minimum <= value <= maximum:
                return bad("setting_out_of_range")
            clean[config_key] = value
        if not (
            clean["FREEZE_MINUTES_FIRST"]
            <= clean["FREEZE_MINUTES_SECOND"]
            <= clean["FREEZE_MINUTES_REPEAT"]
        ):
            return bad("freeze_duration_order")
        import guards  # noqa: PLC0415

        try:
            current_messages = guards.clean_block_messages(
                current.get("BLOCK_MESSAGES", texts.BLOCK_MESSAGES)
            )
            clean["BLOCK_MESSAGES"] = guards.clean_block_messages(
                {**current_messages, **body.get("block_messages", {})}
            )
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "bad_block_messages")

        await db.set_runtime_settings(clean)
        config.apply_runtime_settings(clean)
        await db.audit(
            _admin_actor(request), "runtime_settings_changed", "إعدادات التشغيل",
            ", ".join(
                [f"{key}={value}" for key, value in clean.items()
                 if key != "BLOCK_MESSAGES"]
                + (["تحديث رسائل الحظر"] if "block_messages" in body else [])
            ),
        )

        return JSONResponse({
            "ok": True,
            "items": {
                "max_description_length": clean["MAX_DESC_LEN"],
                "max_photo_mb": clean["MAX_PHOTO_MB"],
                "max_reports_per_day": clean["MAX_REPORTS_PER_DAY"],
                "max_reports_per_minute": clean["MAX_REPORTS_PER_MINUTE"],
                "max_reports_per_hour": clean["MAX_REPORTS_PER_HOUR"],
                "max_reports_per_hour_global": clean["MAX_REPORTS_PER_HOUR_GLOBAL"],
                "max_reports_per_day_global": clean["MAX_REPORTS_PER_DAY_GLOBAL"],
                "freeze_after_strikes": clean["FREEZE_AFTER_STRIKES"],
                "freeze_minutes_first": clean["FREEZE_MINUTES_FIRST"],
                "freeze_minutes_second": clean["FREEZE_MINUTES_SECOND"],
                "freeze_minutes_repeat": clean["FREEZE_MINUTES_REPEAT"],
                "auto_freeze_paused_until": await guards.auto_freeze_pause_until(db),
                "user_limits_paused_until": await guards.user_limits_pause_until(db),
                "block_messages": clean["BLOCK_MESSAGES"],
            },
        })

