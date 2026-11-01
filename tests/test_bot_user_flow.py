import asyncio
import hashlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from bot import admin, handlers, keyboards
from services import (
    guards,
    identity_image_checks,
    sightengine_moderation,
    verified_reporters,
)


class CommandAccessTests(unittest.TestCase):
    def test_end_user_commands_are_available_and_admin_commands_stay_restricted(self):
        public_commands = ("start", "report", "cancel", "app", "help", "status", "reply")
        for command in public_commands:
            with self.subTest(command=command):
                self.assertTrue(
                    guards.is_public_command_allowed(987654, f"/{command}@mrw_bot")
                )
        self.assertFalse(guards.is_public_command_allowed(987654, "/admin"))
        self.assertFalse(guards.is_public_command_allowed(987654, "/panic"))
        with patch.object(guards.config, "ADMIN_IDS", {987654}):
            self.assertTrue(guards.is_public_command_allowed(987654, "/admin"))


class UserPromptCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_phone_back_returns_to_main_menu(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            chat_id=42,
            message_id=8,
            from_user=SimpleNamespace(id=123),
            reply_text=AsyncMock(
                return_value=SimpleNamespace(chat_id=42, message_id=9)
            ),
        )
        context = SimpleNamespace(
            user_data={
                "verification_flow": True,
                "verification_consent_at": "consented",
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 7,
                    "keyboard_kind": "reply",
                },
            },
            bot=SimpleNamespace(
                delete_message=AsyncMock(),
                send_message=AsyncMock(),
                edit_message_reply_markup=AsyncMock(),
            ),
            bot_data={"db": object()},
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_message=message,
            callback_query=None,
        )
        menu = InlineKeyboardMarkup([[keyboards._cb("البلاغات", "usr:report")]])

        with (
            patch.object(handlers, "main_menu_kb", return_value=menu),
            patch.object(
                handlers,
                "_main_menu_content",
                new=AsyncMock(return_value=("نص القائمة الرئيسية", None)),
            ),
        ):
            state = await handlers.verification_phone_back(update, context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertNotIn("verification_flow", context.user_data)
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "نص القائمة الرئيسية",
        )
        self.assertIsInstance(
            message.reply_text.await_args.kwargs["reply_markup"],
            ReplyKeyboardRemove,
        )
        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=42, message_id=9, reply_markup=menu
        )
        self.assertCountEqual(
            context.bot.delete_message.await_args_list,
            [
                call(chat_id=42, message_id=7),
                call(chat_id=42, message_id=8),
            ],
        )

    async def test_invalid_user_callback_is_answered_without_crashing(self):
        query = SimpleNamespace(
            data="usr:",
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(
            bot_data={"db": object()},
            user_data={},
        )
        update = SimpleNamespace(callback_query=query)

        state = await handlers.on_user_callback(update, context)

        self.assertEqual(state, ConversationHandler.END)
        query.answer.assert_awaited_once_with(
            handlers.texts.UNKNOWN_BUTTON,
            show_alert=True,
        )

    async def test_invalid_category_callback_is_answered_once_and_shows_recovery(self):
        query = SimpleNamespace(
            data="usr:category:stale",
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(chat_id=42, message_id=8),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            bot_data={"db": SimpleNamespace(record_button_press=AsyncMock())},
            user_data={},
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=123),
        )

        state = await handlers.on_user_callback(update, context)

        self.assertEqual(state, ConversationHandler.END)
        query.answer.assert_awaited_once_with()
        query.edit_message_text.assert_awaited_once_with(
            handlers.texts.INLINE_MESSAGES["unknown_category"],
            reply_markup=handlers.back_to_menu_kb(),
        )

    async def test_invalid_admin_list_callback_is_answered_only_once(self):
        query = SimpleNamespace(
            data="adm:list",
            message=SimpleNamespace(photo=None),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            bot_data={"db": object()},
            chat_data={},
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=123),
        )

        with patch.object(admin, "_is_admin", return_value=True):
            await admin.on_callback(update, context)

        query.answer.assert_awaited_once_with()
        query.edit_message_text.assert_awaited_once()

    async def test_reply_keyboard_transition_falls_back_to_new_menu_message(self):
        context = SimpleNamespace(
            user_data={
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 7,
                    "keyboard_kind": "reply",
                }
            },
            bot=SimpleNamespace(
                delete_message=AsyncMock(),
                send_message=AsyncMock(
                    return_value=SimpleNamespace(chat_id=42, message_id=9)
                ),
                edit_message_reply_markup=AsyncMock(
                    side_effect=handlers.TelegramError("cannot edit")
                ),
            ),
        )
        message = SimpleNamespace(
            chat_id=42,
            reply_text=AsyncMock(
                return_value=SimpleNamespace(chat_id=42, message_id=8)
            ),
        )
        update = SimpleNamespace(callback_query=None, effective_message=message)
        markup = InlineKeyboardMarkup([[keyboards._cb("Home", "usr:menu")]])

        await handlers._send_clean_prompt(
            update, context, "Main menu", reply_markup=markup
        )

        self.assertEqual(
            context.bot.send_message.await_args.kwargs["text"],
            "Main menu",
        )
        self.assertEqual(
            context.user_data[handlers.ACTIVE_PROMPT_KEY]["message_id"],
            9,
        )
        context.bot.delete_message.assert_any_await(chat_id=42, message_id=8)

    async def test_back_button_restores_previous_step_without_losing_draft(self):
        message = SimpleNamespace(chat_id=42, message_id=8)
        query = SimpleNamespace(
            data="usr:back:description",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(return_value=message),
        )
        context = SimpleNamespace(
            user_data={
                "report_state": handlers.ASK_PHOTO,
                "report_category": "conduct",
                "description": "Saved report description",
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 8,
                    "keyboard_kind": "inline",
                },
            },
            bot_data={"db": object()},
        )
        update = SimpleNamespace(callback_query=query)

        state = await handlers.on_user_callback(update, context)

        self.assertEqual(state, handlers.ASK_DESC)
        self.assertEqual(context.user_data["description"], "Saved report description")
        self.assertEqual(context.user_data["report_state"], handlers.ASK_DESC)
        self.assertEqual(
            query.edit_message_text.await_args.kwargs["reply_markup"]
            .inline_keyboard[0][0]
            .callback_data,
            "usr:back:category",
        )

    async def test_replaces_reply_keyboard_and_removes_previous_prompt(self):
        context = SimpleNamespace(
            user_data={
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 7,
                    "keyboard_kind": "reply",
                }
            },
            bot=SimpleNamespace(
                delete_message=AsyncMock(),
                send_message=AsyncMock(
                    return_value=SimpleNamespace(chat_id=42, message_id=8)
                ),
                edit_message_reply_markup=AsyncMock(),
            ),
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(
                return_value=SimpleNamespace(chat_id=42, message_id=8)
            )
        )
        update = SimpleNamespace(callback_query=None, effective_message=message)
        markup = InlineKeyboardMarkup(
            [[keyboards._cb("Back", "usr:menu")]]
        )

        await handlers._send_clean_prompt(
            update, context, "Next step", reply_markup=markup
        )

        context.bot.delete_message.assert_awaited_once_with(
            chat_id=42, message_id=7
        )
        sent_markup = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIsInstance(sent_markup, ReplyKeyboardRemove)
        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=42, message_id=8, reply_markup=markup
        )
        self.assertEqual(
            context.user_data[handlers.ACTIVE_PROMPT_KEY]["message_id"], 8
        )
        self.assertEqual(
            context.user_data[handlers.ACTIVE_PROMPT_KEY]["keyboard_kind"],
            "inline",
        )

    async def test_failed_delete_removes_old_inline_keyboard(self):
        context = SimpleNamespace(
            user_data={
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 7,
                    "keyboard_kind": "inline",
                }
            },
            bot=SimpleNamespace(
                delete_message=AsyncMock(side_effect=handlers.TelegramError("denied")),
                edit_message_reply_markup=AsyncMock(),
            ),
        )

        await handlers._clear_user_prompt(context)

        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=42, message_id=7, reply_markup=None
        )
        self.assertNotIn(handlers.ACTIVE_PROMPT_KEY, context.user_data)

    async def test_main_menu_places_primary_actions_first(self):
        with (
            patch.object(keyboards, "_user_menu_layout", None),
            patch.object(keyboards.verified_reporters, "is_configured", return_value=True),
        ):
            rows = keyboards.main_menu_kb().inline_keyboard

        self.assertEqual(rows[0][0].callback_data, "usr:report")
        self.assertEqual(rows[1][0].callback_data, "usr:verify")
        self.assertEqual(rows[2][0].callback_data, "usr:verify-status")
        self.assertEqual(rows[3][0].callback_data, "usr:status")
        self.assertEqual(rows[4][0].callback_data, "usr:about")
        self.assertEqual(rows[4][1].callback_data, "usr:contact")

    async def test_main_menu_shows_verification_when_encryption_is_ready(self):
        with (
            patch.object(keyboards, "_user_menu_layout", None),
            patch.object(verified_reporters, "is_configured", return_value=True),
        ):
            rows = keyboards.main_menu_kb().inline_keyboard

        self.assertEqual(rows[1][0].callback_data, "usr:verify")
        self.assertEqual(rows[2][0].callback_data, "usr:verify-status")

    async def test_confirmation_keeps_final_receipt_navigation_separate(self):
        receipt = keyboards.saved_receipt_kb()

        self.assertEqual(
            receipt.inline_keyboard[0][0].callback_data, "usr:home"
        )
        self.assertIsInstance(keyboards.contact_request_kb(), ReplyKeyboardMarkup)
        self.assertIsInstance(keyboards.location_request_kb(), ReplyKeyboardMarkup)


