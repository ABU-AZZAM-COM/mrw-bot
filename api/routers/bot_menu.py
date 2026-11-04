"""مسارات قوائم البوت وأزراره وأوامره وإعداداته العامة.

انتقلت من ``api/routers/bot_config.py``.
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

