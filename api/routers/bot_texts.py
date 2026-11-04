"""مسارات إدارة نصوص البوت: القراءة والتحديث.

انتقلت من ``api/admin_api.py`` مع الحفاظ على نفس مفاتيح الكتالوج ومنطق
التحقق، وتستقبل مساعدات ``admin_api`` المشتركة صراحةً.
"""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse

from bot import keyboards, texts


def register(app, *, db_of, bad, auth, json_body, admin_actor, owner_only) -> None:
    # مساعدات admin_api المشتركة: تُربط هنا كأسماء محلية داخل register حتى
    # تستخدمها المعالجات دون استيراد دائري عند تحميل الوحدة.
    from api import admin_api as core

    # أغلفة تربط الأسماء وقت الطلب لا وقت التركيب: تبقى قابلة للاستبدال
    # في الاختبارات عبر patch على admin_api، وتمنع الاستيراد الدائري.
    def _bot_runtime(*args, **kwargs):
        return core._bot_runtime(*args, **kwargs)

    def _on_bot_loop_value(*args, **kwargs):
        return core._on_bot_loop_value(*args, **kwargs)

    def _apply_bot_text_update(*args, **kwargs):
        return core._apply_bot_text_update(*args, **kwargs)

    _owner_only = owner_only

    @app.get("/admin_api/texts")
    async def get_texts(request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse: return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        saved = {}
        for item in await db.get_all_bot_texts():
            key = str(item.get("key") or "")
            if not texts.is_catalog_key(key):
                continue
            saved[key] = item
        profile_raw = await db.get_state("bot_profile")
        try:
            profile = json.loads(profile_raw) if profile_raw else {}
        except (TypeError, ValueError):
            profile = {}
        if not isinstance(profile, dict):
            profile = {}
        menu_raw = await db.get_state(keyboards.USER_MENU_STATE_KEY)
        menu_layout = keyboards.normalize_user_menu_layout(menu_raw)
        if menu_layout is None:
            menu_layout = keyboards.default_user_menu_layout()
        menu_labels = {
            str(button.get("action")): str(button.get("label") or "")
            for row in menu_layout
            for button in row
            if button.get("type", "callback") == "callback"
        }
        reply_raw = await db.get_state(control.REPLY_STATE_KEY)
        reply_keyboards = control.normalize_reply_keyboards(reply_raw)
        if reply_keyboards is None:
            reply_keyboards = control.default_reply_keyboards()
        reply_labels = {
            "CANCEL_LABEL": "cancel",
            "CONFIRM_LABEL": "confirm",
            "APP_LABEL": "web_app",
            "SKIP_LABEL": "skip",
            "BACK_LABEL": "back",
            "LOCATION_LABEL": "location",
            "SHARE_CONTACT_LABEL": "contact",
        }
        active_reply_labels = {}
        for name, button_type in reply_labels.items():
            active_reply_labels[name] = next(
                (
                    str(button.get("text") or "")
                    for config_item in reply_keyboards.values()
                    for row in config_item.get("rows", [])
                    for button in row
                    if button.get("type") == button_type
                ),
                getattr(control, name),
            )
        active_placeholders = {
            "INPUT_PLACEHOLDER": ("cancel",),
            "CHOOSE_BUTTON_PLACEHOLDER": ("confirm", "contact", "location"),
            "APP_INPUT_PLACEHOLDER": ("app",),
            "VERIFICATION_CONTACT_PLACEHOLDER": ("verification_contact",),
        }
        for name, keyboard_ids in active_placeholders.items():
            active_reply_labels[name] = next(
                (
                    str(reply_keyboards[keyboard_id].get("placeholder") or "")
                    for keyboard_id in keyboard_ids
                    if reply_keyboards.get(keyboard_id, {}).get("placeholder")
                ),
                getattr(control, name),
            )
        catalog = []
        for default in texts.catalog_defaults():
            key = default["key"]
            item = saved.get(key, {})
            content = item.get("content") or default["content"]
            if key == "welcome_message":
                content = str(profile.get("welcome_text") or content)
            elif key.startswith("bot.keyboards.USER_MENU_ACTIONS."):
                action = key.rsplit(".", 1)[-1]
                content = menu_labels.get(action, content)
            elif key.startswith("bot.control."):
                name = key.rsplit(".", 1)[-1]
                content = active_reply_labels.get(name, content)
            catalog.append({
                **default,
                **item,
                "content": content,
                "kind": (
                    "button"
                    if ".USER_MENU_ACTIONS." in key
                    or ".REPORT_CATEGORIES." in key
                    or ".SOCIAL_PLATFORM_LABELS." in key
                    or key == "bot.keyboards.APP_BUTTON_TEXT"
                    or key.startswith("bot.keyboards.FILTER_LABELS.")
                    or key.startswith("bot.keyboards.STATUS_LABELS.")
                    or key.startswith("bot.KEYBOARD_LABELS.")
                    or key.startswith("bot.ADMIN_KEYBOARD_LABELS.")
                    or (
                        key.startswith("bot.control.")
                        and key.rsplit(".", 1)[-1]
                        not in {
                            "INPUT_PLACEHOLDER",
                            "CHOOSE_BUTTON_PLACEHOLDER",
                            "APP_INPUT_PLACEHOLDER",
                            "VERIFICATION_CONTACT_PLACEHOLDER",
                        }
                    )
                    else "message"
                ),
            })
        catalog.sort(key=lambda item: str(item.get("key") or ""))
        return JSONResponse({"ok": True, "texts": catalog})

    @app.post("/admin_api/texts/update")
    async def update_text(request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse: return refuse
        from bot import control  # noqa: PLC0415

        body = await json_body(request)
        key = str(body.get("key") or "").strip()
        content = str(body.get("content") or "")
        label = str(body.get("label") or "").strip()
        category = str(body.get("category") or "user_facing").strip()
        if not key: return bad("missing_key")
        if not texts.is_catalog_key(key):
            return bad("unknown_text_key", 404)
        menu_action_prefix = "bot.keyboards.USER_MENU_ACTIONS."
        reply_label_prefix = "bot.control."
        edits_active_button = key.startswith(menu_action_prefix) or key.startswith(
            reply_label_prefix
        )
        if edits_active_button:
            refuse = _owner_only(request)
            if refuse:
                return refuse
        reset_to_default = not content
        if reset_to_default:
            default_content = texts.default_catalog_text(key)
            if default_content is None:
                return bad("unknown_text_key", 404)
            content = default_content
        db = db_of()
        old_content = await db.get_bot_text(key)
        ok = await db.update_bot_text(key, content, category)
        if not ok:
            import sqlite3
            conn = sqlite3.connect(db.db_path)
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO bot_texts (key,label,content,category,updated_at) VALUES (?,?,?,?,?)",
                (key, label, content, category, __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
            conn.commit(); conn.close(); ok = True
        if ok:
            if _bot_runtime()[0] is not None:
                applied = await _on_bot_loop_value(
                    lambda: _apply_bot_text_update(key, content)
                )
            else:
                applied = texts.apply_database_text(key, content)
            if applied and key == "welcome_message":
                profile_raw = await db.get_state("bot_profile")
                try:
                    profile = json.loads(profile_raw) if profile_raw else {}
                except (TypeError, ValueError):
                    profile = {}
                if isinstance(profile, dict) and profile.get("welcome_text"):
                    profile.pop("welcome_text", None)
                    await db.set_state(
                        "bot_profile",
                        json.dumps(profile, ensure_ascii=False, sort_keys=True),
                    )
            elif applied and key.startswith(menu_action_prefix):
                action = key[len(menu_action_prefix):]
                raw_layout = await db.get_state(keyboards.USER_MENU_STATE_KEY)
                layout = keyboards.normalize_user_menu_layout(raw_layout)
                if layout is not None:
                    found = False
                    for row in layout:
                        for button in row:
                            if (
                                button.get("type", "callback") == "callback"
                                and button.get("action") == action
                            ):
                                button["label"] = content
                                found = True
                    if found:
                        await db.set_state(
                            keyboards.USER_MENU_STATE_KEY,
                            json.dumps(layout, ensure_ascii=False),
                        )
                        keyboards.set_user_menu_layout(layout)
                    else:
                        applied = False
            elif applied and key.startswith(reply_label_prefix):
                name = key[len(reply_label_prefix):]
                button_types = {
                    "CANCEL_LABEL": {"cancel"},
                    "CONFIRM_LABEL": {"confirm"},
                    "APP_LABEL": {"web_app"},
                    "SKIP_LABEL": {"skip"},
                    "BACK_LABEL": {"back"},
                    "LOCATION_LABEL": {"location"},
                    "SHARE_CONTACT_LABEL": {"contact"},
                }
                placeholder_ids = {
                    "INPUT_PLACEHOLDER": {"cancel"},
                    "CHOOSE_BUTTON_PLACEHOLDER": {"confirm", "contact", "location"},
                    "APP_INPUT_PLACEHOLDER": {"app"},
                    "VERIFICATION_CONTACT_PLACEHOLDER": {"verification_contact"},
                }
                raw_layout = await db.get_state(control.REPLY_STATE_KEY)
                layout = control.normalize_reply_keyboards(raw_layout)
                if layout is None:
                    layout = control.default_reply_keyboards()
                if name in button_types:
                    for config_item in layout.values():
                        for row in config_item.get("rows", []):
                            for button in row:
                                if button.get("type") in button_types[name]:
                                    button["text"] = content
                elif name in placeholder_ids:
                    for keyboard_id in placeholder_ids[name]:
                        if keyboard_id in layout:
                            layout[keyboard_id]["placeholder"] = content
                else:
                    applied = False
                if applied:
                    await db.set_state(
                        control.REPLY_STATE_KEY,
                        json.dumps(layout, ensure_ascii=False),
                    )
                    control.set_reply_keyboards(layout)
            await db.audit(admin_actor(request), "bot_text_updated", key, f"category={category}")
        else:
            applied = False
        if not ok or not applied:
            rollback_content = old_content or texts.default_catalog_text(key)
            if rollback_content is not None:
                await db.update_bot_text(key, rollback_content, category)
                if _bot_runtime()[0] is not None:
                    await _on_bot_loop_value(
                        lambda: _apply_bot_text_update(key, rollback_content)
                    )
                else:
                    texts.apply_database_text(key, rollback_content)
            return JSONResponse({
                "ok": False,
                "saved": False,
                "applied": False,
                "error": "text_not_applied_to_active_bot",
                "key": key,
            }, status_code=503)
        return JSONResponse({
            "ok": True,
            "saved": True,
            "key": key,
            "applied": True,
            "reset_to_default": reset_to_default,
        })

    # ------------------------------------------------------------
    # 3) المستخدمون: قائمة + بطاقة + حجب/رفع حجب
    # ------------------------------------------------------------