class VerificationConversationTests(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, *, as_document=False):
        consent_version = hashlib.sha256(
            (handlers.texts.VERIFICATION_CONSENT_DEFAULT + "\n").encode("utf-8")
        ).hexdigest()[:24]
        document = SimpleNamespace(
            file_id="synthetic-image",
            file_size=20,
        )
        photo = SimpleNamespace(file_id="synthetic-image", file_size=20)
        reply_message = SimpleNamespace(chat_id=42, message_id=8)
        message = SimpleNamespace(
            document=document if as_document else None,
            photo=[] if as_document else [photo],
            reply_text=AsyncMock(return_value=reply_message),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type="private"),
            callback_query=None,
        )
        telegram_file = SimpleNamespace(
            download_as_bytearray=AsyncMock(
                side_effect=lambda buffer: buffer.extend(b"synthetic-image-bytes")
            )
        )
        database = SimpleNamespace(
            extend_verification_ticket=AsyncMock(return_value=True),
            submit_verification_documents=AsyncMock(return_value=42),
            cancel_verification_capture=AsyncMock(return_value=True),
            get_state=AsyncMock(return_value=None),
        )
        context = SimpleNamespace(
            user_data={
                handlers.VERIFICATION_TICKET_KEY: "ticket-hash",
                handlers.VERIFICATION_CAPTURES_KEY: {},
                handlers.VERIFICATION_STEP_KEY: "selfie",
                "verification_consent_version": consent_version,
            },
            bot_data={"db": database},
            bot=SimpleNamespace(
                get_file=AsyncMock(return_value=telegram_file),
                send_chat_action=AsyncMock(),
            ),
        )
        return update, context, database

    async def test_accepts_gallery_photo_and_advances_after_screening_face(self):
        update, context, database = self._make_update()

        async def inspect_image_with_delay(path):
            await asyncio.sleep(0.01)
            return sightengine_moderation.NudityScores(())

        with (
            patch.object(
                identity_image_checks, "validate_identity_image"
            ) as validate_image,
            patch.object(sightengine_moderation, "credentials", return_value=("u", "s")),
            patch.object(
                sightengine_moderation,
                "inspect_image",
                new=AsyncMock(side_effect=inspect_image_with_delay),
            ) as inspect_image,
            patch.object(
                verified_reporters,
                "encrypt_document",
                return_value=b"encrypted-selfie",
            ),
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_ID_FRONT)
        self.assertEqual(
            context.user_data[handlers.VERIFICATION_CAPTURES_KEY]["selfie"],
            b"encrypted-selfie",
        )
        validate_image.assert_called_once_with(
            "selfie", b"synthetic-image-bytes"
        )
        inspect_image.assert_awaited_once()
        inspected_path = inspect_image.await_args.args[0]
        self.assertFalse(inspected_path.exists())
        context.bot.send_chat_action.assert_awaited()
        database.extend_verification_ticket.assert_awaited_once()
        database.submit_verification_documents.assert_not_awaited()

    async def test_rejects_file_upload_instead_of_gallery_photo(self):
        update, context, database = self._make_update(as_document=True)
        with patch.object(identity_image_checks, "validate_identity_image") as validate:
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_SELFIE)
        self.assertIn("كملف", update.effective_message.reply_text.await_args.args[0])
        validate.assert_not_called()
        database.extend_verification_ticket.assert_not_awaited()

    async def test_requires_new_consent_before_processing_images(self):
        update, context, database = self._make_update()
        with patch.object(
            handlers,
            "_verification_consent",
            new=AsyncMock(return_value=("updated disclosure", "new-consent-version")),
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, ConversationHandler.END)
        database.cancel_verification_capture.assert_awaited_once_with(
            123, "ticket-hash"
        )
        context.bot.get_file.assert_not_awaited()
        self.assertIn("تم تحديث إفصاح الخصوصية", update.effective_message.reply_text.await_args.args[0])

    async def test_rejects_selfie_flagged_by_sightengine_without_saving(self):
        update, context, database = self._make_update()
        flagged_scores = sightengine_moderation.NudityScores(
            (("sexual_activity", 0.9),)
        )
        with (
            patch.object(identity_image_checks, "validate_identity_image"),
            patch.object(sightengine_moderation, "credentials", return_value=("u", "s")),
            patch.object(
                sightengine_moderation,
                "inspect_image",
                new=AsyncMock(return_value=flagged_scores),
            ),
            patch.object(verified_reporters, "encrypt_document") as encrypt,
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_SELFIE)
        self.assertEqual(
            update.effective_message.reply_text.await_args.args[0],
            handlers._verification_image_error_message("inappropriate_content"),
        )
        encrypt.assert_not_called()
        database.extend_verification_ticket.assert_not_awaited()

    async def test_does_not_save_selfie_when_sightengine_is_unconfigured(self):
        update, context, database = self._make_update()
        with (
            patch.object(identity_image_checks, "validate_identity_image"),
            patch.object(sightengine_moderation, "credentials", return_value=None),
            patch.object(sightengine_moderation, "inspect_image") as inspect_image,
            patch.object(verified_reporters, "encrypt_document") as encrypt,
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_SELFIE)
        self.assertIn("تعذّر فحص صورة الوجه", update.effective_message.reply_text.await_args.args[0])
        inspect_image.assert_not_awaited()
        encrypt.assert_not_called()
        database.extend_verification_ticket.assert_not_awaited()

    async def test_rejects_unclear_image_and_keeps_current_step(self):
        update, context, database = self._make_update()
        with (
            patch.object(
                identity_image_checks,
                "validate_identity_image",
                side_effect=identity_image_checks.IdentityImageCheckError(
                    "image_blurry"
                ),
            ),
            patch.object(verified_reporters, "encrypt_document") as encrypt,
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_SELFIE)
        self.assertEqual(
            context.user_data[handlers.VERIFICATION_CAPTURES_KEY], {}
        )
        encrypt.assert_not_called()
        database.extend_verification_ticket.assert_not_awaited()

    async def test_final_document_submits_encrypted_set_for_human_review(self):
        update, context, database = self._make_update()
        context.user_data[handlers.VERIFICATION_STEP_KEY] = "id_back"
        context.user_data[handlers.VERIFICATION_CAPTURES_KEY] = {
            "selfie": b"encrypted-selfie",
            "id_front": b"encrypted-front",
        }
        with (
            patch.object(identity_image_checks, "validate_identity_image"),
            patch.object(sightengine_moderation, "inspect_image") as inspect_image,
            patch.object(
                verified_reporters,
                "encrypt_document",
                return_value=b"encrypted-back",
            ),
        ):
            state = await handlers.receive_verification_id_back(update, context)

        self.assertEqual(state, ConversationHandler.END)
        database.submit_verification_documents.assert_awaited_once_with(
            123,
            "ticket-hash",
            {
                "selfie": b"encrypted-selfie",
                "id_front": b"encrypted-front",
                "id_back": b"encrypted-back",
            },
        )
        self.assertNotIn(handlers.VERIFICATION_CAPTURES_KEY, context.user_data)
        inspect_image.assert_not_awaited()

    async def test_status_button_does_not_cancel_an_in_progress_capture(self):
        message = SimpleNamespace(chat_id=42, message_id=8)
        query = SimpleNamespace(
            data="usr:verify-status",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(return_value=message),
        )
        database = SimpleNamespace(
            verification_status=AsyncMock(
                return_value={"status": "capturing", "has_documents": 0}
            ),
            cancel_verification_capture=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                handlers.VERIFICATION_TICKET_KEY: "ticket-hash",
                handlers.VERIFICATION_CAPTURES_KEY: {"selfie": b"encrypted"},
                handlers.VERIFICATION_STEP_KEY: "id_front",
            },
            bot_data={"db": database},
        )

        state = await handlers.on_user_callback(
            SimpleNamespace(callback_query=query), context
        )

        self.assertEqual(state, handlers.ASK_VERIFICATION_ID_FRONT)
        self.assertIn(handlers.VERIFICATION_TICKET_KEY, context.user_data)
        database.cancel_verification_capture.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
