"""مسارات إعدادات البوت: الأزرار، الأوامر، القوائم، الملف التعريفي، والإحصاءات.

انتقلت من ``api/admin_api.py``. تتطلب صلاحية ``operate`` يفرضها ``_auth``
مركزيًا، وتستقبل مساعدات ``admin_api`` المشتركة عند التركيب.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from api.admin_auth import _admin_actor, _auth, _owner_only
from bot import keyboards, texts
from services import word_filter


def _bot_menu_actions() -> list[dict[str, str]]:
    return [
        {"id": action, "label": label}
        for action, label in keyboards.USER_MENU_ACTIONS.items()
    ]


def _bot_menu_limits() -> dict[str, int]:
    return {
        "max_rows": keyboards.MAX_USER_MENU_ROWS,
        "max_row_buttons": keyboards.MAX_USER_MENU_ROW_BUTTONS,
        "max_buttons": keyboards.MAX_USER_MENU_BUTTONS,
        "max_label": keyboards.MAX_USER_MENU_LABEL,
        "max_url": keyboards.MAX_USER_MENU_URL,
        "max_copy": keyboards.MAX_USER_MENU_COPY,
    }


def _bot_menu_options() -> dict[str, list[str]]:
    from bot import control

    return {
        "styles": list(control.STYLE_VALUES),
        "types": list(keyboards.MENU_BUTTON_TYPES),
        "visibility": list(keyboards.MENU_VISIBILITY),
    }


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``admin_api`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from api import admin_api as core

        return getattr(core, name)(*args, **kwargs)

    return call


def register(app, *, db_of, bad) -> None:
    logger = logging.getLogger(__name__)

    from api import admin_api as core

    MAX_TEXT = core.MAX_TEXT

    _bot_runtime = _late_bound("_bot_runtime")
    _json = _late_bound("_json")
    _main_module = _late_bound("_main_module")
    _on_bot_loop_value = _late_bound("_on_bot_loop_value")
    _set_telegram_bot_profile = _late_bound("_set_telegram_bot_profile")
    @app.get("/api/admin/bot-buttons")
    async def bot_buttons_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(keyboards.USER_MENU_STATE_KEY)
        layout = keyboards.normalize_user_menu_layout(raw)
        return JSONResponse({
            "ok": True,
            "items": layout or keyboards.default_user_menu_layout(),
            "defaults": keyboards.default_user_menu_layout(),
            "customized": layout is not None,
            "actions": _bot_menu_actions(),
            "limits": _bot_menu_limits(),
            "options": _bot_menu_options(),
        })

    @app.put("/api/admin/bot-buttons")
    async def bot_buttons_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        body = await _json(request)
        layout = keyboards.normalize_user_menu_layout(body.get("items"))
        if layout is None:
            return bad("bad_bot_menu_layout")
        db = db_of()
        await db.set_state(
            keyboards.USER_MENU_STATE_KEY,
            json.dumps(layout, ensure_ascii=False),
        )
        keyboards.set_user_menu_layout(layout)
        await db.audit(
            _admin_actor(request),
            "bot_menu_changed",
            "أزرار قائمة المستخدم",
            f"{len(layout)} صفوف — {sum(len(row) for row in layout)} أزرار",
        )
        return JSONResponse({
            "ok": True,
            "items": layout,
            "actions": _bot_menu_actions(),
            "limits": _bot_menu_limits(),
            "customized": True,
        })

    @app.delete("/api/admin/bot-buttons")
    async def bot_buttons_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        db = db_of()
        await db.set_state(keyboards.USER_MENU_STATE_KEY, None)
        keyboards.set_user_menu_layout(None)
        await db.audit(
            _admin_actor(request),
            "bot_menu_reset",
            "أزرار قائمة المستخدم",
            "العودة إلى التخطيط الافتراضي",
        )
        return JSONResponse({
            "ok": True,
            "items": keyboards.default_user_menu_layout(),
            "actions": _bot_menu_actions(),
            "limits": _bot_menu_limits(),
            "customized": False,
        })

    # ------------------------------------------------------------------
    # مركز التحكم الكامل: لوحات الرد، الأوامر، زر القائمة، الإعدادات
    # ------------------------------------------------------------------
    @app.get("/api/admin/bot-reply-keyboards")
    async def bot_reply_keyboards_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.REPLY_STATE_KEY)
        items = control.normalize_reply_keyboards(raw)
        bot, _ = _bot_runtime()
        return JSONResponse({
            "ok": True,
            "items": items or control.default_reply_keyboards(),
            "defaults": control.default_reply_keyboards(),
            "ids": control.reply_keyboard_ids(),
            "customized": items is not None,
            "mini_app_url": control.mini_app_url(),
            "bot_ready": bot is not None,
            "types": list(control.REPLY_BUTTON_TYPES),
            "styles": list(control.STYLE_VALUES),
        })

    @app.put("/api/admin/bot-reply-keyboards")
    async def bot_reply_keyboards_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        items = control.normalize_reply_keyboards(body.get("items"))
        if items is None:
            return bad("bad_bot_reply_keyboards")
        db = db_of()
        await db.set_state(
            control.REPLY_STATE_KEY, json.dumps(items, ensure_ascii=False)
        )
        control.set_reply_keyboards(items)
        await db.audit(
            _admin_actor(request),
            "bot_reply_keyboards_changed",
            "لوحات رد البوت",
            f"{len(items)} لوحات",
        )
        return JSONResponse({
            "ok": True,
            "items": items,
            "ids": control.reply_keyboard_ids(),
            "customized": True,
            "types": list(control.REPLY_BUTTON_TYPES),
            "styles": list(control.STYLE_VALUES),
        })

    @app.delete("/api/admin/bot-reply-keyboards")
    async def bot_reply_keyboards_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        await db.set_state(control.REPLY_STATE_KEY, None)
        control.set_reply_keyboards(None)
        await db.audit(
            _admin_actor(request),
            "bot_reply_keyboards_reset",
            "لوحات رد البوت",
            "العودة إلى الافتراضي",
        )
        return JSONResponse({
            "ok": True,
            "items": control.default_reply_keyboards(),
            "ids": control.reply_keyboard_ids(),
            "customized": False,
            "types": list(control.REPLY_BUTTON_TYPES),
            "styles": list(control.STYLE_VALUES),
        })

    @app.get("/api/admin/bot-commands")
    async def bot_commands_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.COMMANDS_STATE_KEY)
        cfg = control.normalize_commands(raw)
        bot, _ = _bot_runtime()
        return JSONResponse({
            "ok": True,
            "items": cfg or control.default_commands_config(),
            "defaults": control.default_commands_config(),
            "customized": cfg is not None,
            "scopes": [
                {"id": key, "label": label}
                for key, label in control._SCOPE_LABELS.items()
            ],
            "bot_ready": bot is not None,
            "limits": {
                "max_commands": control.MAX_COMMANDS,
                "max_scopes": control.MAX_SCOPES,
            },
        })

    @app.put("/api/admin/bot-commands")
    async def bot_commands_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        new_cfg = control.normalize_commands(body.get("items", body))
        if new_cfg is None:
            return bad("bad_bot_commands")
        db = db_of()
        old_raw = await db.get_state(control.COMMANDS_STATE_KEY)
        old_cfg = control.normalize_commands(old_raw) or control.default_commands_config()
        await db.set_state(
            control.COMMANDS_STATE_KEY, json.dumps(new_cfg, ensure_ascii=False)
        )
        control.set_commands_config(new_cfg)

        applied: dict[str, Any] = {"ok": False, "errors": ["bot_not_ready"]}
        bot, _ = _bot_runtime()
        if bot is not None:
            removed = control.removed_scopes(old_cfg, new_cfg)

            async def _push() -> dict[str, Any]:
                result = await control.apply_commands(bot, new_cfg)
                for item in removed:
                    await control.delete_commands_scope(bot, item)
                return result

            result = await _on_bot_loop_value(_push)
            if isinstance(result, dict):
                applied = result
        await db.audit(
            _admin_actor(request),
            "bot_commands_changed",
            "أوامر البوت",
            f"{len(new_cfg['scopes'])} نطاق — applied={applied.get('ok')}",
        )
        return JSONResponse({
            "ok": True,
            "items": new_cfg,
            "customized": True,
            "applied": applied,
            "bot_ready": bot is not None,
        })

    @app.get("/api/admin/bot-menu-button")
    async def bot_menu_button_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.MENU_BUTTON_STATE_KEY)
        cfg = control.normalize_menu_button(raw)
        bot, _ = _bot_runtime()
        return JSONResponse({
            "ok": True,
            "items": cfg or control.default_menu_button(),
            "defaults": control.default_menu_button(),
            "customized": cfg is not None,
            "bot_ready": bot is not None,
            "mini_app_url": control.mini_app_url(),
        })

    @app.put("/api/admin/bot-menu-button")
    async def bot_menu_button_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        cfg = control.normalize_menu_button(body.get("items", body))
        if cfg is None:
            return bad("bad_bot_menu_button")
        db = db_of()
        await db.set_state(
            control.MENU_BUTTON_STATE_KEY, json.dumps(cfg, ensure_ascii=False)
        )
        control.set_menu_button_config(cfg)
        applied = False
        bot, _ = _bot_runtime()
        if bot is not None:
            result = await _on_bot_loop_value(
                lambda: control.apply_menu_button(bot, cfg)
            )
            applied = bot is not None
        await db.audit(
            _admin_actor(request),
            "bot_menu_button_changed",
            "زر قائمة البوت",
            cfg.get("type", ""),
        )
        return JSONResponse({
            "ok": True,
            "items": cfg,
            "customized": True,
            "applied": applied,
            "bot_ready": bot is not None,
        })

    @app.delete("/api/admin/bot-menu-button")
    async def bot_menu_button_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        await db.set_state(control.MENU_BUTTON_STATE_KEY, None)
        cfg = control.set_menu_button_config(None)
        bot, _ = _bot_runtime()
        if bot is not None:
            await _on_bot_loop_value(lambda: control.apply_menu_button(bot, cfg))
        await db.audit(
            _admin_actor(request),
            "bot_menu_button_reset",
            "زر قائمة البوت",
            "العودة إلى الافتراضي",
        )
        return JSONResponse({
            "ok": True,
            "items": cfg,
            "customized": False,
            "applied": bot is not None,
            "bot_ready": bot is not None,
        })

    @app.get("/api/admin/bot-settings")
    async def bot_settings_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.SETTINGS_STATE_KEY)
        cfg = control.normalize_settings(raw)
        values = cfg or control.default_settings()
        bot, _ = _bot_runtime()
        info: dict[str, Any] = {
            "username": "",
            "supports_inline_queries": False,
            "can_join_groups": False,
            "can_read_all_group_messages": False,
        }
        if bot is not None:
            async def _me():
                me = await bot.get_me()
                return {
                    "username": me.username or "",
                    "supports_inline_queries": bool(me.supports_inline_queries),
                    "can_join_groups": bool(me.can_join_groups),
                    "can_read_all_group_messages": bool(
                        me.can_read_all_group_messages
                    ),
                }

            remote = await _on_bot_loop_value(_me)
            if isinstance(remote, dict):
                info = remote
        return JSONResponse({
            "ok": True,
            "items": values,
            "defaults": control.default_settings(),
            "customized": cfg is not None,
            "bot_ready": bot is not None,
            "bot": info,
            "rights_keys": list(control._RIGHTS_KEYS),
        })

    @app.put("/api/admin/bot-settings")
    async def bot_settings_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        cfg = control.normalize_settings(body.get("items", body))
        if cfg is None:
            return bad("bad_bot_settings")
        db = db_of()
        await db.set_state(
            control.SETTINGS_STATE_KEY, json.dumps(cfg, ensure_ascii=False)
        )
        control.set_settings(cfg)
        applied = False
        bot, _ = _bot_runtime()
        if bot is not None:
            result = await _on_bot_loop_value(
                lambda: control.apply_default_admin_rights(bot, cfg)
            )
            applied = bot is not None
        await db.audit(
            _admin_actor(request),
            "bot_settings_changed",
            "إعدادات البوت",
            f"mini_app={'yes' if cfg['mini_app_url'] else 'no'}; "
            f"rights_customized={cfg['default_admin_rights_customized']}",
        )
        return JSONResponse({
            "ok": True,
            "items": cfg,
            "customized": True,
            "applied": applied,
            "bot_ready": bot is not None,
        })

    @app.get("/api/admin/bot-analytics")
    async def bot_analytics_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        rows = await db_of().button_stats()
        labels = dict(keyboards.USER_MENU_ACTIONS)
        for action, label in {
            "menu": "القائمة الرئيسية",
            "home": "العودة للقائمة",
            "category": "اختيار التصنيف",
            "r": "فتح بلاغ",
            "back": "رجوع",
            "verify-consent": "الموافقة على التوثيق",
            "verify-supplement": "استكمال التوثيق",
            "report": "تقديم بلاغ",
            "status": "متابعة بلاغاتي",
            "about": "دليل الاستخدام",
            "contact": "التواصل مع الإدارة",
            "privacy": "سياسة الخصوصية",
            "verify": "التسجيل كمراسل",
            "verify-status": "حالة التوثيق",
            "cancel": "إلغاء",
            "confirm": "تأكيد الإرسال",
        }.items():
            labels.setdefault(action, label)
        items = [
            {
                "action": row["action"],
                "label": labels.get(row["action"], row["action"]),
                "presses": row["presses"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return JSONResponse({
            "ok": True,
            "items": items,
            "total": sum(item["presses"] for item in items),
            "tracked_actions": len(items),
        })

    @app.delete("/api/admin/bot-analytics")
    async def bot_analytics_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        await db_of().reset_button_stats()
        await db_of().audit(
            _admin_actor(request),
            "bot_analytics_reset",
            "تحليلات أزرار البوت",
            "تصفير العدّادات",
        )
        return JSONResponse({"ok": True, "items": [], "total": 0})

    @app.get("/api/admin/bot-links")
    async def bot_links_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        payload = control.sanitize_payload(request.query_params.get("payload", ""))
        username = ""
        bot, _ = _bot_runtime()
        if bot is not None:
            async def _me():
                me = await bot.get_me()
                return me.username or ""

            remote = await _on_bot_loop_value(_me)
            if isinstance(remote, str):
                username = remote
        return JSONResponse({
            "ok": True,
            "payload": payload,
            "username": username,
            "links": control.deep_links(username, payload),
            "mini_app_url": control.mini_app_url(),
        })

    @app.post("/api/admin/bot-apply")
    async def bot_apply_all(request: Request) -> JSONResponse:
        """إعادة دفع كل إعدادات البوت إلى تيليجرام (بعد إعادة تشغيل مثلًا)."""
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        bot, _ = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        cfg_commands = control.current_commands_config()
        cfg_menu = control.current_menu_button_config()
        cfg_settings = control.current_settings()

        async def _push() -> dict[str, Any]:
            commands = await control.apply_commands(bot, cfg_commands)
            menu_error = None
            try:
                await control.apply_menu_button(bot, cfg_menu)
            except Exception as exc:  # noqa: BLE001
                menu_error = str(exc)
            rights_error = None
            try:
                await control.apply_default_admin_rights(bot, cfg_settings)
            except Exception as exc:  # noqa: BLE001
                rights_error = str(exc)
            return {
                "ok": commands.get("ok") and not menu_error and not rights_error,
                "command_errors": commands.get("errors", []),
                "menu_error": menu_error,
                "rights_error": rights_error,
            }

        result = await _on_bot_loop_value(_push)
        await db_of().audit(
            _admin_actor(request),
            "bot_apply_all",
            "إعادة تطبيق إعدادات البوت",
            str(result),
        )
        return JSONResponse({"ok": True, "result": result or {"ok": False}})

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

